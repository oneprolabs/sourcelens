"""Jira LensNode runtime entrypoint."""

import base64
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Annotated
from urllib.parse import quote, urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from langchain.tools import ToolRuntime, tool
from pydantic import Field

from lensnode.plugin_runtime import PluginRuntimeError

PLUGIN_API_VERSION = 1
PLUGIN_KEY = "jira"
PLUGIN_VERSION = "1.0.0"
TOOL_METADATA = {"jira_activity_summary": {"durable_evidence": True}}
RESPONSE_MAX_BYTES = 1_000_000
ACTIVITY_DEFAULT_MAX_RESULTS = 500
ACTIVITY_MAX_RESULTS = 1000
ACTIVITY_MAX_PAGES = 10


def http_origins(endpoint):
    """Return the validated Jira origin for connection pooling."""

    return (_endpoint(endpoint),)


def build_tool(definition, executor):
    """Create a fixed Jira read-only tool."""

    if not isinstance(definition, dict) or definition.get("side_effect") != "none":
        raise PluginRuntimeError("PLUGIN_TOOL_NOT_READ_ONLY")
    key, description = str(definition.get("key") or ""), str(definition.get("description") or "").strip()
    expected = {
        "jira_get_issue": "issue.read",
        "jira_search_issues": "jira.issue.search",
        "jira_activity_summary": "jira.issue.search",
    }.get(key)
    if not expected or definition.get("capability") != expected: raise PluginRuntimeError("PLUGIN_TOOL_NOT_READ_ONLY")
    if not description: raise PluginRuntimeError("PLUGIN_TOOL_DESCRIPTION_REQUIRED")
    if key == "jira_get_issue":
        def invoke(issue_key: Annotated[str, Field(min_length=3, max_length=40)], runtime: ToolRuntime) -> str:
            return executor(key, {"issue_key": issue_key}, runtime)
        return tool(key, description=description)(invoke)
    if key == "jira_activity_summary":

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
    def invoke(project: Annotated[str, Field(min_length=2, max_length=20)], query: Annotated[str, Field(min_length=1, max_length=500)], runtime: ToolRuntime, max_results: Annotated[int, Field(ge=1, le=20)] = 10) -> str:
        return executor(key, {"project": project, "query": query, "max_results": max_results}, runtime)
    return tool(key, description=description)(invoke)


def execute_tool(key, client, arguments, secret, endpoint, config):
    """Execute one bounded Jira REST call."""

    endpoint = _endpoint(endpoint)
    if key == "jira_activity_summary":
        return _activity_summary(
            client,
            arguments,
            secret,
            endpoint,
            config,
        )
    headers = _headers(secret, config)
    if key == "jira_get_issue":
        issue_key = quote(_text(arguments, "issue_key"), safe="")
        payload = _request(
            client,
            _api_url(endpoint, f"issue/{issue_key}"),
            headers,
            {
                "fields": (
                    "summary,status,assignee,priority,updated,description"
                )
            },
        )
        return {"ok": True, "issue": _issue(payload)}
    if key == "jira_search_issues":
        project, query = _text(arguments, "project"), _text(arguments, "query")
        max_results = arguments.get("max_results", 10)
        escaped = query.replace("\\", "\\\\").replace('"', '\\"')
        payload = _request(
            client,
            _api_url(endpoint, "search", cloud_resource="search/jql"),
            headers,
            {
                "jql": f'project = "{project}" AND text ~ "{escaped}"',
                "maxResults": max_results,
                "fields": "summary,status,assignee,priority,updated",
            },
        )
        issues = payload.get("issues") if isinstance(payload, dict) else None
        if not isinstance(issues, list): raise PluginRuntimeError("JIRA_RESPONSE_INVALID")
        return {"ok": True, "project": project, "query": query, "items": [_issue(item) for item in issues[:max_results]]}
    raise PluginRuntimeError("PLUGIN_TOOL_UNSUPPORTED")


def _activity_summary(client, arguments, secret, endpoint, config):
    """Return bounded Jira activity through fixed REST requests."""

    projects, since, until, max_results = _activity_arguments(
        arguments,
        config,
    )
    headers = _headers(secret, config)
    jira_timezone = _jira_timezone(client, endpoint, headers)
    items = []
    errors = {}
    possibly_truncated = False
    workers = min(6, len(projects))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                _jira_project_activity,
                client,
                endpoint,
                headers,
                project,
                since,
                until,
                max_results,
                jira_timezone,
            ): project
            for project in projects
        }
        for future in as_completed(futures):
            project = futures[future]
            try:
                project_items, truncated = future.result()
            except PluginRuntimeError as exc:
                errors[project] = str(exc)
                continue
            except Exception:
                errors[project] = "JIRA_REQUEST_FAILED"
                continue
            items.extend(project_items)
            possibly_truncated = possibly_truncated or truncated
    if not items and len(errors) == len(projects):
        raise PluginRuntimeError("JIRA_REQUEST_FAILED")
    items.sort(key=lambda item: item.get("updated") or "", reverse=True)
    if len(items) > max_results:
        possibly_truncated = True
        items = items[:max_results]
    result = {
        "ok": True,
        "since": arguments["since"],
        "until": arguments["until"],
        "items": items,
        "possibly_truncated": possibly_truncated,
    }
    if errors:
        result["errors"] = errors
    return result


