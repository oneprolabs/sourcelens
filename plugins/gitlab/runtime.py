"""GitLab LensNode runtime entrypoint."""

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Annotated
from urllib.parse import quote, urlsplit, urlunsplit

from langchain.tools import ToolRuntime, tool
from pydantic import Field

from lensnode.plugin_runtime import PluginRuntimeError

PLUGIN_API_VERSION = 1
PLUGIN_KEY = "gitlab"
PLUGIN_VERSION = "1.0.0"
READ_MAX_BYTES = 200_000
SEARCH_MAX_BYTES = 1_000_000
ACTIVITY_DEFAULT_MAX_RESULTS = 50
ACTIVITY_MAX_RESULTS = 100
ACTIVITY_RESOURCE_MAX = {
    "commits": 50,
    "merge_requests": 20,
    "issues": 20,
}


def http_origins(endpoint):
    """Return the validated GitLab API origin for connection pooling."""

    return (_endpoint(endpoint),)


def build_tool(definition, executor):
    """Create a fixed GitLab read-only tool."""

    if (not isinstance(definition, dict)
            or definition.get("capability") != "repository.read"
            or definition.get("side_effect") != "none"):
        raise PluginRuntimeError("PLUGIN_TOOL_NOT_READ_ONLY")
    key = str(definition.get("key") or "")
    description = str(definition.get("description") or "").strip()
    if not description:
        raise PluginRuntimeError("PLUGIN_TOOL_DESCRIPTION_REQUIRED")
    if key == "gitlab_read_file":
        def invoke(project: Annotated[str, Field(min_length=3, max_length=255)],
                   path: Annotated[str, Field(min_length=1, max_length=4096)],
                   runtime: ToolRuntime,
                   ref: Annotated[str, Field(max_length=255)] = "") -> str:
            return executor(key, {"project": project, "path": path, "ref": ref}, runtime)
        return tool(key, description=description)(invoke)
    if key == "gitlab_search_code":
        def invoke(project: Annotated[str, Field(min_length=3, max_length=255)],
                   query: Annotated[str, Field(min_length=1, max_length=1024)],
                   runtime: ToolRuntime,
                   path: Annotated[str, Field(max_length=4096)] = "",
                   ref: Annotated[str, Field(max_length=255)] = "",
                   max_results: Annotated[int, Field(ge=1, le=20)] = 10) -> str:
            return executor(key, {"project": project, "query": query, "path": path,
                                  "ref": ref, "max_results": max_results}, runtime)
        return tool(key, description=description)(invoke)
    if key == "gitlab_activity_summary":
        def invoke(
            projects: Annotated[list[str], Field(min_length=1, max_length=50)],
            since: Annotated[str, Field(min_length=1, max_length=64)],
            until: Annotated[str, Field(min_length=1, max_length=64)],
            runtime: ToolRuntime,
            max_results: Annotated[int, Field(ge=1, le=100)] = 50,
        ) -> str:
            return executor(
                key,
                {
                    "projects": projects,
                    "since": since,
                    "until": until,
                    "max_results": max_results,
                },
                runtime,
            )

        return tool(key, description=description)(invoke)
    raise PluginRuntimeError("PLUGIN_TOOL_UNSUPPORTED")


