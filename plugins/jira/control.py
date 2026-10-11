"""Atlassian Jira Connection and Tool Provider implementation."""

import base64
import json
import re
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit

import httpx

from lens.plugins.contracts import ToolProviderError
from lens.plugins.providers.base import (
    DatasourceProvider,
    DatasourceProviderError,
    PluginRequestContext,
    retry_after_seconds,
)

PROJECT_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{1,19}$")
ALLOWED_SCOPE_KEYS = frozenset({"projects"})
JIRA_MAX_PROJECTS = 50
JIRA_TIMEOUT_SECONDS = 15
JIRA_MAX_RESPONSE_BYTES = 500_000

PLUGIN_API_VERSION = 1
PLUGIN_KEY = "jira"
PLUGIN_VERSION = "1.0.0"


class JiraConnectionProvider(DatasourceProvider):
    """Validate read-only Jira Connection selections."""

    key = "jira"

    def validate_connection(self, endpoint, connection_config):
        """Accept an Atlassian Cloud site and non-secret account."""

        _account(connection_config)
        return _endpoint(endpoint)

    def validate_connection_scope(self, connection_scope):
        """Normalize explicit Jira project keys for one Connection."""

        if not isinstance(connection_scope, dict):
            raise DatasourceProviderError("connection scope must be an object")
        if set(connection_scope).difference(ALLOWED_SCOPE_KEYS):
            raise DatasourceProviderError(
                "connection scope contains unsupported fields"
            )
        projects = connection_scope.get("projects")
        if not isinstance(projects, list) or not projects:
            raise DatasourceProviderError("connection scope requires projects")
        if len(projects) > JIRA_MAX_PROJECTS:
            raise DatasourceProviderError(
                "connection scope contains too many projects"
            )
        normalized = []
        identities = set()
        for value in projects:
            project = _project_key(value)
            if project not in identities:
                identities.add(project)
                normalized.append(project)
        return {"projects": normalized}

    def validate_live_connection(
        self,
        secret,
        endpoint="",
        connection_config=None,
        client=None,
        request_context=None,
    ):
        """Validate Jira API token authentication."""

        base_url = self.validate_connection(endpoint, connection_config)
        headers = _headers(connection_config, secret)
        context = request_context or PluginRequestContext(
            timeout_seconds=JIRA_TIMEOUT_SECONDS,
        )
        with _JiraClient(client) as jira_client:
            payload = context.run(
                lambda: _jira_json(
                    jira_client,
                    base_url,
                    _api_path(base_url, "myself"),
                    headers,
                )
            )
        if not isinstance(payload, dict):
            raise DatasourceProviderError("JIRA_RESPONSE_INVALID")
        account_id = (
            payload.get("accountId")
            or payload.get("key")
            or payload.get("name")
        )
        display_name = payload.get("displayName")
        if not isinstance(account_id, str) or not account_id:
            raise DatasourceProviderError("JIRA_RESPONSE_INVALID")
        return {
            "account": {
                "account_id": account_id[:160],
                "display_name": (
                    display_name[:160]
                    if isinstance(display_name, str)
                    else ""
                ),
            }
        }

    def discover_connection_resources(
        self,
        secret,
        endpoint="",
        connection_config=None,
        query="",
        cursor="",
        limit=50,
        client=None,
        request_context=None,
    ):
        """List Jira projects visible to a temporary Connection secret."""

        base_url = self.validate_connection(endpoint, connection_config)
        headers = _headers(connection_config, secret)
        offset = _connection_resource_offset(cursor)
        limit = _connection_resource_limit(limit)
        query = _connection_resource_query(query)
        cloud = _is_cloud_endpoint(base_url)
        path = _api_path(
            base_url,
            "project/search" if cloud else "project",
        )
        params = None
        if cloud:
            params = {
                "startAt": offset,
                "maxResults": limit,
                "orderBy": "key",
            }
            if query:
                params["query"] = query
        context = request_context or PluginRequestContext(
            timeout_seconds=JIRA_TIMEOUT_SECONDS,
        )
        with _JiraClient(client) as jira_client:
            payload = context.run(
                lambda: _jira_json(
                    jira_client,
                    base_url,
                    path,
                    headers,
                    params=params,
                )
            )
        if cloud:
            raw_items = (
                payload.get("values")
                if isinstance(payload, dict)
                else None
            )
            is_last = (
                payload.get("isLast")
                if isinstance(payload, dict)
                else None
            )
            if not isinstance(raw_items, list) or not isinstance(
                is_last,
                bool,
            ):
                raise DatasourceProviderError("JIRA_RESPONSE_INVALID")
            page_items = raw_items[:limit]
            next_cursor = "" if is_last else str(offset + len(page_items))
        else:
            if not isinstance(payload, list):
                raise DatasourceProviderError("JIRA_RESPONSE_INVALID")
            filtered = _filter_jira_projects(payload, query)
            page_items = filtered[offset : offset + limit]
            next_cursor = (
                str(offset + len(page_items))
                if offset + len(page_items) < len(filtered)
                else ""
            )
        return {
            "resources": {
                "projects": {
                    "items": _jira_project_items(page_items),
                }
            },
            "next_cursor": next_cursor,
        }