def _jira_project_activity(
    client,
    endpoint,
    headers,
    project,
    since,
    until,
    max_results,
    jira_timezone,
):
    """Query one approved Jira project with fixed REST parameters."""

    lower_bound, upper_bound = _jql_time_bounds(
        since,
        until,
        jira_timezone,
    )
    jql = (
        f'project = "{project}" AND ('
        f'(updated >= "{lower_bound}" AND updated < "{upper_bound}") OR '
        f'(resolved >= "{lower_bound}" AND resolved < "{upper_bound}") OR '
        f'(created >= "{lower_bound}" AND created < "{upper_bound}")) ORDER BY updated DESC'
    )
    url = _api_url(endpoint, "search", cloud_resource="search/jql")
    params = {
        "jql": jql,
        "maxResults": min(max_results, 100),
        "fields": "summary,status,assignee,reporter,creator,priority,created,updated,resolutiondate,description",
    }
    items = []
    seen = set()
    tokens = set()
    fetched = 0
    for _ in range(ACTIVITY_MAX_PAGES):
        try:
            payload = _request(client, url, headers, params)
        except (PluginRuntimeError, httpx.HTTPError):
            if not seen:
                raise
            return items[:max_results], True
        raw_items = payload.get("issues") if isinstance(payload, dict) else None
        if not isinstance(raw_items, list):
            if seen:
                return items[:max_results], True
            raise PluginRuntimeError("JIRA_RESPONSE_INVALID")
        new_count = 0
        for raw_item in raw_items:
            item = _activity_issue(raw_item, project)
            if item["key"] in seen:
                continue
            seen.add(item["key"])
            new_count += 1
            if any(_in_window(item.get(field), since, until) for field in ("updated", "created", "resolution_date")):
                items.append(item)
        fetched += len(raw_items)
        if raw_items and not new_count:
            return items[:max_results], True
        total = payload.get("total")
        if payload.get("isLast") is True or (type(total) is int and len(seen) >= total):
            return items[:max_results], len(items) > max_results
        more = (
            payload.get("isLast") is False
            or bool(payload.get("nextPageToken"))
            or (type(total) is int and fetched < total)
        )
        if not raw_items:
            return items[:max_results], more or len(items) > max_results
        if len(raw_items) < params["maxResults"] and not more:
            return items[:max_results], len(items) > max_results
        if len(items) >= max_results or not new_count:
            return items[:max_results], True
        if url.endswith("/search/jql"):
            token = payload.get("nextPageToken")
            if not isinstance(token, str) or not token or token in tokens:
                return items[:max_results], True
            tokens.add(token)
            params["nextPageToken"] = token
        else:
            params["startAt"] = fetched
    return items[:max_results], True


def _jira_timezone(client, endpoint, headers):
    """Return the authenticated Jira user's configured timezone."""

    payload = _request(
        client,
        _api_url(endpoint, "myself"),
        headers,
        {},
    )
    timezone_name = payload.get("timeZone")
    if not isinstance(timezone_name, str) or not timezone_name:
        raise PluginRuntimeError("JIRA_RESPONSE_INVALID")
    try:
        return ZoneInfo(timezone_name)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise PluginRuntimeError("JIRA_RESPONSE_INVALID") from exc


def _jql_time_bounds(since, until, jira_timezone):
    """Return inclusive/exclusive minute bounds in Jira's user timezone."""

    lower_utc = since.replace(second=0, microsecond=0)
    upper_utc = until.replace(second=0, microsecond=0) + timedelta(minutes=1)
    return (
        lower_utc.astimezone(jira_timezone).strftime("%Y-%m-%d %H:%M"),
        upper_utc.astimezone(jira_timezone).strftime("%Y-%m-%d %H:%M"),
    )


def _activity_issue(value, project):
    """Project one untrusted Jira issue into compact report evidence."""

    item = _issue(value)
    fields = value["fields"]

    def nested(name):
        nested_value = fields.get(name)
        if not isinstance(nested_value, dict):
            return ""
        return str(
            nested_value.get("displayName")
            or nested_value.get("name")
            or ""
        )[:160]

    item.update(
        {
            "project": project,
            "reporter": nested("reporter"),
            "creator": nested("creator"),
            "creator_identity": _activity_user_identity(fields.get("creator")),
            "reporter_identity": _activity_user_identity(fields.get("reporter")),
            "created": str(fields.get("created") or "")[:64],
            "resolution_date": str(fields.get("resolutiondate") or "")[:64],
            "assignee_identity": (
                str(
                    (fields.get("assignee") or {}).get("accountId")
                    or (fields.get("assignee") or {}).get("key")
                    or (fields.get("assignee") or {}).get("name")
                    or ""
                )[:160]
                if isinstance(fields.get("assignee"), dict)
                else ""
            ),
        }
    )
    item["description"] = item["description"][:2000]
    return item


