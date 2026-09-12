"""GitHub implementation of the generic datasource provider contract."""

import json
import re
from datetime import datetime
from pathlib import PurePosixPath
from urllib.parse import quote, urlsplit

import httpx

from lens.plugins.contracts import ToolProviderError
from lens.plugins.providers.base import (
    DatasourceProvider,
    DatasourceProviderError,
    PluginRequestContext,
    retry_after_seconds,
)

SENSITIVE_CONFIG_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "authorization",
        "credential",
        "credentials",
        "password",
        "secret",
        "token",
    }
)
ALLOWED_CONFIG_KEYS = frozenset(
    {"repository", "repositories", "branch", "directory"}
)
ALLOWED_SCOPE_KEYS = frozenset({"repositories"})
GITHUB_API_URL = "https://api.github.com"
GITHUB_API_VERSION = "2022-11-28"
GITHUB_MAX_RESPONSE_BYTES = 500_000
GITHUB_MAX_REPOSITORIES = 50
GITHUB_MAX_BRANCHES = 100
GITHUB_DISCOVERY_WORKERS = 5
GITHUB_TIMEOUT_SECONDS = 15
GITHUB_MAX_BRANCH_LENGTH = 255
GITHUB_MAX_DIRECTORY_LENGTH = 1000
SEARCH_SCOPE_PATTERN = re.compile(r"(?:repo|org|user):", re.IGNORECASE)
GITHUB_TOOL_KEYS = frozenset(
    {
        "github_read_file",
        "github_search_code",
        "github_repository_get",
        "github_activity_summary",
        "github_branch_list",
        "github_commit_list",
        "github_commit_get",
        "github_issue_list",
        "github_issue_get",
        "github_issue_comments",
        "github_pull_request_list",
        "github_pull_request_get",
        "github_pull_request_files",
        "github_pull_request_reviews",
        "github_release_list",
        "github_workflow_run_list",
        "github_workflow_run_get",
    }
)
PAGINATED_TOOL_KEYS = frozenset(
    {
        "github_branch_list",
        "github_commit_list",
        "github_issue_list",
        "github_issue_comments",
        "github_pull_request_list",
        "github_pull_request_files",
        "github_pull_request_reviews",
        "github_release_list",
        "github_workflow_run_list",
    }
)
NUMBERED_TOOL_KEYS = frozenset(
    {
        "github_issue_get",
        "github_issue_comments",
        "github_pull_request_get",
        "github_pull_request_files",
        "github_pull_request_reviews",
    }
)
WORKFLOW_STATUSES = frozenset(
    {
        "action_required",
        "cancelled",
        "completed",
        "failure",
        "in_progress",
        "neutral",
        "pending",
        "queued",
        "requested",
        "skipped",
        "stale",
        "startup_failure",
        "success",
        "timed_out",
        "waiting",
    }
)

PLUGIN_API_VERSION = 1
PLUGIN_KEY = "github"
PLUGIN_VERSION = "1.0.0"