def execute_tool(key, client, arguments, secret, endpoint, config):
    """Execute one bounded GitLab REST call."""

    endpoint = _endpoint(endpoint)
    if key == "gitlab_activity_summary":
        return _activity_summary(client, arguments, secret, endpoint, config)
    del config
    project = _text(arguments, "project")
    if key == "gitlab_read_file":
        path, ref = _text(arguments, "path"), str(arguments.get("ref") or "main")
        status, body, truncated = _get(client, f"{endpoint}/api/v4/projects/{quote(project, safe='')}/repository/files/{quote(path, safe='')}/raw", secret, {"ref": ref}, READ_MAX_BYTES, True)
        _status(status)
        if b"\x00" in body: raise PluginRuntimeError("GITLAB_FILE_NOT_TEXT")
        return {"ok": True, "project": project, "path": path, "ref": ref,
                "content": body.decode("utf-8", "replace"), "truncated": truncated}
    if key == "gitlab_search_code":
        query, path, ref = _text(arguments, "query"), str(arguments.get("path") or ""), str(arguments.get("ref") or "")
        max_results = arguments.get("max_results", 10)
        status, body, truncated = _get(client, f"{endpoint}/api/v4/projects/{quote(project, safe='')}/search", secret, {"scope": "blobs", "search": query, "per_page": max_results, "page": 1}, SEARCH_MAX_BYTES, False)
        _status(status)
        if truncated: raise PluginRuntimeError("GITLAB_RESPONSE_TOO_LARGE")
        try: payload = json.loads(body)
        except (UnicodeDecodeError, ValueError) as exc: raise PluginRuntimeError("GITLAB_RESPONSE_INVALID") from exc
        if not isinstance(payload, list): raise PluginRuntimeError("GITLAB_RESPONSE_INVALID")
        items = []
        for item in payload:
            if not isinstance(item, dict): continue
            item_path, item_ref = str(item.get("path") or "")[:4096], str(item.get("ref") or "")[:255]
            if (path and not item_path.startswith(f"{path.rstrip('/')}/")) or (ref and item_ref != ref): continue
            items.append({"name": str(item.get("filename") or "")[:255], "path": item_path, "ref": item_ref})
            if len(items) >= max_results: break
        return {"ok": True, "project": project, "query": query, "items": items}
    raise PluginRuntimeError("PLUGIN_TOOL_UNSUPPORTED")


def _activity_summary(client, arguments, secret, endpoint, config):
    """Return bounded project activity through fixed REST requests."""

    projects, since, until, max_results = _activity_arguments(
        arguments,
        config,
    )
    if not secret:
        raise PluginRuntimeError("PLUGIN_SNAPSHOT_MISMATCH")
    results = {
        project: {
            "project": project,
            "commits": [],
            "merge_requests": [],
            "issues": [],
            "possibly_truncated": {},
        }
        for project in projects
    }
    requests = [
        (project, resource)
        for project in projects
        for resource in ACTIVITY_RESOURCE_MAX
    ]
    successful_resources = 0
    with ThreadPoolExecutor(max_workers=min(8, len(requests))) as executor:
        futures = {
            executor.submit(
                _gitlab_activity_resource,
                client,
                endpoint,
                secret,
                project,
                resource,
                since,
                until,
                max_results,
            ): (project, resource)
            for project, resource in requests
        }
        for future in as_completed(futures):
            project, resource = futures[future]
            try:
                items, truncated = future.result()
            except PluginRuntimeError as exc:
                results[project].setdefault("errors", {})[resource] = str(exc)
                continue
            except Exception:
                results[project].setdefault("errors", {})[
                    resource
                ] = "GITLAB_REQUEST_FAILED"
                continue
            results[project][resource] = items
            results[project]["possibly_truncated"][resource] = truncated
            successful_resources += 1
    if not successful_resources:
        raise PluginRuntimeError("GITLAB_REQUEST_FAILED")
    return {
        "ok": True,
        "since": arguments["since"],
        "until": arguments["until"],
        "limits": {
            resource: min(max_results, limit)
            for resource, limit in ACTIVITY_RESOURCE_MAX.items()
        },
        "projects": [results[project] for project in projects],
    }


