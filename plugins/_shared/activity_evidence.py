"""Source-owned activity records and coverage, independent of report workflows."""

import json
from datetime import datetime


def _json(value):
    """Encode stable evidence without serializing arbitrary Python objects."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _stamp(value):
    """Parse only timezone-aware source timestamps."""

    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _in_window(value, since, until):
    """Apply the same inclusive source window used by activity tools."""

    stamp, lower, upper = _stamp(value), _stamp(since), _stamp(until)
    return stamp is not None and lower is not None and upper is not None and lower <= stamp <= upper


def _limited(source):
    """Read only explicit provider coverage metadata."""

    if not isinstance(source, dict):
        return True
    flag = source.get("possibly_truncated")
    return (
        (isinstance(source.get("_evidence"), dict) and source["_evidence"].get("complete") is not True)
        or source.get("ok") is False
        or bool(source.get("error"))
        or bool(source.get("errors"))
        or source.get("truncated") is True
        or source.get("source_coverage_complete") is False
        or source.get("repositories_truncated") is True
        or source.get("projects_truncated") is True
        or flag is True
        or (isinstance(flag, dict) and any(value is True for value in flag.values()))
    )


def _account(value, identity="", login=False):
    """Keep display names separate from verified source account identifiers."""

    if isinstance(value, dict):
        identity = identity or value.get("login") or value.get("username") or value.get("accountId") or value.get("key")
        name = value.get("displayName") or value.get("name") or identity or ""
    else:
        name = str(value or identity or "")
        identity = identity or (name if login else "")
    return {"identity": str(identity or ""), "name": str(name), "verified": bool(identity)}


def _record(provider, connection, resource, kind, item, since, until):
    """Describe observed events without equating ownership with daily activity."""

    if not isinstance(item, dict):
        return None
    commit = kind == "commits"
    timestamp = (
        item.get("committed_at", item.get("authored_at")) if commit else item.get("updated_at", item.get("updated"))
    )
    if not commit:
        for field in ("merged_at", "resolution_date", "closed_at", "created_at", "created"):
            if _in_window(item.get(field), since, until):
                timestamp = item[field]
                break
    if not _in_window(timestamp, since, until):
        return None
    identity = str(item.get("sha", item.get("key", item.get("number", ""))))
    if not identity:
        return None
    author = item.get("author")
    if provider == "jira":
        name = str(item.get("assignee") or "未分配 / Unassigned")
        person = str(item.get("assignee_identity") or f"unverified:{name}:{resource}:{identity}")
        role = "当前负责人（非本次更新者） / current assignee, not update actor"
    elif commit:
        name = str(item.get("login") or item.get("author_name") or author or "Unknown")
        person = str(item.get("login") or item.get("author_email") or f"unverified:{name}:{identity}")
        role = "提交作者 / commit author"
    else:
        name = (
            str(author.get("username") or author.get("name") or "Unknown")
            if isinstance(author, dict)
            else str(author or "Unknown")
        )
        person = (
            str(author.get("username") or f"unverified:{name}:{resource}:{identity}")
            if isinstance(author, dict)
            else str(author or f"unverified:{name}:{resource}:{identity}")
        )
        role = "条目作者（非本次更新者） / item author, not update actor"
    event = "committed" if commit else "updated"
    if not commit and _in_window(item.get("merged_at"), since, until):
        event = "merged"
    elif provider == "jira" and _in_window(item.get("resolution_date"), since, until):
        event = "resolved"
    elif not commit and provider != "jira" and _in_window(item.get("closed_at"), since, until):
        event = "closed"
    elif not commit and _in_window(item.get("created_at", item.get("created")), since, until):
        event = "created"
    roles = {
        "author": _account(author, item.get("author_identity"), login=provider == "github"),
        "assignee": _account(item.get("assignee"), item.get("assignee_identity")),
    }
    for role_name in ("creator", "reporter", "updated_by", "closed_by", "merged_by", "resolved_by"):
        roles[role_name] = _account(item.get(role_name), item.get(f"{role_name}_identity"), login=provider == "github")
    roles["assignees"] = [_account(value) for value in item.get("assignees", []) if isinstance(value, dict)]
    if commit:
        roles["author"] = _account(name, item.get("login") or item.get("author_email"))
    actor_role = {
        "merged": "merged_by",
        "closed": "closed_by",
        "resolved": "resolved_by",
        "updated": "updated_by",
        "created": "creator" if provider == "jira" else "author",
        "committed": "author",
    }[event]
    actor = roles[actor_role]
    return {
        "provider": provider,
        "person_id": f"{provider}:{connection}:{person}",
        "person": name,
        "sources": [{"provider": provider, "resource": resource, "url": str(item.get("url", ""))}],
        "role": role,
        "roles": roles,
        "event_actor": actor if actor["identity"] or actor["name"] else None,
        "kind": kind,
        "id": identity,
        "resources": [resource or str(item.get("project", ""))],
        "title": str(item.get("title", item.get("summary", item.get("message", "")))),
        "timestamp": timestamp,
        "event": event,
        "current_state": str(item.get("state", item.get("status", ""))),
        "url": str(item.get("url", "")),
    }


def bound_activity_result(result, max_bytes):
    """Keep a usable bounded result and disclose every dropped resource tail."""

    result = json.loads(_json(result))
    while len(_json(result).encode()) > max_bytes:
        candidates = []
        if isinstance(result.get("items"), list) and result["items"]:
            candidates.append((result, "items"))
        for group in result.get("projects", result.get("repositories", [])):
            for key in ("commits", "issues", "pull_requests", "merge_requests"):
                if isinstance(group.get(key), list) and group[key]:
                    candidates.append((group, key))
        if not candidates:
            raise ValueError("Activity coverage metadata exceeds result budget")
        group, key = max(candidates, key=lambda pair: len(_json(pair[0][pair[1]])))
        group[key] = group[key][: len(group[key]) // 2]
        if key == "items":
            group["possibly_truncated"] = True
        else:
            group.setdefault("possibly_truncated", {})[key] = True
        if "source_coverage_complete" in result:
            result["source_coverage_complete"] = False
    return result


def activity_evidence(provider, result, connection=""):
    """Interpret one source snapshot without selecting or composing a report."""

    since, until = result.get("since", ""), result.get("until", "")
    groups = result.get("repositories", result.get("projects"))
    if not isinstance(groups, list):
        groups = [{"project": "", "issues": result.get("items", [])}]
    complete = not any(_limited(source) for source in [result, *groups])
    records = {}
    limitations = []
    resources = []
    for group in groups:
        resource = str(group.get("repository", group.get("project", "")))
        resources.append({"resource": resource, "complete": not _limited(group), "errors": group.get("errors", [])})
        for kind in ("commits", "pull_requests", "merge_requests", "issues"):
            for item in group.get(kind, []):
                record = _record(provider, connection, resource, kind, item, since, until)
                if record is None:
                    limitations.append("Record outside the verified source window")
                    continue
                identity = (kind, record["id"]) if kind == "commits" else (resource, kind, record["id"])
                previous = records.get(identity)
                if previous is None:
                    records[identity] = record
                elif resource not in previous["resources"]:
                    previous["resources"].append(resource)
                    previous["sources"].extend(record["sources"])
    return {
        "complete": complete and not limitations,
        "records": list(records.values()),
        "limitations": sorted(set(limitations)),
        "scope": {"since": since, "until": until},
        "resources": resources,
        "errors": result.get("errors", []),
    }


def project_activity_result(tool_key, result, max_bytes, metadata):
    """Keep source facts and coverage inside the existing tool-result budget."""

    if metadata.get(tool_key, {}).get("durable_evidence") is not True:
        return bound_activity_result(result, max_bytes)
    result = dict(result)
    groups = result.get("repositories", result.get("projects", []))
    complete = not any(_limited(source) for source in [result, *groups])
    result["_evidence"] = {"complete": complete}
    result = bound_activity_result(result, max_bytes)
    groups = result.get("repositories", result.get("projects", []))
    result["_evidence"] = {"complete": complete and not any(_limited(source) for source in [result, *groups])}
    return bound_activity_result(result, max_bytes)
