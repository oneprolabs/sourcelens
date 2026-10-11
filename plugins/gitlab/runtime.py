"""GitLab LensNode runtime entrypoint."""

import configparser
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated
from urllib.parse import quote, urlsplit, urlunsplit

import httpx
from langchain.tools import ToolRuntime, tool
from pydantic import Field

from lensnode.plugin_runtime import PluginRuntimeError

PLUGIN_API_VERSION = 1
PLUGIN_KEY = "gitlab"
PLUGIN_VERSION = "1.0.0"
READ_MAX_BYTES = 200_000
SEARCH_MAX_BYTES = 1_000_000
ACTIVITY_DEFAULT_MAX_RESULTS = 500
ACTIVITY_MAX_RESULTS = 1000
ACTIVITY_MAX_PAGES = 10
ACTIVITY_MAX_PROJECTS = 200
GITLAB_ALL_PROJECTS = "*"
ACTIVITY_RESOURCE_MAX = {
    "commits": 1000,
    "merge_requests": 500,
    "issues": 500,
}
GITLAB_MAX_SUBMODULE_CONFIG_BYTES = 64 * 1024


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
            max_results: Annotated[int, Field(ge=1, le=1000)] = 500,
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
    scope_all = projects == [GITLAB_ALL_PROJECTS]
    projects_truncated = False
    if scope_all:
        projects, projects_truncated = _all_activity_projects(
            client,
            endpoint,
            secret,
        )
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
    payload = {
        "ok": True,
        "since": arguments["since"],
        "until": arguments["until"],
        "limits": {
            resource: min(max_results, limit)
            for resource, limit in ACTIVITY_RESOURCE_MAX.items()
        },
        "projects": [results[project] for project in projects],
    }
    if scope_all:
        payload["projects_truncated"] = projects_truncated
    return payload


def _activity_page(client, url, secret, params):
    """Read one bounded activity page and validate the provider response."""

    status, body, truncated = _get(client, url, secret, params, SEARCH_MAX_BYTES, False)
    _status(status)
    if truncated:
        raise PluginRuntimeError("GITLAB_RESPONSE_TOO_LARGE")
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, ValueError) as exc:
        raise PluginRuntimeError("GITLAB_RESPONSE_INVALID") from exc
    if not isinstance(payload, list):
        raise PluginRuntimeError("GITLAB_RESPONSE_INVALID")
    return payload


def _all_activity_projects(client, endpoint, secret):
    """Resolve the all-projects sentinel to visible project identities."""

    projects = []
    seen = set()
    for page in range(1, ACTIVITY_MAX_PAGES + 1):
        try:
            payload = _activity_page(
                client,
                f"{endpoint}/api/v4/projects",
                secret,
                {
                    "simple": "true",
                    "order_by": "last_activity_at",
                    "sort": "desc",
                    "per_page": 100,
                    "page": page,
                },
            )
        except (PluginRuntimeError, httpx.HTTPError):
            if page == 1:
                raise
            return projects, True
        new_count = 0
        for item in payload:
            name = item.get("path_with_namespace") if isinstance(item, dict) else None
            if not isinstance(name, str):
                continue
            value = name.strip().strip("/")
            if "/" not in value or len(value) > 255 or value.casefold() in seen:
                continue
            seen.add(value.casefold())
            projects.append(value)
            new_count += 1
        if len(projects) > ACTIVITY_MAX_PROJECTS:
            return projects[:ACTIVITY_MAX_PROJECTS], True
        if len(payload) < 100:
            if not projects:
                raise PluginRuntimeError("GITLAB_PROJECTS_UNAVAILABLE")
            return projects, False
        if len(projects) >= ACTIVITY_MAX_PROJECTS or not new_count:
            return projects, True
    return projects, True


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

    result_limit = min(max_results, ACTIVITY_RESOURCE_MAX[resource])
    page_size = min(result_limit, 50)
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
    items = []
    seen = set()
    for page in range(1, ACTIVITY_MAX_PAGES + 1):
        params["page"] = page
        try:
            payload = _activity_page(client, f"{endpoint}/api/v4/{path}", secret, params)
        except (PluginRuntimeError, httpx.HTTPError):
            if page == 1:
                raise
            return items, True
        new_count = 0
        for raw in payload:
            if not isinstance(raw, dict):
                continue
            identity = raw.get("id", raw.get("iid"))
            if identity in seen:
                continue
            seen.add(identity)
            new_count += 1
            timestamps = (
                (timestamp_field,)
                if resource == "commits"
                else (
                    timestamp_field,
                    "created_at",
                    "merged_at",
                    "closed_at",
                )
            )
            if any(_in_window(raw.get(field), since, until) for field in timestamps):
                items.append(projector(raw))
        older = False
        if resource != "commits" and payload and isinstance(payload[-1], dict):
            try:
                older = _timestamp(payload[-1].get(timestamp_field)) < since
            except PluginRuntimeError:
                pass
        if len(payload) < page_size or older:
            return items[:result_limit], len(items) > result_limit
        if len(items) >= result_limit or not new_count:
            return items[:result_limit], True
    return items[:result_limit], True