class GitHubDatasourceProvider(DatasourceProvider):
    """Validate read-only GitHub repository datasource selections."""

    key = "github"

    def http_origins(self, endpoint, connection_config=None):
        """Return the fixed GitHub REST API origin."""

        self.validate_connection(endpoint, connection_config)
        return (GITHUB_API_URL,)

    def validate_datasource_source_type(self, source_type):
        """Bind the GitHub Provider to the existing Git datasource runtime."""

        if source_type != "git":
            raise DatasourceProviderError(
                "GitHub datasource source type must be git"
            )
        return source_type

    def validate_connection(self, endpoint, connection_config):
        """Allow only the public GitHub HTTPS endpoint in V1."""

        if connection_config not in ({}, None):
            raise DatasourceProviderError(
                "GitHub connection config contains unsupported fields"
            )
        normalized_endpoint = str(endpoint or "").strip()
        if not normalized_endpoint:
            return "https://github.com"
        parsed = urlsplit(normalized_endpoint)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "github.com"
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise DatasourceProviderError(
                "GitHub connection endpoint must be https://github.com"
            )
        return "https://github.com"

    def validate_connection_scope(self, connection_scope):
        """Normalize the explicit repository allowlist for one Connection."""

        if not isinstance(connection_scope, dict):
            raise DatasourceProviderError("connection scope must be an object")
        if set(connection_scope).difference(ALLOWED_SCOPE_KEYS):
            raise DatasourceProviderError(
                "connection scope contains unsupported fields"
            )
        repositories = connection_scope.get("repositories")
        if not isinstance(repositories, list) or not repositories:
            raise DatasourceProviderError(
                "connection scope requires repositories"
            )
        if len(repositories) > GITHUB_MAX_REPOSITORIES:
            raise DatasourceProviderError(
                "connection scope contains too many repositories"
            )
        normalized = []
        identities = set()
        for value in repositories:
            repository = _repository_name(value)
            identity = repository.casefold()
            if identity not in identities:
                identities.add(identity)
                normalized.append(repository)
        return {"repositories": normalized}

    def validate_live_connection(
        self,
        secret,
        endpoint="",
        connection_config=None,
        client=None,
        request_context=None,
    ):
        """Validate one PAT and return bounded, non-sensitive account data."""

        if endpoint or connection_config:
            self.validate_connection(endpoint, connection_config)
        token = _secret_value(secret)
        context = request_context or PluginRequestContext(
            timeout_seconds=GITHUB_TIMEOUT_SECONDS,
        )
        with _GitHubClient(client) as github_client:
            payload = context.run(
                lambda: _github_json(github_client, "/user", token)
            )
        if not isinstance(payload, dict):
            raise DatasourceProviderError("GITHUB_RESPONSE_INVALID")
        login = payload.get("login")
        if not isinstance(login, str) or not login:
            raise DatasourceProviderError("GITHUB_RESPONSE_INVALID")
        name = payload.get("name")
        return {
            "account": {
                "login": login[:160],
                "name": name[:160] if isinstance(name, str) else "",
            }
        }

    def discover_resources(
        self,
        connection_scope,
        secret,
        endpoint="",
        connection_config=None,
        client=None,
        request_context=None,
    ):
        """Return the explicit repository allowlist without remote requests."""

        if endpoint or connection_config:
            self.validate_connection(endpoint, connection_config)
        scope = self.validate_connection_scope(connection_scope)
        del secret, client, request_context
        return {
            "resources": {
                "repositories": {
                    "items": [
                        {"value": repository, "label": repository}
                        for repository in scope["repositories"]
                    ],
                }
            }
        }

    def discover_resource_options(
        self,
        connection_scope,
        secret,
        resource,
        selected_values,
        endpoint="",
        connection_config=None,
        client=None,
        request_context=None,
    ):
        """Return branches for one repository in the approved allowlist."""

        if resource != "branches":
            raise DatasourceProviderError("resource options are unsupported")
        if endpoint or connection_config:
            self.validate_connection(endpoint, connection_config)
        if not isinstance(selected_values, dict):
            raise DatasourceProviderError("resource dependency is invalid")
        repository_value = selected_values.get("repository")
        if repository_value is None:
            repositories = selected_values.get("repositories")
            repository_value = (
                repositories[0]
                if isinstance(repositories, list) and repositories
                else None
            )
        repository = _repository_name(repository_value)
        if repository.casefold() not in _allowed_repositories(connection_scope):
            raise DatasourceProviderError("repository is outside connection scope")
        token = _secret_value(secret)
        context = request_context or PluginRequestContext(
            timeout_seconds=GITHUB_TIMEOUT_SECONDS,
        )
        path = quote(repository, safe="/")
        with _GitHubClient(client) as github_client:
            branches = context.run(
                lambda: _github_json(
                    github_client,
                    f"/repos/{path}/branches",
                    token,
                    params={"per_page": GITHUB_MAX_BRANCHES, "page": 1},
                )
            )
        if not isinstance(branches, list):
            raise DatasourceProviderError("GITHUB_RESPONSE_INVALID")
        items = []
        for branch in branches[:GITHUB_MAX_BRANCHES]:
            name = branch.get("name") if isinstance(branch, dict) else None
            if isinstance(name, str) and name:
                items.append({"value": name[:255], "label": name[:255]})
        return {"resources": {"branches": {"items": items}}}

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
        """List repositories visible to a temporary Connection secret."""

        self.validate_connection(endpoint, connection_config)
        token = _secret_value(secret)
        page = _connection_resource_page(cursor)
        if not isinstance(limit, int) or isinstance(limit, bool):
            raise DatasourceProviderError(
                "connection resource limit is invalid"
            )
        limit = min(max(limit, 1), 100)
        if not isinstance(query, str) or len(query) > 100:
            raise DatasourceProviderError(
                "connection resource query is invalid"
            )
        context = request_context or PluginRequestContext(
            timeout_seconds=GITHUB_TIMEOUT_SECONDS,
        )
        with _GitHubClient(client) as github_client:
            payload = context.run(
                lambda: _github_json(
                    github_client,
                    "/user/repos",
                    token,
                    params={
                        "affiliation": (
                            "owner,collaborator,organization_member"
                        ),
                        "sort": "updated",
                        "direction": "desc",
                        "per_page": limit,
                        "page": page,
                    },
                )
            )
        if not isinstance(payload, list):
            raise DatasourceProviderError("GITHUB_RESPONSE_INVALID")
        items = []
        query_key = query.casefold().strip()
        for item in payload:
            name = item.get("full_name") if isinstance(item, dict) else None
            if not isinstance(name, str) or not name:
                continue
            repository = _repository_name(name)
            if query_key and query_key not in repository.casefold():
                continue
            items.append(
                {
                    "value": repository,
                    "label": repository,
                    "metadata": {
                        "private": bool(item.get("private")),
                        "updated_at": str(item.get("updated_at") or "")[:64],
                    },
                }
            )
        return {
            "resources": {"repositories": {"items": items}},
            "next_cursor": str(page + 1) if len(payload) == limit else "",
        }

    def validate_datasource_config(self, connection_scope, datasource_config):
        """Return a normalized repository selection within configured scope."""

        if not isinstance(datasource_config, dict):
            raise DatasourceProviderError(
                "datasource config must be an object"
            )
        unknown_keys = set(datasource_config).difference(ALLOWED_CONFIG_KEYS)
        if unknown_keys.intersection(SENSITIVE_CONFIG_KEYS):
            raise DatasourceProviderError(
                "datasource config cannot contain credentials"
            )
        if unknown_keys:
            raise DatasourceProviderError(
                "datasource config contains unsupported fields"
            )
        allowed_repositories = _allowed_repositories(connection_scope)
        raw_repositories = datasource_config.get("repositories")
        if raw_repositories is None:
            raw_repositories = [datasource_config.get("repository")]
        if (
            not isinstance(raw_repositories, list)
            or not raw_repositories
            or len(raw_repositories) > GITHUB_MAX_REPOSITORIES
        ):
            raise DatasourceProviderError(
                "repositories must contain 1 through 50 items"
            )
        repositories = [_repository_name(value) for value in raw_repositories]
        if len({item.casefold() for item in repositories}) != len(repositories):
            raise DatasourceProviderError("repositories must be unique")
        if any(item.casefold() not in allowed_repositories for item in repositories):
            raise DatasourceProviderError(
                "repository is outside connection scope"
            )
        normalized = {"repositories": repositories}
        if "repository" in datasource_config and "repositories" not in datasource_config:
            normalized = {"repository": repositories[0]}
        branch = _optional_nonempty_string(
            datasource_config.get("branch"), "branch"
        )
        if branch:
            normalized["branch"] = branch
        directory = _directory_path(datasource_config.get("directory"))
        if directory:
            normalized["directory"] = directory
        return normalized