def _gitlab_activity_resource(
    client,
    endpoint,
    secret,
    project,
    resource,
    since,
    until,
    max_results,
):
    """Query and project one fixed GitLab activity resource."""

    page_size = min(max_results, ACTIVITY_RESOURCE_MAX[resource])
    encoded_project = quote(project, safe="")
    if resource == "commits":
        path = f"projects/{encoded_project}/repository/commits"
        params = {
            "since": since.isoformat().replace("+00:00", "Z"),
            "until": until.isoformat().replace("+00:00", "Z"),
            "per_page": page_size,
            "page": 1,
        }
        projector = _activity_commit
        timestamp_field = "committed_date"
    else:
        path = f"projects/{encoded_project}/{resource}"
        params = {
            "scope": "all",
            "state": "all",
            "order_by": "updated_at",
            "sort": "desc",
            "updated_after": since.isoformat().replace("+00:00", "Z"),
            "per_page": page_size,
            "page": 1,
        }
        projector = (
            _activity_merge_request
            if resource == "merge_requests"
            else _activity_issue
        )
        timestamp_field = "updated_at"
    status, body, truncated = _get(
        client,
        f"{endpoint}/api/v4/{path}",
        secret,
        params,
        SEARCH_MAX_BYTES,
        False,
    )
    _status(status)
    if truncated:
        raise PluginRuntimeError("GITLAB_RESPONSE_TOO_LARGE")
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, ValueError) as exc:
        raise PluginRuntimeError("GITLAB_RESPONSE_INVALID") from exc
    if not isinstance(payload, list):
        raise PluginRuntimeError("GITLAB_RESPONSE_INVALID")
    items = [
        projector(item)
        for item in payload
        if isinstance(item, dict)
        and _in_window(item.get(timestamp_field), since, until)
    ]
    return items, len(payload) >= page_size


def _activity_commit(value):
    """Project one compact GitLab commit."""

    return {
        "sha": str(value.get("id") or "")[:64],
        "title": str(value.get("title") or "")[:300],
        "author_name": str(value.get("author_name") or "")[:160],
        "authored_at": str(value.get("authored_date") or "")[:64],
        "committed_at": str(value.get("committed_date") or "")[:64],
        "url": str(value.get("web_url") or "")[:500],
    }


def _activity_merge_request(value):
    """Project one compact GitLab merge request."""

    item = _activity_work_item(value)
    item.update(
        {
            "draft": bool(value.get("draft")),
            "merged_at": str(value.get("merged_at") or "")[:64],
        }
    )
    return item


def _activity_issue(value):
    """Project one compact GitLab issue."""

    return _activity_work_item(value)


def _activity_work_item(value):
    """Project common untrusted GitLab work-item fields."""

    author = value.get("author")
    if not isinstance(author, dict):
        author = {}
    return {
        "number": value.get("iid") if isinstance(value.get("iid"), int) else 0,
        "title": str(value.get("title") or "")[:300],
        "description": str(value.get("description") or "")[:2000],
        "state": str(value.get("state") or "")[:32],
        "author": {
            "username": str(author.get("username") or "")[:160],
            "name": str(author.get("name") or "")[:160],
        },
        "created_at": str(value.get("created_at") or "")[:64],
        "updated_at": str(value.get("updated_at") or "")[:64],
        "closed_at": str(value.get("closed_at") or "")[:64],
        "url": str(value.get("web_url") or "")[:500],
    }


def _activity_arguments(arguments, config):
    """Validate the frozen time window and Connection project scope."""

    if not isinstance(arguments, dict) or not isinstance(config, dict):
        raise PluginRuntimeError("PLUGIN_ARGUMENTS_INVALID")
    projects = arguments.get("projects")
    if (
        not isinstance(projects, list)
        or not projects
        or len(projects) > 50
        or any(not isinstance(item, str) or not item for item in projects)
        or len({item.casefold() for item in projects}) != len(projects)
    ):
        raise PluginRuntimeError("PLUGIN_ARGUMENTS_INVALID")
    scope = config.get("__allowed_scope")
    allowed = scope.get("projects") if isinstance(scope, dict) else None
    if not isinstance(allowed, list):
        raise PluginRuntimeError("PLUGIN_SCOPE_MISMATCH")
    allowed_projects = {str(item).casefold() for item in allowed}
    if any(project.casefold() not in allowed_projects for project in projects):
        raise PluginRuntimeError("PLUGIN_SCOPE_MISMATCH")
    since = _timestamp(arguments.get("since"))
    until = _timestamp(arguments.get("until"))
    if since > until:
        raise PluginRuntimeError("PLUGIN_ARGUMENTS_INVALID")
    max_results = arguments.get(
        "max_results",
        ACTIVITY_DEFAULT_MAX_RESULTS,
    )
    if (
        isinstance(max_results, bool)
        or not isinstance(max_results, int)
        or not 1 <= max_results <= ACTIVITY_MAX_RESULTS
    ):
        raise PluginRuntimeError("PLUGIN_ARGUMENTS_INVALID")
    return list(projects), since, until, max_results