def _activity_user_identity(value):
    """Preserve a stable source identity independently of its display name."""

    if not isinstance(value, dict):
        return ""
    return str(value.get("accountId") or value.get("key") or "")[:160]


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
        or len(set(projects)) != len(projects)
    ):
        raise PluginRuntimeError("PLUGIN_ARGUMENTS_INVALID")
    scope = config.get("__allowed_scope")
    allowed = scope.get("projects") if isinstance(scope, dict) else None
    if not isinstance(allowed, list):
        raise PluginRuntimeError("PLUGIN_SCOPE_MISMATCH")
    allowed_keys = {str(item).upper() for item in allowed}
    if any(project.upper() not in allowed_keys for project in projects):
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
    return [item.upper() for item in projects], since, until, max_results


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


def _endpoint(value):
    try:
        parsed = urlsplit(str(value or "").strip())
        parsed.port
    except ValueError as exc:
        raise PluginRuntimeError("PLUGIN_SNAPSHOT_MISMATCH") from exc
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise PluginRuntimeError("PLUGIN_SNAPSHOT_MISMATCH")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc, "", "", ""))


def _api_url(endpoint, resource, cloud_resource=None):
    """Return a Jira Cloud or self-hosted REST API URL."""

    hostname = (urlsplit(endpoint).hostname or "").lower()
    if hostname.endswith(".atlassian.net"):
        return f"{endpoint}/rest/api/3/{cloud_resource or resource}"
    return f"{endpoint}/rest/api/2/{resource}"


def _headers(token, config):
    account = config.get("email") if isinstance(config, dict) else ""
    if not isinstance(account, str) or not account or not token:
        raise PluginRuntimeError("PLUGIN_SNAPSHOT_MISMATCH")
    auth = base64.b64encode(f"{account}:{token}".encode()).decode("ascii")
    return {
        "Accept": "application/json",
        "Authorization": f"Basic {auth}",
        "User-Agent": "SourceLens-LensNode",
    }


def _text(arguments, name):
    value = arguments.get(name)
    if not isinstance(value, str) or not value: raise PluginRuntimeError("PLUGIN_ARGUMENTS_INVALID")
    return value


def _request(client, url, headers, params):
    with client.stream("GET", url, params=params, headers=headers, follow_redirects=False) as response:
        if response.is_redirect: raise PluginRuntimeError("JIRA_REDIRECT_REJECTED")
        if response.status_code >= 400: raise PluginRuntimeError({404: "JIRA_NOT_FOUND", 401: "JIRA_ACCESS_DENIED", 403: "JIRA_ACCESS_DENIED", 429: "JIRA_RATE_LIMITED"}.get(response.status_code, "JIRA_REQUEST_FAILED"))
        body = b"".join(response.iter_bytes())
    if len(body) > RESPONSE_MAX_BYTES: raise PluginRuntimeError("JIRA_RESPONSE_TOO_LARGE")
    try: payload = json.loads(body)
    except (UnicodeDecodeError, ValueError) as exc: raise PluginRuntimeError("JIRA_RESPONSE_INVALID") from exc
    if not isinstance(payload, dict): raise PluginRuntimeError("JIRA_RESPONSE_INVALID")
    return payload


def _issue(value):
    if not isinstance(value, dict) or not isinstance(value.get("fields"), dict) or not isinstance(value.get("key"), str): raise PluginRuntimeError("JIRA_RESPONSE_INVALID")
    fields = value["fields"]
    def nested(name):
        item = fields.get(name); return str(item.get("name") or item.get("displayName") or "")[:160] if isinstance(item, dict) else ""
    return {"key": value["key"][:40], "summary": str(fields.get("summary") or "")[:1000], "status": nested("status"), "assignee": nested("assignee"), "priority": nested("priority"), "updated": str(fields.get("updated") or "")[:64], "description": _description(fields.get("description"))}


def _description(value):
    output = []
    def visit(item):
        if len("".join(output)) >= 20000: return
        if isinstance(item, dict):
            if isinstance(item.get("text"), str): output.append(item["text"])
            for child in item.get("content") or []: visit(child)
        elif isinstance(item, list):
            for child in item: visit(child)
    visit(value); return "\n".join(output)[:20000]


def project_tool_result(tool_key, result, max_bytes):
    """Declare source evidence coverage without host-side provider knowledge."""

    from lensnode.plugin_package_loader import load_plugin_support

    return load_plugin_support("activity_evidence").project_activity_result(
        tool_key, result, max_bytes, TOOL_METADATA
    )


def project_tool_evidence(tool_key, result, connection_uuid):
    """Interpret source events without prescribing report analysis or output."""

    from lensnode.plugin_package_loader import load_plugin_support

    return load_plugin_support("activity_evidence").activity_evidence(PLUGIN_KEY, result, connection_uuid)