def _activity_commit(value):
    """Project one compact GitLab commit."""

    return {
        "sha": str(value.get("id") or "")[:64],
        "title": str(value.get("title") or "")[:300],
        "author_name": str(value.get("author_name") or "")[:160],
        "author_email": str(value.get("author_email") or "")[:254],
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
    if GITLAB_ALL_PROJECTS in projects and len(projects) != 1:
        raise PluginRuntimeError("PLUGIN_ARGUMENTS_INVALID")
    scope = config.get("__allowed_scope")
    allowed = scope.get("projects") if isinstance(scope, dict) else None
    if not isinstance(allowed, list):
        raise PluginRuntimeError("PLUGIN_SCOPE_MISMATCH")
    if "*" not in allowed:
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
    if (
        not isinstance(material, dict)
        or material.get("plugin_key") != PLUGIN_KEY
        or str(material.get("endpoint") or "").rstrip("/") != endpoint
        or not isinstance(material.get("value"), str)
        or (not material["value"] and material.get("authentication") != "anonymous")
    ):
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
    if "*" not in allowed and any(
        item.casefold() not in allowed for item in projects
    ):
        raise PluginRuntimeError("PLUGIN_SCOPE_VIOLATION")
    config = {
        "branch": datasource.get("branch") or "",
        "directory": datasource.get("directory") or "",
        "auth_scheme": "token" if material["value"] else "none",
        "allow_submodules": bool(material["value"]),
        "gitlab_endpoint": endpoint,
    }
    if material["value"]:
        config["access_token"] = material["value"]
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


def sync_datasource(command, workspace_path, emit, execute):
    """Execute GitLab synchronization with GitLab-owned submodule policy."""

    config = dict((command or {}).get("config") or {})
    config["submodule_url_resolver"] = _gitlab_submodule_url_rewrites
    return execute(
        {**command, "config": config},
        workspace_path,
        emit,
    )


def _gitlab_submodule_url_rewrites(target, config):
    """Return GitLab-owned SSH-to-HTTP submodule origin rewrites."""

    endpoint = _endpoint((config or {}).get("gitlab_endpoint"))
    modules_path = Path(target) / ".gitmodules"
    if not modules_path.is_file():
        return []
    if modules_path.stat().st_size > GITLAB_MAX_SUBMODULE_CONFIG_BYTES:
        raise PluginRuntimeError("GITLAB_SUBMODULE_CONFIG_TOO_LARGE")
    parser = configparser.RawConfigParser(interpolation=None)
    try:
        parser.read(modules_path, encoding="utf-8")
    except (configparser.Error, OSError, UnicodeDecodeError) as exc:
        raise PluginRuntimeError("GITLAB_SUBMODULE_CONFIG_INVALID") from exc
    rewrites = []
    seen = set()
    for section in parser.sections():
        if not section.startswith("submodule ") or not parser.has_option(
            section,
            "url",
        ):
            continue
        rewrite = _gitlab_submodule_url_rewrite(
            parser.get(section, "url"),
            endpoint,
        )
        if rewrite is None:
            continue
        identity = (rewrite["from"], rewrite["to"])
        if identity not in seen:
            seen.add(identity)
            rewrites.append(rewrite)
    return rewrites


def _gitlab_submodule_url_rewrite(url, endpoint):
    """Map one GitLab SSH URL origin to the approved HTTP endpoint."""

    value = str(url or "").strip()
    parsed = urlsplit(value)
    if (
        parsed.scheme == "ssh"
        and parsed.username == "git"
        and parsed.hostname
        and parsed.path.startswith("/")
        and not parsed.query
        and not parsed.fragment
    ):
        source = urlunsplit(("ssh", parsed.netloc, "/", "", ""))
    else:
        match = re.fullmatch(r"git@([^/:]+):.+", value)
        if match is None:
            return None
        source = f"git@{match.group(1)}:"
    return {"from": source, "to": f"{endpoint}/"}


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
    with client.stream(
        "GET",
        url,
        params=params,
        follow_redirects=False,
        headers={
            "Accept": "application/json",
            **({"PRIVATE-TOKEN": token} if token else {}),
            "User-Agent": "SourceLens-LensNode",
        },
    ) as response:
        if response.is_redirect: raise PluginRuntimeError("GITLAB_REDIRECT_REJECTED")
        if response.status_code >= 400: return response.status_code, b"", False
        body = b"".join(response.iter_bytes())
    return response.status_code, body[:max_bytes], len(body) > max_bytes


def _status(status):
    if status < 400: return
    raise PluginRuntimeError({404: "GITLAB_NOT_FOUND", 401: "GITLAB_ACCESS_DENIED", 403: "GITLAB_ACCESS_DENIED", 429: "GITLAB_RATE_LIMITED"}.get(status, "GITLAB_REQUEST_FAILED"))