def _timestamp(value):
    """Parse an ISO-8601 timestamp into an aware UTC datetime."""

    if not isinstance(value, str) or not value or len(value) > 64:
        raise PluginRuntimeError("PLUGIN_ARGUMENTS_INVALID")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PluginRuntimeError("PLUGIN_ARGUMENTS_INVALID") from exc
    if parsed.tzinfo is None:
        raise PluginRuntimeError("PLUGIN_ARGUMENTS_INVALID")
    return parsed.astimezone(timezone.utc)


def _in_window(value, since, until):
    """Return whether one provider timestamp is inside the exact window."""

    try:
        timestamp = _timestamp(value)
    except PluginRuntimeError:
        return False
    return since <= timestamp <= until


def build_datasource_command(snapshot, material, trigger):
    """Build one Git datasource command from frozen GitLab state."""

    resolved = snapshot.get("resolved_config")
    if not isinstance(resolved, dict): raise PluginRuntimeError("PLUGIN_CONFIG_INVALID")
    endpoint = _endpoint(resolved.get("endpoint"))
    if not isinstance(material, dict) or material.get("plugin_key") != PLUGIN_KEY or str(material.get("endpoint") or "").rstrip("/") != endpoint or not material.get("value"):
        raise PluginRuntimeError("PLUGIN_MATERIAL_MISMATCH")
    datasource = resolved.get("datasource_config") or {}
    if not isinstance(datasource, dict):
        raise PluginRuntimeError("PLUGIN_CONFIG_INVALID")
    projects = datasource.get("projects")
    if projects is None:
        projects = [datasource.get("project")]
    if (
        not isinstance(projects, list)
        or not projects
        or any(not isinstance(item, str) or not item for item in projects)
    ):
        raise PluginRuntimeError("PLUGIN_CONFIG_INVALID")
    scope = resolved.get("connection_scope") or {}
    allowed = {
        str(item).casefold() for item in scope.get("projects", [])
    }
    if any(item.casefold() not in allowed for item in projects):
        raise PluginRuntimeError("PLUGIN_SCOPE_VIOLATION")
    config = {
        "branch": datasource.get("branch") or "",
        "directory": datasource.get("directory") or "",
        "auth_scheme": "token",
        "access_token": material["value"],
    }
    config["repositories"] = [
        {
            "repo_url": f"{endpoint}/{project}.git",
            "branch": datasource.get("branch") or "",
            "directory": datasource.get("directory") or "",
            "target_subdir": project,
            "enabled": True,
        }
        for project in projects
    ]
    return {
        "source_type": "git",
        "datasource_uuid": snapshot.get("datasource_uuid"),
        "target_path": resolved.get("target_path"),
        "sync_policy": resolved.get("sync_policy") or {},
        "trigger": trigger,
        "config": config,
    }


def _endpoint(value):
    parsed = urlsplit(str(value or "").strip())
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise PluginRuntimeError("PLUGIN_SNAPSHOT_MISMATCH")
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def _text(arguments, name):
    value = arguments.get(name)
    if not isinstance(value, str) or not value: raise PluginRuntimeError("PLUGIN_ARGUMENTS_INVALID")
    return value


def _get(client, url, token, params, max_bytes, truncate):
    with client.stream("GET", url, params=params, follow_redirects=False, headers={"Accept": "application/json", "PRIVATE-TOKEN": token, "User-Agent": "SourceLens-LensNode"}) as response:
        if response.is_redirect: raise PluginRuntimeError("GITLAB_REDIRECT_REJECTED")
        if response.status_code >= 400: return response.status_code, b"", False
        body = b"".join(response.iter_bytes())
    return response.status_code, body[:max_bytes], len(body) > max_bytes


def _status(status):
    if status < 400: return
    raise PluginRuntimeError({404: "GITLAB_NOT_FOUND", 401: "GITLAB_ACCESS_DENIED", 403: "GITLAB_ACCESS_DENIED", 429: "GITLAB_RATE_LIMITED"}.get(status, "GITLAB_REQUEST_FAILED"))