def _endpoint(value):
    """Return a safe root Jira HTTP(S) endpoint."""

    try:
        parsed = urlsplit(str(value or "").strip())
        parsed.port
    except ValueError as exc:
        raise DatasourceProviderError("Jira endpoint is invalid") from exc
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise DatasourceProviderError("Jira endpoint is invalid")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc, "", "", ""))


def _api_path(endpoint, resource):
    """Return the Jira Cloud or self-hosted REST API path."""

    hostname = (urlsplit(endpoint).hostname or "").lower()
    version = "3" if hostname.endswith(".atlassian.net") else "2"
    return f"/rest/api/{version}/{resource}"


def _is_cloud_endpoint(endpoint):
    """Return whether an endpoint is an Atlassian Jira Cloud site."""

    hostname = (urlsplit(endpoint).hostname or "").lower()
    return hostname.endswith(".atlassian.net")


def _connection_resource_offset(value):
    """Return one bounded zero-based Jira project cursor."""

    if value in {None, ""}:
        return 0
    try:
        offset = int(value)
    except (TypeError, ValueError) as exc:
        raise DatasourceProviderError(
            "connection resource cursor is invalid"
        ) from exc
    if offset < 0 or offset > 100_000:
        raise DatasourceProviderError(
            "connection resource cursor is invalid"
        )
    return offset


def _connection_resource_limit(value):
    """Return one bounded Jira project page size."""

    if isinstance(value, bool) or not isinstance(value, int):
        raise DatasourceProviderError(
            "connection resource limit is invalid"
        )
    return min(max(value, 1), 100)


def _connection_resource_query(value):
    """Return one bounded optional Jira project query."""

    if not isinstance(value, str) or len(value) > 100 or any(
        ord(character) < 32 for character in value
    ):
        raise DatasourceProviderError(
            "connection resource query is invalid"
        )
    return value.strip()


def _filter_jira_projects(projects, query):
    """Filter self-hosted Jira projects by key or display name."""

    query_key = query.casefold()
    if not query_key:
        return projects
    return [
        item
        for item in projects
        if isinstance(item, dict)
        and query_key
        in f"{item.get('key') or ''} {item.get('name') or ''}".casefold()
    ]


def _jira_project_items(projects):
    """Return normalized selectable Jira project items."""

    items = []
    for item in projects:
        if not isinstance(item, dict):
            continue
        try:
            key = _project_key(item.get("key"))
        except DatasourceProviderError:
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        name = name.strip()[:160]
        items.append(
            {
                "value": key,
                "label": f"{key} · {name}",
                "metadata": {"name": name},
            }
        )
    return items