def _allowed_repositories(connection_scope):
    """Return explicitly allowed V1 repositories from one Connection scope."""

    normalized = GitHubDatasourceProvider().validate_connection_scope(
        connection_scope
    )
    return {repository.casefold() for repository in normalized["repositories"]}


def _repository_name(value):
    """Validate and normalize an owner/repository identifier."""

    if not isinstance(value, str):
        raise DatasourceProviderError("repository is required")
    repository = value.strip().strip("/")
    parts = repository.split("/")
    if (
        len(parts) != 2
        or not all(parts)
        or any(len(part) > 100 for part in parts)
        or any(part in {".", ".."} for part in parts)
        or any(
            not all(
                character.isalnum() or character in {"-", "_", "."}
                for character in part
            )
            for part in parts
        )
    ):
        raise DatasourceProviderError("repository must use owner/repository")
    return repository


def _optional_nonempty_string(value, field_name):
    """Return a trimmed optional string field."""

    if value is None:
        return ""
    if not isinstance(value, str):
        raise DatasourceProviderError(
            f"{field_name} must be a non-empty string"
        )
    text = value.strip()
    if (
        not text
        or len(text) > GITHUB_MAX_BRANCH_LENGTH
        or any(ord(character) < 32 for character in text)
    ):
        raise DatasourceProviderError(f"{field_name} is invalid")
    return text


