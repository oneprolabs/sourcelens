"""GitHub LensNode runtime entrypoint."""

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import PurePosixPath
from urllib.parse import quote, urlsplit

import httpx
from langchain.tools import ToolRuntime, tool

from lensnode.plugin_runtime import PluginRuntimeError


PLUGIN_API_VERSION = 1
PLUGIN_KEY = "github"
PLUGIN_VERSION = "1.0.0"
API_URL = "https://api.github.com"
API_VERSION = "2022-11-28"
READ_MAX_BYTES = 200_000
SEARCH_MAX_BYTES = 1_000_000
JSON_MAX_BYTES = 1_000_000
BODY_MAX_CHARS = 12_000
GET_MAX_ATTEMPTS = 3
GET_RETRY_DELAY_SECONDS = 0.25
ACTIVITY_DEFAULT_PER_PAGE = 50
ACTIVITY_RESOURCE_MAX = {
    "commits": 50,
    "pull_requests": 10,
    "issues": 10,
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
        return _activity_summary(client, arguments, secret)
    if key == "github_read_file":
        return _read_file(client, arguments, secret)
    if key == "github_search_code":
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


def _activity_summary(client, arguments, secret):
    """Return compact activity from approved repositories in one Tool call."""

    repositories = arguments["repositories"]
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
    requests = [
        (repository, resource)
        for repository in repositories
        for resource in ("commits", "pull_requests", "issues")
    ]
    workers = min(8, len(requests))
    successful_resources = 0
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                _activity_resource,
                client,
                repository,
                resource,
                since,
                until,
                per_page,
                secret,
            ): (repository, resource)
            for repository, resource in requests
        }
        for future in as_completed(futures):
            repository, resource = futures[future]
            try:
                items, possibly_truncated = future.result()
            except PluginRuntimeError as exc:
                results[repository].setdefault("errors", {})[resource] = str(
                    exc
                )
                continue
            except httpx.HTTPError:
                results[repository].setdefault("errors", {})[
                    resource
                ] = "GITHUB_REQUEST_FAILED"
                continue
            results[repository][resource] = items
            results[repository]["possibly_truncated"][resource] = (
                possibly_truncated
            )
            successful_resources += 1
    if not successful_resources:
        raise PluginRuntimeError("GITHUB_REQUEST_FAILED")
    return {
        "ok": True,
        "since": since,
        "until": until,
        "limits": {
            resource: min(per_page, maximum)
            for resource, maximum in ACTIVITY_RESOURCE_MAX.items()
        },
        "repositories": [results[repository] for repository in repositories],
    }


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
    raw_payload = _items(_json_get(client, path, secret, params))
    possibly_truncated = _activity_possibly_truncated(
        raw_payload,
        resource,
        since,
        page_size,
    )
    return (
        [
            projector(item)
            for item in raw_payload
            if _timestamp_in_window(
                _nested(item, "commit", "author", "date")
                if resource == "commits"
                else item.get("updated_at"),
                since,
                until,
            )
        ],
        possibly_truncated,
    )


def _activity_issues(
    client,
    repository,
    since,
    until,
    page_size,
    secret,
):
    """Search only issues so pull requests cannot consume the result limit."""

    query = (
        f"repo:{repository} is:issue "
        f"updated:{since}..{until}"
    )
    payload = _json_get(
        client,
        "/search/issues",
        secret,
        {
            "q": query,
            "sort": "updated",
            "direction": "desc",
            "page": 1,
            "per_page": page_size,
        },
        max_bytes=SEARCH_MAX_BYTES,
    )
    if (
        not isinstance(payload, dict)
        or isinstance(payload.get("total_count"), bool)
        or not isinstance(payload.get("total_count"), int)
        or not isinstance(payload.get("incomplete_results"), bool)
    ):
        raise PluginRuntimeError("GITHUB_RESPONSE_INVALID")
    raw_items = _items(payload.get("items"))
    items = [
        _activity_issue_summary(item)
        for item in raw_items
        if _timestamp_in_window(item.get("updated_at"), since, until)
    ]
    possibly_truncated = (
        payload["incomplete_results"]
        or payload["total_count"] > len(raw_items)
    )
    return items[:page_size], possibly_truncated


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
        "created_at": _string(payload.get("created_at"), 64),
        "updated_at": _string(payload.get("updated_at"), 64),
        "closed_at": _string(payload.get("closed_at"), 64),
        "merged_at": _string(payload.get("merged_at"), 64),
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
        "labels": _labels(payload.get("labels")),
        "created_at": _string(payload.get("created_at"), 64),
        "updated_at": _string(payload.get("updated_at"), 64),
        "closed_at": _string(payload.get("closed_at"), 64),
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
        "auth_scheme": "token",
        "access_token": material["value"],
        "allow_submodules": False,
    }
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
            _validate_repository_scope(repository, scope)
        identities = {
            _repository_identity(repository).casefold()
            for repository in repositories
        }
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
            or not material.get("value")):
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
                    "Authorization": f"Bearer {token}",
                    "X-GitHub-Api-Version": API_VERSION,
                    "User-Agent": "SourceLens-LensNode",
                },
            ) as response:
                if response.is_redirect:
                    raise PluginRuntimeError("GITHUB_REDIRECT_REJECTED")
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


def _status(status):
    if status < 400:
        return
    errors = {404: "GITHUB_NOT_FOUND", 401: "GITHUB_ACCESS_DENIED",
              403: "GITHUB_ACCESS_DENIED", 429: "GITHUB_RATE_LIMITED"}
    raise PluginRuntimeError(errors.get(status, "GITHUB_REQUEST_FAILED"))