def _account(connection_config):
    """Return the bounded non-secret Jira account identifier."""

    if not isinstance(connection_config, dict) or set(
        connection_config
    ) != {"email"}:
        raise DatasourceProviderError(
            "Jira connection config requires only account"
        )
    account = connection_config.get("email")
    if (
        not isinstance(account, str)
        or not account.strip()
        or len(account) > 254
        or any(character.isspace() for character in account.strip())
    ):
        raise DatasourceProviderError("Jira account is invalid")
    return account.strip()


def _project_key(value):
    """Return one canonical Jira project key."""

    if not isinstance(value, str):
        raise DatasourceProviderError("project is required")
    project = value.strip().upper()
    if not PROJECT_PATTERN.fullmatch(project):
        raise DatasourceProviderError("Jira project key is invalid")
    return project


def _headers(connection_config, secret):
    """Build Jira Basic authentication without exposing raw values."""

    account = _account(connection_config)
    if not isinstance(secret, str) or not secret:
        raise DatasourceProviderError("JIRA_SECRET_UNAVAILABLE")
    credential = base64.b64encode(
        f"{account}:{secret}".encode("utf-8")
    ).decode("ascii")
    return {
        "Accept": "application/json",
        "Authorization": f"Basic {credential}",
        "User-Agent": "SourceLens-Control-Plane",
    }


class _JiraClient:
    """Require the HTTP client injected by the SourceLens host."""

    def __init__(self, client):
        self.client = client

    def __enter__(self):
        if self.client is None:
            raise DatasourceProviderError(
                "PLUGIN_HTTP_CLIENT_REQUIRED"
            )
        return self.client

    def __exit__(self, exc_type, exc_value, traceback):
        return False



def _jira_json(client, endpoint, path, headers, params=None):
    """Read bounded JSON from one validated Atlassian Cloud site."""

    try:
        with client.stream(
            "GET",
            f"{endpoint}{path}",
            params=params,
            headers=headers,
            follow_redirects=False,
        ) as response:
            if response.is_redirect:
                raise DatasourceProviderError("JIRA_REDIRECT_REJECTED")
            if response.status_code >= 400:
                retry_after = (
                    retry_after_seconds(response.headers.get("Retry-After"))
                    if response.status_code == 429
                    else None
                )
                raise DatasourceProviderError(
                    _jira_error(response.status_code),
                    retry_after=retry_after,
                )
            body = bytearray()
            for chunk in response.iter_bytes():
                if len(body) + len(chunk) > JIRA_MAX_RESPONSE_BYTES:
                    raise DatasourceProviderError("JIRA_RESPONSE_TOO_LARGE")
                body.extend(chunk)
    except DatasourceProviderError:
        raise
    except httpx.HTTPError as exc:
        raise DatasourceProviderError("JIRA_REQUEST_FAILED") from exc
    try:
        return json.loads(body)
    except (UnicodeDecodeError, ValueError) as exc:
        raise DatasourceProviderError("JIRA_RESPONSE_INVALID") from exc


def _jira_error(status_code):
    """Map Jira statuses without exposing response bodies."""

    if status_code == 404:
        return "JIRA_NOT_FOUND"
    if status_code in {401, 403}:
        return "JIRA_ACCESS_DENIED"
    if status_code == 429:
        return "JIRA_RATE_LIMITED"
    return "JIRA_REQUEST_FAILED"