def _directory_path(value):
    """Return a safe repository-relative directory path."""

    if value is None or value == "":
        return ""
    if not isinstance(value, str):
        raise DatasourceProviderError("directory must be a string")
    text = value.strip()
    if len(text) > GITHUB_MAX_DIRECTORY_LENGTH or any(
        ord(character) < 32 for character in text
    ):
        raise DatasourceProviderError("directory is invalid")
    path = PurePosixPath(text)
    if (
        path.is_absolute()
        or ".." in path.parts
        or any(len(part) > 255 for part in path.parts)
    ):
        raise DatasourceProviderError("directory must be repository-relative")
    normalized = path.as_posix()
    return "" if normalized == "." else normalized


class _GitHubClient:
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


def _secret_value(value):
    """Return a non-empty secret without persisting or returning it."""

    if not isinstance(value, str) or not value:
        raise DatasourceProviderError("GITHUB_SECRET_UNAVAILABLE")
    return value


def _connection_resource_page(value):
    """Return a bounded GitHub repository-list page number."""

    if value in (None, ""):
        return 1
    if not isinstance(value, str) or not value.isdecimal():
        raise DatasourceProviderError("connection resource cursor is invalid")
    page = int(value)
    if not 1 <= page <= 100:
        raise DatasourceProviderError("connection resource cursor is invalid")
    return page


def _github_json(client, path, token, params=None):
    """Read bounded JSON from the fixed GitHub API host."""

    try:
        with client.stream(
            "GET",
            f"{GITHUB_API_URL}{path}",
            params=params,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": GITHUB_API_VERSION,
                "User-Agent": "SourceLens-Control-Plane",
            },
            follow_redirects=False,
        ) as response:
            if response.is_redirect:
                raise DatasourceProviderError("GITHUB_REDIRECT_REJECTED")
            if response.status_code >= 400:
                retry_after = (
                    retry_after_seconds(response.headers.get("Retry-After"))
                    if response.status_code == 429
                    else None
                )
                raise DatasourceProviderError(
                    _github_error(response.status_code),
                    retry_after=retry_after,
                )
            body = bytearray()
            for chunk in response.iter_bytes():
                if len(body) + len(chunk) > GITHUB_MAX_RESPONSE_BYTES:
                    raise DatasourceProviderError("GITHUB_RESPONSE_TOO_LARGE")
                body.extend(chunk)
    except DatasourceProviderError:
        raise
    except httpx.HTTPError as exc:
        raise DatasourceProviderError("GITHUB_REQUEST_FAILED") from exc
    try:
        return json.loads(body)
    except (UnicodeDecodeError, ValueError) as exc:
        raise DatasourceProviderError("GITHUB_RESPONSE_INVALID") from exc


