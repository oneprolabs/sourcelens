"""GitHub LensNode runtime entrypoint."""

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import PurePosixPath
from urllib.parse import quote, urlsplit

import httpx
from langchain.tools import ToolRuntime, tool

from lensnode.plugin_runtime import PluginRuntimeError


PLUGIN_API_VERSION = 1
PLUGIN_KEY = "github"
PLUGIN_VERSION = "1.0.0"
TOOL_METADATA = {"github_activity_summary": {"activity_report": True}}
API_URL = "https://api.github.com"
API_VERSION = "2022-11-28"
READ_MAX_BYTES = 200_000
SEARCH_MAX_BYTES = 1_000_000
JSON_MAX_BYTES = 1_000_000
BODY_MAX_CHARS = 12_000
GET_MAX_ATTEMPTS = 3
GET_RETRY_DELAY_SECONDS = 0.25
GET_MAX_RETRY_WAIT_SECONDS = 65.0
ACTIVITY_DEFAULT_PER_PAGE = 50
ACTIVITY_MAX_PAGES = 10
ACTIVITY_PULL_REQUEST_PER_PAGE = 10
ACTIVITY_PULL_REQUEST_MAX_PAGES = 50
ACTIVITY_MAX_REPOSITORIES = 500
ACTIVITY_REPOSITORY_BATCH_SIZE = 50
ACTIVITY_DISCOVERY_MAX_PAGES = 20
ACTIVITY_DISCOVERY_PAGE_SIZE = 100
ACTIVITY_RESOURCE_MAX = {
    "commits": 500,
    "pull_requests": 500,
    "issues": 500,
}
TOOL_KEYS = frozenset(
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


def http_origins(endpoint):
    """Return fixed HTTPS origins used by the GitHub REST runtime."""

    _endpoint(endpoint)
    return (API_URL,)


def build_tool(definition, executor):
    """Create a fixed GitHub read-only tool from its manifest declaration."""

    _validate_definition(definition)
    key = definition["key"]
    description = definition["description"].strip()

    def invoke(runtime: ToolRuntime, **arguments) -> str:
        return executor(key, arguments, runtime)

    return tool(
        key,
        description=description,
        args_schema=definition["input_schema"],
    )(invoke)


def execute_tool(key, client, arguments, secret, endpoint, config):
    """Execute one bounded GitHub REST request."""

    _endpoint(endpoint)
    if key not in TOOL_KEYS:
        raise PluginRuntimeError("PLUGIN_TOOL_UNSUPPORTED")
    _validate_runtime_arguments(key, arguments, config)
    if key == "github_activity_summary":
        return _activity_summary(client, arguments, secret, config)
    if key == "github_read_file":
        return _read_file(client, arguments, secret)
    if key == "github_search_code":
        if not secret:
            raise PluginRuntimeError("GITHUB_AUTHENTICATION_REQUIRED")
        return _search_code(client, arguments, secret)
    repository = _text(arguments, "repository")
    repository_path = quote(repository, safe="/")
    if key == "github_repository_get":
        payload = _json_get(
            client,
            f"/repos/{repository_path}",
            secret,
        )
        return _repository_result(repository, payload)
    if key == "github_branch_list":
        payload = _json_get(
            client,
            f"/repos/{repository_path}/branches",
            secret,
            _pagination(arguments),
        )
        return _list_result(
            repository,
            arguments,
            payload,
            _branch_result,
        )
    if key == "github_commit_list":
        params = _pagination(arguments)
        _add_time_filters(params, arguments)
        if arguments.get("ref"):
            params["sha"] = arguments["ref"]
        if arguments.get("path"):
            params["path"] = arguments["path"]
        payload = _json_get(
            client,
            f"/repos/{repository_path}/commits",
            secret,
            params,
        )
        return _list_result(
            repository,
            arguments,
            payload,
            _commit_summary,
        )
    if key == "github_commit_get":
        ref = quote(_text(arguments, "ref"), safe="")
        payload = _json_get(
            client,
            f"/repos/{repository_path}/commits/{ref}",
            secret,
        )
        return _commit_result(repository, payload)
    if key == "github_issue_list":
        params = _pagination(arguments)
        if arguments.get("since"):
            params["since"] = _bounded_text(arguments["since"], 64)
        params["state"] = str(arguments.get("state") or "open")
        if arguments.get("labels"):
            params["labels"] = arguments["labels"]
        payload = _json_get(
            client,
            f"/repos/{repository_path}/issues",
            secret,
            params,
        )
        issues = [
            item
            for item in _items(payload)
            if not isinstance(item.get("pull_request"), dict)
        ]
        return _list_result(
            repository,
            arguments,
            issues,
            _issue_summary,
            raw_count=len(_items(payload)),
        )
    if key == "github_issue_get":
        number = _positive_integer(arguments, "number")
        payload = _json_get(
            client,
            f"/repos/{repository_path}/issues/{number}",
            secret,
        )
        return _issue_result(repository, payload)
    if key == "github_issue_comments":
        return _paged_resource(
            client,
            repository,
            repository_path,
            f"issues/{_positive_integer(arguments, 'number')}/comments",
            arguments,
            secret,
            _comment_result,
        )
    if key == "github_pull_request_list":
        params = _pagination(arguments)
        params["state"] = str(arguments.get("state") or "open")
        payload = _json_get(
            client,
            f"/repos/{repository_path}/pulls",
            secret,
            params,
        )
        return _list_result(
            repository,
            arguments,
            payload,
            _pull_request_summary,
        )
    if key == "github_pull_request_get":
        number = _positive_integer(arguments, "number")
        payload = _json_get(
            client,
            f"/repos/{repository_path}/pulls/{number}",
            secret,
        )
        return _pull_request_result(repository, payload)
    if key == "github_pull_request_files":
        return _paged_resource(
            client,
            repository,
            repository_path,
            f"pulls/{_positive_integer(arguments, 'number')}/files",
            arguments,
            secret,
            _file_result,
        )
    if key == "github_pull_request_reviews":
        return _paged_resource(
            client,
            repository,
            repository_path,
            f"pulls/{_positive_integer(arguments, 'number')}/reviews",
            arguments,
            secret,
            _review_result,
        )
    if key == "github_release_list":
        return _paged_resource(
            client,
            repository,
            repository_path,
            "releases",
            arguments,
            secret,
            _release_result,
        )
    if key == "github_workflow_run_list":
        params = _pagination(arguments)
        for name in ("status", "branch"):
            if arguments.get(name):
                params[name] = arguments[name]
        payload = _json_get(
            client,
            f"/repos/{repository_path}/actions/runs",
            secret,
            params,
        )
        if not isinstance(payload, dict):
            raise PluginRuntimeError("GITHUB_RESPONSE_INVALID")
        return _list_result(
            repository,
            arguments,
            payload.get("workflow_runs"),
            _workflow_run_result,
        )
    if key == "github_workflow_run_get":
        run_id = _positive_integer(arguments, "run_id")
        payload = _json_get(
            client,
            f"/repos/{repository_path}/actions/runs/{run_id}",
            secret,
        )
        result = _workflow_run_result(payload)
        result.update({"ok": True, "repository": repository})
        return result
    raise PluginRuntimeError("PLUGIN_TOOL_UNSUPPORTED")


def _activity_summary(client, arguments, secret, config):
    """Return compact activity from approved repositories in one Tool call."""

    repositories, coverage = _resolve_activity_repositories(client, arguments["repositories"], secret, config)
    since = arguments["since"]
    until = arguments["until"]
    per_page = _page_value(
        arguments,
        "per_page",
        1,
        100,
        ACTIVITY_DEFAULT_PER_PAGE,
    )
    results = {
        repository: {
            "repository": repository,
            "commits": [],
            "pull_requests": [],
            "issues": [],
            "possibly_truncated": {},
        }
        for repository in repositories
    }
    for offset in range(0, len(repositories), ACTIVITY_REPOSITORY_BATCH_SIZE):
        batch = repositories[offset : offset + ACTIVITY_REPOSITORY_BATCH_SIZE]
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {
                executor.submit(_activity_resource, client, repository, resource, since, until, per_page, secret): (
                    repository,
                    resource,
                )
                for repository in batch
                for resource in ("commits", "pull_requests", "issues")
            }
            for future in as_completed(futures):
                repository, resource = futures[future]
                try:
                    items, possibly_truncated = future.result()
                except (PluginRuntimeError, httpx.HTTPError) as exc:
                    error = str(exc) if isinstance(exc, PluginRuntimeError) else "GITHUB_REQUEST_FAILED"
                    results[repository].setdefault("errors", {})[resource] = error
                    continue
                results[repository][resource] = items
                results[repository]["possibly_truncated"][resource] = possibly_truncated
    source_complete = not coverage["repositories_truncated"] and all(
        not result.get("errors") and not any(result["possibly_truncated"].values()) for result in results.values()
    )
    return {
        "ok": True,
        "since": since,
        "until": until,
        "limits": {resource: maximum for resource, maximum in ACTIVITY_RESOURCE_MAX.items()},
        "repositories": [results[repository] for repository in repositories],
        "repositories_truncated": coverage["repositories_truncated"],
        "source_coverage_complete": source_complete,
        "coverage": coverage,
    }


def _activity_selector(value):
    """Normalize one exact, owner-wide or connection-wide repository selector."""

    if isinstance(value, str):
        value = value.strip().strip("/")
        if value == "*":
            return value
        if value.endswith("/*") and _repository_identity(f"{value[:-2]}/selector"):
            return value
    repository = _repository_identity(value)
    if repository is None:
        raise PluginRuntimeError("PLUGIN_ARGUMENTS_INVALID")
    return repository


def _selector_matches(selector, repository):
    """Match only a whole owner or exact repository, case-insensitively."""

    return (
        selector == "*"
        or selector.casefold() == repository.casefold()
        or (selector.endswith("/*") and selector[:-2].casefold() == repository.split("/")[0].casefold())
    )


def _discover_activity_pages(client, selector, secret):
    """Yield validated repository pages within a shared per-selector budget."""

    owner = quote(selector[:-2], safe="")
    org_path = f"/orgs/{owner}/repos"
    paths = ["/user/repos" if selector == "*" else org_path]
    pages_read = 0
    for path in paths:
        seen = set()
        page = 1
        while True:
            if pages_read >= ACTIVITY_DISCOVERY_MAX_PAGES:
                raise PluginRuntimeError("GITHUB_DISCOVERY_PAGE_LIMIT")
            params = {"per_page": ACTIVITY_DISCOVERY_PAGE_SIZE, "page": page, "sort": "full_name"}
            if path == "/user/repos":
                params["affiliation"] = "owner,collaborator,organization_member"
            try:
                payload = _json_get(client, path, secret, params)
            except PluginRuntimeError as exc:
                if path == org_path and page == 1 and str(exc) == "GITHUB_NOT_FOUND":
                    paths.append(f"/users/{owner}/repos")
                    if secret:
                        paths.append("/user/repos")
                    break
                raise
            pages_read += 1
            items = _items(payload)
            repositories = [_repository_identity(item.get("full_name")) for item in items]
            if any(repository is None for repository in repositories):
                raise PluginRuntimeError("GITHUB_RESPONSE_INVALID")
            yield repositories
            if len(items) < ACTIVITY_DISCOVERY_PAGE_SIZE:
                break
            identities = {repository.casefold() for repository in repositories}
            if not identities.difference(seen):
                raise PluginRuntimeError("GITHUB_DISCOVERY_STALLED")
            seen.update(identities)
            page += 1


def _resolve_activity_repositories(client, selectors, secret, config):
    """Expand selectors inside the frozen scope and disclose bounded discovery."""

    selectors = [_activity_selector(value) for value in selectors]
    scope = _runtime_scope(config).get("repositories")
    if not isinstance(scope, list) or not scope:
        raise PluginRuntimeError("PLUGIN_SCOPE_MISMATCH")
    unrestricted = scope == ["*"]
    if not unrestricted and any(_repository_identity(value) is None for value in scope):
        raise PluginRuntimeError("PLUGIN_SCOPE_MISMATCH")
    if unrestricted and "*" in selectors and not secret:
        raise PluginRuntimeError(
            "GITHUB_AUTHENTICATION_REQUIRED",
            reason="OWNER_SELECTOR_REQUIRED",
            hint="Anonymous access with an all-repositories Connection requires owner/* or explicit repositories.",
        )
    coverage = {
        "requested_selectors": selectors,
        "resolved_repositories": [],
        "discovery_complete": True,
        "repositories_truncated": False,
        "reasons": [],
        "discovery_errors": [],
        "limits": {
            "repositories": ACTIVITY_MAX_REPOSITORIES,
            "discovery_pages_per_selector": ACTIVITY_DISCOVERY_MAX_PAGES,
        },
    }
    repositories = {}

    def add(repository):
        """Keep a bounded, deterministic set of approved repository identities."""

        _validate_repository_scope(repository, _runtime_scope(config))
        identity = repository.casefold()
        if identity not in repositories:
            if len(repositories) >= ACTIVITY_MAX_REPOSITORIES:
                coverage["repositories_truncated"] = True
                if "repository_limit" not in coverage["reasons"]:
                    coverage["reasons"].append("repository_limit")
                return False
            repositories[identity] = repository
        return True

    for selector in selectors:
        if "*" not in selector:
            add(selector)
    if not unrestricted:
        for repository in scope:
            repository = _repository_identity(repository)
            if any(_selector_matches(selector, repository) for selector in selectors):
                add(repository)
    else:
        discovery_selectors = list(dict.fromkeys(selector for selector in selectors if "*" in selector))
        for selector in discovery_selectors:
            try:
                for discovered in _discover_activity_pages(client, selector, secret):
                    for repository in discovered:
                        if _selector_matches(selector, repository) and not add(repository):
                            break
                    if "repository_limit" in coverage["reasons"]:
                        coverage["discovery_complete"] = False
                        break
            except (PluginRuntimeError, httpx.HTTPError) as exc:
                error = str(exc) if isinstance(exc, PluginRuntimeError) else "GITHUB_REQUEST_FAILED"
                reason = {
                    "GITHUB_DISCOVERY_PAGE_LIMIT": "discovery_page_limit",
                    "GITHUB_DISCOVERY_STALLED": "discovery_stalled",
                }.get(error, "discovery_failed")
                coverage["reasons"].append(reason)
                coverage["discovery_complete"] = False
                if reason == "discovery_failed":
                    coverage["discovery_errors"].append({"selector": selector, "error": error})
            if "repository_limit" in coverage["reasons"]:
                break
    coverage["repositories_truncated"] |= not coverage["discovery_complete"]
    coverage["reasons"] = list(dict.fromkeys(coverage["reasons"]))
    coverage["resolved_repositories"] = list(repositories.values())
    return coverage["resolved_repositories"], coverage


def _activity_resource(
    client,
    repository,
    resource,
    since,
    until,
    per_page,
    secret,
):
    """Read and project one activity resource within the requested window."""

    repository_path = quote(repository, safe="/")
    page_size = min(per_page, ACTIVITY_RESOURCE_MAX[resource])
    max_pages = ACTIVITY_MAX_PAGES
    if resource == "pull_requests":
        page_size = min(page_size, ACTIVITY_PULL_REQUEST_PER_PAGE)
        max_pages = ACTIVITY_PULL_REQUEST_MAX_PAGES
    if resource == "issues":
        return _activity_issues(
            client,
            repository,
            since,
            until,
            page_size,
            secret,
        )
    params = {"page": 1, "per_page": page_size}
    if resource == "commits":
        params.update({"since": since, "until": until})
        path = f"/repos/{repository_path}/commits"
        projector = _activity_commit_summary
    elif resource == "pull_requests":
        params.update(
            {
                "state": "all",
                "sort": "updated",
                "direction": "desc",
            }
        )
        path = f"/repos/{repository_path}/pulls"
        projector = _activity_pull_request_summary
    items = []
    seen = set()
    result_limit = ACTIVITY_RESOURCE_MAX[resource]
    for page in range(1, max_pages + 1):
        params["page"] = page
        try:
            raw_payload = _items(_json_get(client, path, secret, params))
        except (PluginRuntimeError, httpx.HTTPError):
            if page == 1:
                raise
            return items[:result_limit], True
        new_count = 0
        for item in raw_payload:
            identity = item.get("sha") if resource == "commits" else item.get("number")
            if identity in seen:
                continue
            seen.add(identity)
            new_count += 1
            in_window = _timestamp_in_window(
                (
                    (_nested(item, "commit", "committer", "date") or _nested(item, "commit", "author", "date"))
                    if resource == "commits"
                    else item.get("updated_at")
                ),
                since,
                until,
            )
            if resource == "pull_requests":
                in_window = in_window or any(
                    _timestamp_in_window(item.get(field), since, until)
                    for field in ("created_at", "merged_at", "closed_at")
                )
            if in_window:
                items.append(projector(item))
        if not _activity_possibly_truncated(raw_payload, resource, since, page_size):
            return items[:result_limit], len(items) > result_limit
        if len(items) >= result_limit or not new_count:
            return items[:result_limit], True
    return items[:result_limit], True


def _activity_issues(
    client,
    repository,
    since,
    until,
    page_size,
    secret,
):
    """Search only issues so pull requests cannot consume the result limit."""

    items = []
    seen = set()
    incomplete = False
    result_limit = ACTIVITY_RESOURCE_MAX["issues"]
    for field in ("updated", "created", "closed"):
        query_seen = set()
        query = f"repo:{repository} is:issue {field}:{since}..{until}"
        for page in range(1, ACTIVITY_MAX_PAGES + 1):
            partial_payload = None
            try:
                for attempt in range(GET_MAX_ATTEMPTS):
                    try:
                        payload = _json_get(
                            client,
                            "/search/issues",
                            secret,
                            {"q": query, "sort": "updated", "direction": "desc", "page": page, "per_page": page_size},
                            max_bytes=SEARCH_MAX_BYTES,
                        )
                        if (
                            not isinstance(payload, dict)
                            or type(payload.get("total_count")) is not int
                            or payload["total_count"] < 0
                            or not isinstance(payload.get("incomplete_results"), bool)
                        ):
                            raise PluginRuntimeError("GITHUB_RESPONSE_INVALID")
                        _items(payload.get("items"))
                    except (PluginRuntimeError, httpx.HTTPError):
                        if partial_payload is None:
                            raise
                        payload = partial_payload
                        break
                    if not payload["incomplete_results"]:
                        break
                    partial_payload = payload
                    if attempt + 1 < GET_MAX_ATTEMPTS:
                        time.sleep(GET_RETRY_DELAY_SECONDS * (attempt + 1))
            except (PluginRuntimeError, httpx.HTTPError) as exc:
                if isinstance(exc, PluginRuntimeError) and str(exc) == "GITHUB_ACCESS_DENIED":
                    return items[:result_limit], incomplete
                if field == "updated" and page == 1:
                    raise
                return items[:result_limit], True
            raw_items = _items(payload.get("items"))
            incomplete = incomplete or payload["incomplete_results"]
            new_count = 0
            for item in raw_items:
                identity = item.get("number")
                if identity not in query_seen:
                    query_seen.add(identity)
                    new_count += 1
                if identity in seen:
                    continue
                seen.add(identity)
                if any(
                    _timestamp_in_window(item.get(stamp), since, until)
                    for stamp in ("updated_at", "created_at", "closed_at")
                ):
                    items.append(_activity_issue_summary(item))
            if len(query_seen) >= payload["total_count"]:
                break
            if len(items) >= result_limit or not new_count:
                incomplete = True
                break
        else:
            incomplete = True
        if len(items) >= result_limit:
            return items[:result_limit], True
    return items, incomplete


def _activity_possibly_truncated(payload, resource, since, per_page):
    """Return whether another page could contain in-window activity."""

    if len(payload) < per_page:
        return False
    if resource != "pull_requests":
        return True
    oldest_updated_at = payload[-1].get("updated_at")
    if not isinstance(oldest_updated_at, str):
        return True
    try:
        return _timestamp(oldest_updated_at) >= _timestamp(since)
    except PluginRuntimeError:
        return True


def _activity_commit_summary(payload):
    """Project one compact commit for an activity report."""

    item = _commit_summary(payload)
    item["message"] = _string(item["message"], 300)
    item["committed_at"] = _string(
        _nested(payload, "commit", "committer", "date") or _nested(payload, "commit", "author", "date"),
        64,
    )
    return item


def _activity_pull_request_summary(payload):
    """Project one compact pull request for an activity report."""

    _object(payload)
    return {
        "number": _integer(payload.get("number")),
        "title": _string(payload.get("title"), 300),
        "state": _string(payload.get("state"), 32),
        "draft": bool(payload.get("draft")),
        "author": _login(payload.get("user")),
        "assignees": [{"login": _login(value)} for value in (payload.get("assignees") or [])[:20]
                      if isinstance(value, dict)],
        "created_at": _string(payload.get("created_at"), 64),
        "updated_at": _string(payload.get("updated_at"), 64),
        "closed_at": _string(payload.get("closed_at"), 64),
        "merged_at": _string(payload.get("merged_at"), 64),
        "merged_by": _login(payload.get("merged_by") or payload.get("merged_by_user")),
        "url": _string(payload.get("html_url"), 500),
    }


def _activity_issue_summary(payload):
    """Project one compact issue for an activity report."""

    _object(payload)
    return {
        "number": _integer(payload.get("number")),
        "title": _string(payload.get("title"), 300),
        "state": _string(payload.get("state"), 32),
        "author": _login(payload.get("user")),
        "assignees": [{"login": _login(value)} for value in (payload.get("assignees") or [])[:20]
                      if isinstance(value, dict)],
        "labels": _labels(payload.get("labels")),
        "created_at": _string(payload.get("created_at"), 64),
        "updated_at": _string(payload.get("updated_at"), 64),
        "closed_at": _string(payload.get("closed_at"), 64),
        "closed_by": _login(payload.get("closed_by") or payload.get("closed_by_user")),
        "url": _string(payload.get("html_url"), 500),
    }


def _read_file(client, arguments, secret):
    """Read one bounded UTF-8 repository file."""

    repository = _text(arguments, "repository")
    path = _text(arguments, "path")
    ref = str(arguments.get("ref") or "")
    status, body, truncated = _get(
        client,
        f"{API_URL}/repos/{quote(repository, safe='/')}/contents/"
        f"{quote(path, safe='/')}",
        secret,
        {"ref": ref} if ref else None,
        "application/vnd.github.raw+json",
        READ_MAX_BYTES,
        True,
    )
    _status(status)
    if b"\x00" in body:
        raise PluginRuntimeError("GITHUB_FILE_NOT_TEXT")
    return {
        "ok": True,
        "repository": repository,
        "path": path,
        "ref": ref,
        "content": body.decode("utf-8", "replace"),
        "truncated": truncated,
    }


def _search_code(client, arguments, secret):
    """Search one approved repository and return safe file identities."""

    repository = _text(arguments, "repository")
    query = _text(arguments, "query")
    if any(item in query.lower() for item in ("repo:", "org:", "user:")):
        raise PluginRuntimeError("GITHUB_SEARCH_SCOPE_INVALID")
    path = str(arguments.get("path") or "")
    max_results = arguments.get("max_results", 10)
    if (
        isinstance(max_results, bool)
        or not isinstance(max_results, int)
        or not 1 <= max_results <= 20
    ):
        raise PluginRuntimeError("PLUGIN_ARGUMENTS_INVALID")
    qualifiers = [query, f"repo:{repository}"]
    if path:
        qualifiers.append(f"path:{path}")
    payload = _json_get(
        client,
        "/search/code",
        secret,
        {"q": " ".join(qualifiers), "per_page": max_results, "page": 1},
        max_bytes=SEARCH_MAX_BYTES,
    )
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise PluginRuntimeError("GITHUB_RESPONSE_INVALID")
    return {
        "ok": True,
        "repository": repository,
        "query": query,
        "path": path,
        "total_count": max(_integer(payload.get("total_count")), 0),
        "items": [
            {
                "name": _string(item.get("name"), 255),
                "path": _string(item.get("path"), 4096),
                "sha": _string(item.get("sha"), 128),
            }
            for item in items[:max_results]
            if isinstance(item, dict)
        ],
    }


def _paged_resource(
    client,
    repository,
    repository_path,
    resource_path,
    arguments,
    secret,
    projector,
):
    """Read and project one fixed paginated repository resource."""

    payload = _json_get(
        client,
        f"/repos/{repository_path}/{resource_path}",
        secret,
        _pagination(arguments),
    )
    return _list_result(repository, arguments, payload, projector)


def _list_result(
    repository,
    arguments,
    payload,
    projector,
    raw_count=None,
):
    """Return one stable bounded list result."""

    items = _items(payload)
    page = _page_value(arguments, "page", 1, 1000, 1)
    per_page = _page_value(arguments, "per_page", 1, 20, 10)
    return {
        "ok": True,
        "repository": repository,
        "page": page,
        "per_page": per_page,
        "has_more": (len(items) if raw_count is None else raw_count)
        == per_page,
        "items": [projector(item) for item in items[:per_page]],
    }


def _repository_result(repository, payload):
    """Project one repository response onto stable safe fields."""

    _object(payload)
    return {
        "ok": True,
        "repository": _string(payload.get("full_name"), 201) or repository,
        "description": _string(payload.get("description"), 1000),
        "private": bool(payload.get("private")),
        "default_branch": _string(payload.get("default_branch"), 255),
        "archived": bool(payload.get("archived")),
        "fork": bool(payload.get("fork")),
        "language": _string(payload.get("language"), 100),
        "stars": _integer(payload.get("stargazers_count")),
        "forks": _integer(payload.get("forks_count")),
        "open_issues": _integer(payload.get("open_issues_count")),
        "updated_at": _string(payload.get("updated_at"), 64),
        "url": _string(payload.get("html_url"), 500),
        "owner": _login(payload.get("owner")),
    }


def _branch_result(payload):
    """Project one branch response."""

    _object(payload)
    return {
        "name": _string(payload.get("name"), 255),
        "protected": bool(payload.get("protected")),
        "sha": _string(_nested(payload, "commit", "sha"), 128),
    }


def _commit_summary(payload):
    """Project one commit list item."""

    _object(payload)
    message = _string(_nested(payload, "commit", "message"), 1000)
    return {
        "sha": _string(payload.get("sha"), 128),
        "message": message.splitlines()[0] if message else "",
        "author": _string(_nested(payload, "commit", "author", "name"), 255),
        "login": _login(payload.get("author")),
        "authored_at": _string(
            _nested(payload, "commit", "author", "date"),
            64,
        ),
        "url": _string(payload.get("html_url"), 500),
    }


def _commit_result(repository, payload):
    """Project one commit detail without patch content."""

    _object(payload)
    stats = payload.get("stats")
    files = payload.get("files")
    if not isinstance(stats, dict):
        stats = {}
    if not isinstance(files, list):
        files = []
    return {
        "ok": True,
        "repository": repository,
        "sha": _string(payload.get("sha"), 128),
        "message": _string(
            _nested(payload, "commit", "message"),
            BODY_MAX_CHARS,
        ),
        "author": _string(_nested(payload, "commit", "author", "name"), 255),
        "login": _login(payload.get("author")),
        "authored_at": _string(
            _nested(payload, "commit", "author", "date"),
            64,
        ),
        "committer": _string(
            _nested(payload, "commit", "committer", "name"),
            255,
        ),
        "committed_at": _string(
            _nested(payload, "commit", "committer", "date"),
            64,
        ),
        "url": _string(payload.get("html_url"), 500),
        "stats": {
            "additions": _integer(stats.get("additions")),
            "deletions": _integer(stats.get("deletions")),
            "total": _integer(stats.get("total")),
        },
        "files": [
            _file_result(item)
            for item in files[:50]
            if isinstance(item, dict)
        ],
    }


def _issue_summary(payload):
    """Project one issue list item."""

    _object(payload)
    return {
        "number": _integer(payload.get("number")),
        "title": _string(payload.get("title"), 1000),
        "state": _string(payload.get("state"), 32),
        "author": _login(payload.get("user")),
        "labels": _labels(payload.get("labels")),
        "comments": _integer(payload.get("comments")),
        "created_at": _string(payload.get("created_at"), 64),
        "updated_at": _string(payload.get("updated_at"), 64),
        "url": _string(payload.get("html_url"), 500),
    }


def _issue_result(repository, payload):
    """Project one issue detail."""

    result = _issue_summary(payload)
    result.update(
        {
            "ok": True,
            "repository": repository,
            "body": _string(payload.get("body"), BODY_MAX_CHARS),
            "closed_at": _string(payload.get("closed_at"), 64),
        }
    )
    return result


def _comment_result(payload):
    """Project one issue comment."""

    _object(payload)
    return {
        "id": _integer(payload.get("id")),
        "author": _login(payload.get("user")),
        "body": _string(payload.get("body"), BODY_MAX_CHARS),
        "created_at": _string(payload.get("created_at"), 64),
        "updated_at": _string(payload.get("updated_at"), 64),
        "url": _string(payload.get("html_url"), 500),
    }


def _pull_request_summary(payload):
    """Project one pull request list item."""

    _object(payload)
    return {
        "number": _integer(payload.get("number")),
        "title": _string(payload.get("title"), 1000),
        "state": _string(payload.get("state"), 32),
        "draft": bool(payload.get("draft")),
        "author": _login(payload.get("user")),
        "head_ref": _string(_nested(payload, "head", "ref"), 255),
        "head_sha": _string(_nested(payload, "head", "sha"), 128),
        "base_ref": _string(_nested(payload, "base", "ref"), 255),
        "base_sha": _string(_nested(payload, "base", "sha"), 128),
        "created_at": _string(payload.get("created_at"), 64),
        "updated_at": _string(payload.get("updated_at"), 64),
        "url": _string(payload.get("html_url"), 500),
    }


def _pull_request_result(repository, payload):
    """Project one pull request detail."""

    result = _pull_request_summary(payload)
    result.update(
        {
            "ok": True,
            "repository": repository,
            "body": _string(payload.get("body"), BODY_MAX_CHARS),
            "merged": bool(payload.get("merged")),
            "mergeable": payload.get("mergeable")
            if isinstance(payload.get("mergeable"), bool)
            else None,
            "commits": _integer(payload.get("commits")),
            "additions": _integer(payload.get("additions")),
            "deletions": _integer(payload.get("deletions")),
            "changed_files": _integer(payload.get("changed_files")),
            "comments": _integer(payload.get("comments")),
            "review_comments": _integer(payload.get("review_comments")),
            "merged_at": _string(payload.get("merged_at"), 64),
            "closed_at": _string(payload.get("closed_at"), 64),
        }
    )
    return result


def _file_result(payload):
    """Project one changed-file item without patch content."""

    _object(payload)
    return {
        "path": _string(payload.get("filename"), 4096),
        "status": _string(payload.get("status"), 32),
        "additions": _integer(payload.get("additions")),
        "deletions": _integer(payload.get("deletions")),
        "changes": _integer(payload.get("changes")),
    }


def _review_result(payload):
    """Project one pull request review."""

    _object(payload)
    return {
        "id": _integer(payload.get("id")),
        "author": _login(payload.get("user")),
        "state": _string(payload.get("state"), 32),
        "body": _string(payload.get("body"), BODY_MAX_CHARS),
        "commit_id": _string(payload.get("commit_id"), 128),
        "submitted_at": _string(payload.get("submitted_at"), 64),
        "url": _string(payload.get("html_url"), 500),
    }


def _release_result(payload):
    """Project one release without asset download metadata."""

    _object(payload)
    return {
        "id": _integer(payload.get("id")),
        "tag": _string(payload.get("tag_name"), 255),
        "name": _string(payload.get("name"), 1000),
        "body": _string(payload.get("body"), BODY_MAX_CHARS),
        "draft": bool(payload.get("draft")),
        "prerelease": bool(payload.get("prerelease")),
        "author": _login(payload.get("author")),
        "created_at": _string(payload.get("created_at"), 64),
        "published_at": _string(payload.get("published_at"), 64),
        "url": _string(payload.get("html_url"), 500),
    }


def _workflow_run_result(payload):
    """Project one workflow run without jobs, logs, or artifacts."""

    _object(payload)
    return {
        "id": _integer(payload.get("id")),
        "name": _string(payload.get("name"), 500),
        "event": _string(payload.get("event"), 100),
        "status": _string(payload.get("status"), 64),
        "conclusion": _string(payload.get("conclusion"), 64),
        "workflow_id": _integer(payload.get("workflow_id")),
        "run_number": _integer(payload.get("run_number")),
        "run_attempt": _integer(payload.get("run_attempt")),
        "head_branch": _string(payload.get("head_branch"), 255),
        "head_sha": _string(payload.get("head_sha"), 128),
        "actor": _login(payload.get("actor")),
        "created_at": _string(payload.get("created_at"), 64),
        "updated_at": _string(payload.get("updated_at"), 64),
        "url": _string(payload.get("html_url"), 500),
    }


def build_datasource_command(snapshot, material, trigger):
    """Build one Git datasource command from frozen GitHub state."""

    resolved = _resolved(snapshot)
    endpoint = _endpoint(resolved.get("endpoint"))
    _material(material, endpoint)
    datasource = resolved.get("datasource_config") or {}
    if not isinstance(datasource, dict):
        raise PluginRuntimeError("PLUGIN_CONFIG_INVALID")
    repositories = datasource.get("repositories")
    if repositories is None:
        repositories = [datasource.get("repository")]
    if (
        not isinstance(repositories, list)
        or not repositories
        or any(not isinstance(item, str) or not item for item in repositories)
    ):
        raise PluginRuntimeError("PLUGIN_CONFIG_INVALID")
    for repository in repositories:
        _validate_repository_scope(repository, resolved.get("connection_scope"))
    config = {
        "branch": datasource.get("branch") or "",
        "directory": datasource.get("directory") or "",
        "auth_scheme": "token" if material["value"] else "none",
        "allow_submodules": False,
    }
    if material["value"]:
        config["access_token"] = material["value"]
    config["repositories"] = [
        {
            "repo_url": f"{endpoint}/{repository}.git",
            "branch": datasource.get("branch") or "",
            "directory": datasource.get("directory") or "",
            "target_subdir": repository,
            "enabled": True,
        }
        for repository in repositories
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
    """Execute the GitHub datasource policy through the shared executor."""

    return execute(command, workspace_path, emit)


def _validate_definition(value):
    if (
        not isinstance(value, dict)
        or value.get("key") not in TOOL_KEYS
        or value.get("capability") != "repository.read"
        or value.get("side_effect") != "none"
        or not isinstance(value.get("description"), str)
        or not value["description"].strip()
        or not isinstance(value.get("input_schema"), dict)
        or value["input_schema"].get("type") != "object"
    ):
        raise PluginRuntimeError("PLUGIN_TOOL_NOT_READ_ONLY")


def _validate_runtime_arguments(key, arguments, config):
    """Validate repository and path boundaries inside the Runtime."""

    if key == "github_activity_summary":
        if not isinstance(arguments, dict):
            raise PluginRuntimeError("PLUGIN_ARGUMENTS_INVALID")
        repositories = arguments.get("repositories")
        if (
            not isinstance(repositories, list)
            or not repositories
            or len(repositories) > 50
        ):
            raise PluginRuntimeError("PLUGIN_ARGUMENTS_INVALID")
        scope = _runtime_scope(config)
        for repository in repositories:
            selector = _activity_selector(repository)
            if "*" not in selector:
                _validate_repository_scope(selector, scope)
        identities = {_activity_selector(repository).casefold() for repository in repositories}
        if len(identities) != len(repositories):
            raise PluginRuntimeError("PLUGIN_ARGUMENTS_INVALID")
        since = _timestamp(arguments.get("since"))
        until = _timestamp(arguments.get("until"))
        if since > until:
            raise PluginRuntimeError("PLUGIN_ARGUMENTS_INVALID")
        _page_value(arguments, "per_page", 1, 100, 50)
        return
    repository = _text(arguments, "repository")
    _validate_repository_scope(repository, _runtime_scope(config))
    if key == "github_read_file":
        _repository_path(_text(arguments, "path"))
        if arguments.get("ref"):
            _bounded_text(arguments["ref"], 255)
    elif key == "github_search_code":
        query = _text(arguments, "query")
        _bounded_text(query, 1024)
        if arguments.get("path"):
            _repository_path(arguments["path"])
    elif key == "github_commit_list":
        for name in ("since", "until"):
            if arguments.get(name):
                _bounded_text(arguments[name], 64)
        if arguments.get("ref"):
            _bounded_text(arguments["ref"], 255)
        if arguments.get("path"):
            _repository_path(arguments["path"])
    elif key == "github_issue_list":
        for name in ("since", "until"):
            if arguments.get(name):
                _bounded_text(arguments[name], 64)
    elif key == "github_commit_get" and arguments.get("ref"):
        _bounded_text(arguments["ref"], 255)
    elif key in {
        "github_issue_get",
        "github_issue_comments",
        "github_pull_request_get",
        "github_pull_request_files",
        "github_pull_request_reviews",
    }:
        _positive_integer(arguments, "number")
    elif key == "github_workflow_run_get":
        _positive_integer(arguments, "run_id")


def _runtime_scope(config):
    """Return the scope copied into the Runtime-only connection config."""

    if not isinstance(config, dict):
        raise PluginRuntimeError("PLUGIN_SCOPE_MISMATCH")
    scope = config.get("__allowed_scope")
    if not isinstance(scope, dict):
        raise PluginRuntimeError("PLUGIN_SCOPE_MISMATCH")
    return scope


def _validate_repository_scope(repository, scope):
    """Require a normalized repository inside the frozen allowlist."""

    if not isinstance(scope, dict):
        raise PluginRuntimeError("PLUGIN_SCOPE_MISMATCH")
    repositories = scope.get("repositories")
    if not isinstance(repositories, list):
        raise PluginRuntimeError("PLUGIN_SCOPE_MISMATCH")
    normalized = _repository_identity(repository)
    if normalized is None:
        raise PluginRuntimeError("PLUGIN_ARGUMENTS_INVALID")
    if "*" in repositories:
        return
    allowed = {
        identity
        for value in repositories
        for identity in [_repository_identity(value)]
        if identity is not None
    }
    if normalized.casefold() not in {value.casefold() for value in allowed}:
        raise PluginRuntimeError("PLUGIN_SCOPE_MISMATCH")


def _repository_identity(value):
    """Return one valid owner/repository identity or None."""

    if not isinstance(value, str):
        return None
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
        return None
    return repository


def _repository_path(value):
    """Require one repository-relative path without traversal segments."""

    if not isinstance(value, str) or not value or len(value) > 4096:
        raise PluginRuntimeError("PLUGIN_ARGUMENTS_INVALID")
    if any(ord(character) < 32 for character in value):
        raise PluginRuntimeError("PLUGIN_ARGUMENTS_INVALID")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise PluginRuntimeError("PLUGIN_ARGUMENTS_INVALID")
    if path.as_posix() in {"", "."}:
        raise PluginRuntimeError("PLUGIN_ARGUMENTS_INVALID")
    return path.as_posix()


def _bounded_text(value, limit):
    """Require one bounded text argument without control characters."""

    if (
        not isinstance(value, str)
        or not value
        or len(value) > limit
        or any(ord(character) < 32 for character in value)
    ):
        raise PluginRuntimeError("PLUGIN_ARGUMENTS_INVALID")
    return value


def _endpoint(value):
    parsed = urlsplit(str(value or "").strip())
    if (parsed.scheme != "https" or parsed.hostname != "github.com"
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
        raise PluginRuntimeError("PLUGIN_SNAPSHOT_MISMATCH")
    return "https://github.com"


def _resolved(snapshot):
    resolved = snapshot.get("resolved_config")
    if not isinstance(resolved, dict):
        raise PluginRuntimeError("PLUGIN_CONFIG_INVALID")
    return resolved


def _material(material, endpoint):
    if (not isinstance(material, dict) or material.get("plugin_key") != PLUGIN_KEY
            or str(material.get("endpoint") or "").rstrip("/") != endpoint
            or not isinstance(material.get("value"), str)
            or (not material["value"] and material.get("authentication") != "anonymous")):
        raise PluginRuntimeError("PLUGIN_MATERIAL_MISMATCH")


def _text(arguments, name):
    if not isinstance(arguments, dict):
        raise PluginRuntimeError("PLUGIN_ARGUMENTS_INVALID")
    value = arguments.get(name)
    if not isinstance(value, str) or not value:
        raise PluginRuntimeError("PLUGIN_ARGUMENTS_INVALID")
    return value


def _positive_integer(arguments, name):
    """Return one required positive integer runtime argument."""

    if not isinstance(arguments, dict):
        raise PluginRuntimeError("PLUGIN_ARGUMENTS_INVALID")
    value = arguments.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise PluginRuntimeError("PLUGIN_ARGUMENTS_INVALID")
    return value


def _pagination(arguments):
    """Return bounded GitHub pagination parameters."""

    return {
        "page": _page_value(arguments, "page", 1, 1000, 1),
        "per_page": _page_value(arguments, "per_page", 1, 20, 10),
    }


def _add_time_filters(params, arguments):
    """Add optional ISO-8601 window filters supported by GitHub lists."""

    for name in ("since", "until"):
        value = arguments.get(name)
        if value:
            params[name] = _bounded_text(value, 64)


def _timestamp(value):
    """Return one timezone-aware ISO-8601 timestamp."""

    text = _bounded_text(value, 64)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PluginRuntimeError("PLUGIN_ARGUMENTS_INVALID") from exc
    if parsed.tzinfo is None:
        raise PluginRuntimeError("PLUGIN_ARGUMENTS_INVALID")
    return parsed


def _timestamp_in_window(value, since, until):
    """Return whether one provider timestamp is within an inclusive window."""

    if not isinstance(value, str):
        return False
    try:
        timestamp = _timestamp(value)
    except PluginRuntimeError:
        return False
    return _timestamp(since) <= timestamp <= _timestamp(until)


def _page_value(arguments, name, minimum, maximum, default):
    """Return one bounded optional pagination value."""

    if not isinstance(arguments, dict):
        raise PluginRuntimeError("PLUGIN_ARGUMENTS_INVALID")
    value = arguments.get(name, default)
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise PluginRuntimeError("PLUGIN_ARGUMENTS_INVALID")
    return value


def _json_get(client, path, token, params=None, max_bytes=JSON_MAX_BYTES):
    """Read bounded JSON from one fixed GitHub API path."""

    status, body, truncated = _get(
        client,
        f"{API_URL}{path}",
        token,
        params,
        "application/vnd.github+json",
        max_bytes,
        False,
    )
    _status(status)
    if truncated:
        raise PluginRuntimeError("GITHUB_RESPONSE_TOO_LARGE")
    try:
        return json.loads(body)
    except (UnicodeDecodeError, ValueError) as exc:
        raise PluginRuntimeError("GITHUB_RESPONSE_INVALID") from exc


def _items(payload):
    """Return a validated list of GitHub response objects."""

    if not isinstance(payload, list) or any(
        not isinstance(item, dict) for item in payload
    ):
        raise PluginRuntimeError("GITHUB_RESPONSE_INVALID")
    return payload


def _object(payload):
    """Require one GitHub response object."""

    if not isinstance(payload, dict):
        raise PluginRuntimeError("GITHUB_RESPONSE_INVALID")
    return payload


def _nested(value, *path):
    """Read one nested response value without trusting its shape."""

    current = value
    for name in path:
        if not isinstance(current, dict):
            return None
        current = current.get(name)
    return current


def _string(value, limit):
    """Return one bounded response string."""

    return value[:limit] if isinstance(value, str) else ""


def _integer(value):
    """Return one non-negative response integer."""

    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(value, 0)


def _login(value):
    """Return one bounded GitHub account login."""

    return _string(value.get("login"), 160) if isinstance(value, dict) else ""


def _labels(value):
    """Return bounded label names from one issue response."""

    if not isinstance(value, list):
        return []
    labels = []
    for item in value[:20]:
        name = item.get("name") if isinstance(item, dict) else item
        if isinstance(name, str) and name:
            labels.append(name[:255])
    return labels


def _get(client, url, token, params, accept, max_bytes, truncate):
    """Retry transient failures within a bounded wait and preserve permanent errors."""

    for attempt in range(GET_MAX_ATTEMPTS):
        try:
            with client.stream(
                "GET",
                url,
                params=params,
                timeout=15.0,
                follow_redirects=False,
                headers={
                    "Accept": accept,
                    **({"Authorization": f"Bearer {token}"} if token else {}),
                    "X-GitHub-Api-Version": API_VERSION,
                    "User-Agent": "SourceLens-LensNode",
                },
            ) as response:
                if response.is_redirect:
                    raise PluginRuntimeError("GITHUB_REDIRECT_REJECTED")
                rate_limited = response.status_code == 429 or (
                    response.status_code == 403
                    and (response.headers.get("X-RateLimit-Remaining") == "0" or response.headers.get("Retry-After"))
                )
                retryable = rate_limited or response.status_code in {408, 500, 502, 503, 504}
                delay = GET_RETRY_DELAY_SECONDS * (attempt + 1)
                if retryable:
                    delay = _retry_delay(response.headers, delay, rate_limited)
                    if attempt + 1 < GET_MAX_ATTEMPTS and delay <= GET_MAX_RETRY_WAIT_SECONDS:
                        response.close()
                        time.sleep(delay)
                        continue
                if rate_limited:
                    raise PluginRuntimeError("GITHUB_RATE_LIMITED")
                if response.status_code >= 400:
                    return response.status_code, b"", False
                body = bytearray()
                for chunk in response.iter_bytes():
                    remaining = max_bytes - len(body)
                    if len(chunk) <= remaining:
                        body.extend(chunk)
                        continue
                    if truncate:
                        body.extend(chunk[:remaining])
                        return response.status_code, bytes(body), True
                    raise PluginRuntimeError("GITHUB_RESPONSE_TOO_LARGE")
            return response.status_code, bytes(body), False
        except httpx.TransportError:
            if attempt + 1 == GET_MAX_ATTEMPTS:
                raise
            time.sleep(GET_RETRY_DELAY_SECONDS * (attempt + 1))
    raise AssertionError("unreachable")


def _retry_delay(headers, fallback, rate_limited):
    """Honor GitHub retry hints without polling before the indicated reset."""

    delays = [fallback]
    for key in (("Retry-After", "X-RateLimit-Reset") if rate_limited else ("Retry-After",)):
        try:
            hint = headers[key]
        except KeyError:
            continue
        try:
            value = int(hint)
        except (TypeError, ValueError):
            if key != "Retry-After":
                continue
            try:
                value = parsedate_to_datetime(hint).timestamp() - time.time()
            except (TypeError, ValueError, OverflowError):
                continue
        if key == "X-RateLimit-Reset":
            value = value - time.time() + 1
        delays.append(value)
    if rate_limited and len(delays) == 1:
        delays.append(60.0)
    return max(delays)


def _status(status):
    if status < 400:
        return
    errors = {404: "GITHUB_NOT_FOUND", 401: "GITHUB_ACCESS_DENIED",
              403: "GITHUB_ACCESS_DENIED", 429: "GITHUB_RATE_LIMITED"}
    raise PluginRuntimeError(errors.get(status, "GITHUB_REQUEST_FAILED"))


def agent_extensions():
    """Register Plugin-owned agent behavior through the generic host protocol."""

    from lensnode.plugin_package_loader import load_plugin_support

    support = load_plugin_support("activity_reports")
    return (("activity-report", support.prepare_activity_report),)


def project_tool_result(tool_key, result, max_bytes):
    """Declare source evidence coverage without host-side provider knowledge."""

    from lensnode.plugin_package_loader import load_plugin_support

    return load_plugin_support("activity_reports").project_activity_result(tool_key, result, max_bytes, TOOL_METADATA)