class JiraToolProvider:
    """Validate bounded read-only Jira Tool requests."""

    key = "jira"

    def validate_request(self, endpoint, allowed_scope, tool_key, arguments):
        """Return canonical endpoint and authorized arguments."""

        try:
            endpoint = _endpoint(endpoint)
            allowed = set(
                JiraConnectionProvider()
                .validate_connection_scope(allowed_scope)["projects"]
            )
        except DatasourceProviderError as exc:
            raise ToolProviderError(str(exc)) from exc
        if not isinstance(arguments, dict):
            raise ToolProviderError("tool arguments must be an object")
        if tool_key == "jira_get_issue":
            issue_key = _issue_key(arguments.get("issue_key"))
            if issue_key.rsplit("-", 1)[0] not in allowed:
                raise ToolProviderError("issue is outside connection scope")
            return endpoint, {"issue_key": issue_key}
        if tool_key == "jira_search_issues":
            try:
                project = _project_key(arguments.get("project"))
            except DatasourceProviderError as exc:
                raise ToolProviderError(str(exc)) from exc
            if project not in allowed:
                raise ToolProviderError("project is outside connection scope")
            return endpoint, {
                "project": project,
                "query": _tool_text(
                    arguments.get("query"),
                    "query",
                    500,
                    required=True,
                ),
                "max_results": _tool_max_results(
                    arguments.get("max_results")
                ),
            }
        if tool_key == "jira_activity_summary":
            projects = _tool_projects(arguments.get("projects"))
            if any(project not in allowed for project in projects):
                raise ToolProviderError("project is outside connection scope")
            since = _tool_timestamp(arguments.get("since"), "since")
            until = _tool_timestamp(arguments.get("until"), "until")
            if datetime.fromisoformat(since.replace("Z", "+00:00")) > (
                datetime.fromisoformat(until.replace("Z", "+00:00"))
            ):
                raise ToolProviderError("time window is invalid")
            return endpoint, {
                "projects": projects,
                "since": since,
                "until": until,
                "max_results": _activity_max_results(
                    arguments.get("max_results")
                ),
            }
        raise ToolProviderError("tool is unsupported")


def _tool_text(value, field, limit, required=False):
    """Return bounded Tool text without control characters."""

    if value is None and not required:
        return ""
    if not isinstance(value, str):
        raise ToolProviderError(f"{field} must be a string")
    text = value.strip()
    if (required and not text) or len(text) > limit or any(
        ord(character) < 32 for character in text
    ):
        raise ToolProviderError(f"{field} is invalid")
    return text


def _issue_key(value):
    """Return one bounded canonical Jira Issue key."""

    text = _tool_text(value, "issue_key", 40, required=True).upper()
    parts = text.rsplit("-", 1)
    if len(parts) != 2 or not parts[1].isdigit() or int(parts[1]) < 1:
        raise ToolProviderError("issue_key is invalid")
    try:
        _project_key(parts[0])
    except DatasourceProviderError as exc:
        raise ToolProviderError("issue_key is invalid") from exc
    return text


def _tool_max_results(value):
    """Return one bounded search result count."""

    if value is None:
        return 10
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 1
        or value > 20
    ):
        raise ToolProviderError("max_results must be between 1 and 20")
    return value


def _activity_max_results(value):
    """Return one bounded activity result count."""

    if value is None:
        return 500
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > 1000:
        raise ToolProviderError("max_results must be between 1 and 1000")
    return value


def _tool_projects(value):
    """Return unique canonical Jira projects."""

    if not isinstance(value, list) or not value or len(value) > 50:
        raise ToolProviderError("projects must contain 1 through 50 items")
    projects = []
    try:
        for item in value:
            projects.append(_project_key(item))
    except DatasourceProviderError as exc:
        raise ToolProviderError(str(exc)) from exc
    if len(set(projects)) != len(projects):
        raise ToolProviderError("projects must be unique")
    return projects


def _tool_timestamp(value, field):
    """Return one bounded timezone-aware ISO-8601 timestamp."""

    text = _tool_text(value, field, 64, required=True)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ToolProviderError(f"{field} is invalid") from exc
    if parsed.tzinfo is None:
        raise ToolProviderError(f"{field} is invalid")
    return text


DATASOURCE_PROVIDER = JiraConnectionProvider()
TOOL_PROVIDER = JiraToolProvider()