def _github_error(status_code):
    """Map provider failures without exposing remote response content."""

    if status_code == 404:
        return "GITHUB_NOT_FOUND"
    if status_code in {401, 403}:
        return "GITHUB_ACCESS_DENIED"
    if status_code == 429:
        return "GITHUB_RATE_LIMITED"
    return "GITHUB_REQUEST_FAILED"


class GitHubToolProvider:
    """Validate bounded read-only GitHub Tool requests."""

    key = "github"

    def validate_request(self, endpoint, allowed_scope, tool_key, arguments):
        """Return canonical endpoint and authorized arguments."""

        if tool_key not in GITHUB_TOOL_KEYS:
            raise ToolProviderError("tool is unsupported")
        try:
            endpoint = GitHubDatasourceProvider().validate_connection(
                endpoint,
                {},
            )
            allowed = _allowed_repositories(allowed_scope)
            if tool_key == "github_activity_summary":
                if not isinstance(arguments, dict):
                    raise DatasourceProviderError(
                        "arguments must be an object"
                    )
                repositories = arguments.get("repositories")
                if (
                    not isinstance(repositories, list)
                    or not repositories
                    or len(repositories) > GITHUB_MAX_REPOSITORIES
                ):
                    raise DatasourceProviderError(
                        "repositories must contain 1 through 50 items"
                    )
                normalized_repositories = [
                    _repository_name(repository)
                    for repository in repositories
                ]
                identities = {
                    repository.casefold()
                    for repository in normalized_repositories
                }
                if len(identities) != len(normalized_repositories):
                    raise DatasourceProviderError(
                        "repositories must be unique"
                    )
                if not identities.issubset(allowed):
                    raise DatasourceProviderError(
                        "repository is outside connection scope"
                    )
                since = _tool_timestamp(arguments.get("since"), "since")
                until = _tool_timestamp(arguments.get("until"), "until")
                if since[1] > until[1]:
                    raise DatasourceProviderError(
                        "since must not be after until"
                    )
                return endpoint, {
                    "repositories": normalized_repositories,
                    "since": since[0],
                    "until": until[0],
                    "per_page": _tool_integer(
                        arguments.get("per_page"),
                        "per_page",
                        1,
                        100,
                        50,
                    ),
                }
            repository = _repository_name(
                arguments.get("repository")
                if isinstance(arguments, dict)
                else None
            )
        except DatasourceProviderError as exc:
            raise ToolProviderError(str(exc)) from exc
        if repository.casefold() not in allowed:
            raise ToolProviderError("repository is outside connection scope")
        normalized = {"repository": repository}
        if tool_key == "github_read_file":
            normalized["path"] = _tool_path(
                arguments.get("path"),
                required=True,
            )
            ref = _tool_text(arguments.get("ref"), "ref", 255)
            if ref:
                normalized["ref"] = ref
            return endpoint, normalized
        if tool_key == "github_search_code":
            query = _tool_text(
                arguments.get("query"),
                "query",
                1024,
                required=True,
            )
            if SEARCH_SCOPE_PATTERN.search(query):
                raise ToolProviderError(
                    "query scope qualifiers are not allowed"
                )
            normalized["query"] = query
            normalized["max_results"] = _tool_max_results(
                arguments.get("max_results")
            )
            path = _tool_path(arguments.get("path"), required=False)
            if path and (
                any(character.isspace() for character in path) or '"' in path
            ):
                raise ToolProviderError("search path is invalid")
            if path:
                normalized["path"] = path
            return endpoint, normalized
        if tool_key in PAGINATED_TOOL_KEYS:
            normalized.update(_tool_pagination(arguments))
        if tool_key in NUMBERED_TOOL_KEYS:
            normalized["number"] = _tool_positive_integer(
                arguments.get("number"),
                "number",
            )
        if tool_key == "github_commit_list":
            ref = _tool_text(arguments.get("ref"), "ref", 255)
            path = _tool_path(arguments.get("path"), required=False)
            if ref:
                normalized["ref"] = ref
            if path:
                normalized["path"] = path
            for name in ("since", "until"):
                value = _tool_text(arguments.get(name), name, 64)
                if value:
                    normalized[name] = value
        elif tool_key == "github_commit_get":
            normalized["ref"] = _tool_text(
                arguments.get("ref"),
                "ref",
                255,
                required=True,
            )
        elif tool_key == "github_issue_list":
            normalized["state"] = _tool_choice(
                arguments.get("state"),
                "state",
                {"open", "closed", "all"},
                "open",
            )
            labels = _tool_text(arguments.get("labels"), "labels", 500)
            if labels:
                normalized["labels"] = labels
            for name in ("since", "until"):
                value = _tool_text(arguments.get(name), name, 64)
                if value:
                    normalized[name] = value
        elif tool_key == "github_pull_request_list":
            normalized["state"] = _tool_choice(
                arguments.get("state"),
                "state",
                {"open", "closed", "all"},
                "open",
            )
        elif tool_key == "github_workflow_run_list":
            status = _tool_choice(
                arguments.get("status"),
                "status",
                WORKFLOW_STATUSES,
                "",
            )
            branch = _tool_text(arguments.get("branch"), "branch", 255)
            if status:
                normalized["status"] = status
            if branch:
                normalized["branch"] = branch
        elif tool_key == "github_workflow_run_get":
            normalized["run_id"] = _tool_positive_integer(
                arguments.get("run_id"),
                "run_id",
            )
        return endpoint, normalized


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


def _tool_timestamp(value, field):
    """Return one timezone-aware ISO-8601 Tool timestamp and parsed value."""

    text = _tool_text(value, field, 64, required=True)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ToolProviderError(f"{field} is invalid") from exc
    if parsed.tzinfo is None:
        raise ToolProviderError(f"{field} is invalid")
    return text, parsed


def _tool_path(value, required):
    """Return a bounded repository-relative Tool path."""

    text = _tool_text(value, "path", 4096, required=required)
    if not text:
        return ""
    path = PurePosixPath(text)
    if path.is_absolute() or ".." in path.parts:
        raise ToolProviderError("path must be repository-relative")
    normalized = path.as_posix()
    if normalized in {"", "."} and required:
        raise ToolProviderError("path is required")
    return "" if normalized == "." else normalized


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


def _tool_pagination(arguments):
    """Return bounded one-based pagination for GitHub list Tools."""

    return {
        "page": _tool_integer(arguments.get("page"), "page", 1, 1000, 1),
        "per_page": _tool_integer(
            arguments.get("per_page"),
            "per_page",
            1,
            50,
            20,
        ),
    }


def _tool_positive_integer(value, field):
    """Return a required positive integer Tool argument."""

    return _tool_integer(value, field, 1, 2**63 - 1, None)


def _tool_integer(value, field, minimum, maximum, default):
    """Return one bounded integer Tool argument."""

    if value is None and default is not None:
        return default
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < minimum
        or value > maximum
    ):
        raise ToolProviderError(f"{field} is invalid")
    return value


def _tool_choice(value, field, choices, default):
    """Return a normalized enum-like Tool argument."""

    if value in (None, ""):
        return default
    text = _tool_text(value, field, 64, required=True).lower()
    if text not in choices:
        raise ToolProviderError(f"{field} is invalid")
    return text


DATASOURCE_PROVIDER = GitHubDatasourceProvider()
TOOL_PROVIDER = GitHubToolProvider()
