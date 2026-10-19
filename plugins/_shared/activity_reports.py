"""Durable, attributable source evidence for model-authored activity reports."""

import hashlib
import json
import os
import re
import shutil
import threading
import uuid
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

from lensnode.plugin_agent import AgentExecutionPolicy, AgentExtension, plugin_state_root
from lensnode.plugin_package_loader import load_runtime_contract

MAX_ACTIVITY_PAGE_CHARS = 24000


def activity_ledger_root(workspace, run_uuid):
    """Isolate private evidence by Run without accepting a caller path."""

    return plugin_state_root(workspace, run_uuid) / "activity-report"


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


class ActivityLedger:
    """Keep source results outside the model-writable scratch filesystem."""

    def __init__(self, root):
        self.root = Path(root)
        if any(path.is_symlink() for path in (self.root, *self.root.parents)):
            raise ValueError("Invalid activity evidence root")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        self.request_locks = {}
        self.entries = {}
        for path in self.root.glob("*.json"):
            if path.is_symlink():
                raise ValueError("Invalid activity evidence file")
            entry = json.loads(path.read_text(encoding="utf-8"))
            self.entries[path.stem] = entry

    @staticmethod
    def _key(provider, connection, version, tool, arguments):
        identity = _json([provider, connection, version, tool, arguments])
        return hashlib.sha256(identity.encode()).hexdigest()

    def cached(self, provider, connection, version, tool, arguments):
        """Reuse only an exact source snapshot within this Run."""

        key = self._key(provider, connection, version, tool, arguments)
        with self.lock:
            entry = self.entries.get(key)
            return _json(entry["result"]) if entry else None

    def has_evidence(self):
        """Distinguish a collected report from a request awaiting clarification."""

        with self.lock:
            return bool(self.entries)

    def request_lock(self, provider, connection, version, tool, arguments):
        """Serialize identical concurrent activity requests within this Run."""

        key = self._key(provider, connection, version, tool, arguments)
        with self.lock:
            return self.request_locks.setdefault(key, threading.Lock())

    def record(self, provider, connection, version, tool, arguments, result):
        """Persist projected evidence atomically before model compaction."""

        if not isinstance(result, dict) or result.get("ok") is not True:
            return
        key = self._key(provider, connection, version, tool, arguments)
        with self.lock:
            entry = json.loads(
                _json(
                    {
                        "provider": provider,
                        "connection": connection,
                        "version": version,
                        "tool": tool,
                        "arguments": arguments,
                        "result": result,
                        "sequence": max((item["sequence"] for item in self.entries.values()), default=0) + 1,
                    }
                )
            )
            path = self.root / f"{key}.json"
            temporary = self.root / f"{key}.{uuid.uuid4().hex}.tmp"
            with temporary.open("x", encoding="utf-8") as stream:
                os.chmod(temporary, 0o600)
                stream.write(_json(entry))
            os.replace(temporary, path)
            self.entries[key] = entry

    def read_progress(self, evidence, page=None, *, persist=True):
        """Persist consumed ranges for one exact immutable evidence snapshot."""

        digest = hashlib.sha256(_json(evidence).encode()).hexdigest()
        total = len(evidence["records"])
        folder = self.root / "review"
        path = folder / "progress.json"
        with self.lock:
            if folder.is_symlink() or path.is_symlink():
                raise ValueError("Invalid activity review state")
            state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            if state.get("snapshot_sha256") != digest:
                state = {"snapshot_sha256": digest, "ranges": [], "read": False}
            if page is not None:
                state["read"] = True
                ranges = sorted([*state["ranges"], list(page)])
                merged = []
                for start, end in ranges:
                    if merged and start <= merged[-1][1]:
                        merged[-1][1] = max(merged[-1][1], end)
                    else:
                        merged.append([start, end])
                state["ranges"] = merged
                if persist:
                    folder.mkdir(exist_ok=True, mode=0o700)
                    temporary = folder / f"{uuid.uuid4().hex}.tmp"
                    with temporary.open("x", encoding="utf-8") as stream:
                        os.chmod(temporary, 0o600)
                        stream.write(_json(state))
                    os.replace(temporary, path)
            cursor = 0
            for start, end in state["ranges"]:
                if start > cursor:
                    break
                cursor = max(cursor, end)
            complete = state["read"] and cursor >= total
            return {"complete": complete, "total_records": total,
                    "next_offset": None if complete else cursor, "snapshot_sha256": digest}

    def report(self, expected_tools):
        """Normalize saved evidence without producing a business report or answer."""

        with self.lock:
            entries = sorted(self.entries.values(), key=lambda entry: entry["sequence"])
            entries = json.loads(_json(entries))
        latest = {}
        for entry in entries:
            args = entry["arguments"]
            result = entry["result"]
            scope = args.get("projects", args.get("repositories", []))
            key = _json([entry["tool"], entry["connection"], sorted(scope), result.get("since"), result.get("until")])
            latest[key] = entry
        records = {}
        limitations = []
        tools = set()
        windows = set()
        source_coverage = {}
        for entry in sorted(latest.values(), key=lambda entry: (entry["provider"], entry["sequence"])):
            provider, result = entry["provider"], entry["result"]
            tools.add(entry["tool"])
            since, until = result.get("since", ""), result.get("until", "")
            windows.add((since, until))
            groups = result.get("repositories", result.get("projects"))
            if not isinstance(groups, list):
                groups = [{"project": "", "issues": result.get("items", [])}]
            sources = [result, *groups]
            coverage = source_coverage.setdefault(entry["tool"], {"complete": True, "snapshots": []})
            complete = not any(_limited(source) for source in sources)
            coverage["complete"] = coverage["complete"] and complete
            coverage["snapshots"].append({
                "provider": provider, "since": since, "until": until, "complete": complete,
                "resources": [
                    {"resource": str(group.get("repository", group.get("project", ""))),
                     "complete": not _limited(group), "errors": group.get("errors", [])}
                    for group in groups
                ],
                "errors": result.get("errors", []),
            })
            if any(_limited(source) for source in sources):
                limitations.append(f"{provider}: 来源截断或部分请求失败 / truncated or partially failed")
            for group in groups:
                resource = str(group.get("repository", group.get("project", "")))
                for kind in ("commits", "pull_requests", "merge_requests", "issues"):
                    for item in group.get(kind, []):
                        record = _record(provider, entry["connection"], resource, kind, item, since, until)
                        if record is None:
                            limitations.append(f"{provider}: 未归入时间窗口的记录 / record outside verified window")
                            continue
                        identity = (
                            (kind, record["id"], since, until)
                            if kind == "commits"
                            else (
                                provider,
                                entry["connection"],
                                kind,
                                record["id"],
                                since,
                                until,
                                resource,
                            )
                        )
                        existing = records.get(identity)
                        if existing is None:
                            records[identity] = record
                        elif resource not in existing["resources"]:
                            existing["resources"].append(resource)
                        if existing is not None:
                            existing["sources"].extend(record["sources"])
        for tool in sorted(set(expected_tools) - tools):
            limitations.append(f"{tool}: 尚无来源结果 / source not retrieved")
            source_coverage[tool] = {"complete": False, "snapshots": []}
        if not windows:
            limitations.append("尚无可验证的来源材料 / no source evidence")
        ordered = sorted(records.values(), key=lambda item: (item["person_id"], item["timestamp"], item["id"]))
        limitations = sorted(set(limitations))
        observed_events = {}
        for record in ordered:
            key = (record["person_id"], record["provider"], record["kind"], record["event"], record["role"])
            facts = observed_events.setdefault(key, {
                "person_id": record["person_id"], "person": record["person"], "provider": record["provider"],
                "kind": record["kind"], "event": record["event"], "role": record["role"],
                "count": 0, "record_ids": [],
                "attribution": "associated_with_role_not_performed_by_person",
            })
            facts["count"] += 1
            facts["record_ids"].append(record["id"])
        report = {
            "records": ordered,
            "limitations": limitations,
            "complete": not limitations,
            "windows": sorted(windows),
            "source_tools": sorted(tools),
            "source_coverage": source_coverage,
            "observed_events": list(observed_events.values()),
            "source_snapshots": [
                {"provider": entry["provider"], "arguments": entry["arguments"], "result": entry["result"]}
                for entry in sorted(latest.values(), key=lambda entry: (entry["provider"], entry["sequence"]))
            ],
        }
        return report


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


class _ReportText(HTMLParser):
    """Keep visible report blocks separate while joining inline formatting."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        if tag in {"p", "div", "tr", "li", "br", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(self.hidden - 1, 0)
        if tag in {"p", "div", "tr", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")
        elif tag in {"td", "th"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _negative_activity_claims(answer):
    """Locate high-risk statements so the model can repair the rejected report."""

    parser = _ReportText()
    parser.feed(str(answer or ""))
    pattern = re.compile(
        r"零完成|没有(?:任何)?(?:完成|产出|工作)|低产出|产出(?:偏低|很低)|长期未推进|长期停滞|"
        r"\b(?:completed nothing|zero[- ]completions?|low productivity|no activity|inactive|no work)\b",
        re.IGNORECASE,
    )
    claims = []
    for sentence in re.split(r"[。！？.!?\n]", "".join(parser.parts)):
        sentence = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", sentence)
        for match in pattern.finditer(sentence):
            prefix = sentence[:match.start()]
            if not re.search(
                r"(?:不能据此判断|无法判断|不代表|不推论为|不等于|不能认定|不含|删去|删除|移除|去掉|而非[\"“]?|cannot conclude|does not mean)"
                r"[^，,；;]{0,40}$", prefix, re.IGNORECASE,
            ):
                claims.append(sentence.strip()[:400])
                break
    return claims


def _contains_negative_activity_claim(answer):
    """Detect high-risk personnel conclusions without judging ordinary prose."""

    return bool(_negative_activity_claims(answer))


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
        roles[role_name] = _account(item.get(role_name), item.get(f"{role_name}_identity"),
                                    login=provider == "github")
    roles["assignees"] = [_account(value) for value in item.get("assignees", []) if isinstance(value, dict)]
    if commit:
        roles["author"] = _account(name, item.get("login") or item.get("author_email"))
    actor_role = {"merged": "merged_by", "closed": "closed_by", "resolved": "resolved_by",
                  "updated": "updated_by", "created": "creator" if provider == "jira" else "author",
                  "committed": "author"}[event]
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


def build_activity_evidence_tool(ledger, expected_tools):
    """Expose bounded saved evidence without staging files or ending the agent."""

    from langchain.tools import tool

    @tool("read_activity_evidence")
    def read_activity_evidence(offset: int = 0, limit: int = 50) -> str:
        """Read normalized activity evidence saved by the batch source tools.

        Use offset >= 0 and limit between 1 and 100 (default 50). Follow next_offset
        to read further pages without refetching sources. Records
        retain source identities, event dates, roles and coverage limits. They are
        evidence, not a finished work summary. Query coverage does not establish
        that the selected dates and resources match the user's request. Follow the
        bound report Skill to interpret the evidence and deliver the final report.
        """

        if not ledger.has_evidence():
            return _json({"ok": False, "error": "ACTIVITY_EVIDENCE_REQUIRED"})
        if offset < 0 or not 1 <= limit <= 100:
            return _json({"ok": False, "error": "ACTIVITY_EVIDENCE_PAGE_INVALID"})
        evidence = ledger.report(expected_tools)
        records = evidence["records"]
        if offset >= len(records) and offset != 0:
            return _json({"ok": False, "error": "ACTIVITY_EVIDENCE_PAGE_INVALID", "total_records": len(records)})
        end = min(offset + limit, len(records))
        payload = {
                "ok": True,
                "records": records[offset:end],
                "total_records": len(records),
                "next_offset": end if end < len(records) else None,
                "next_page": (
                    {"tool": "read_activity_evidence", "arguments": {"offset": end, "limit": limit}}
                    if end < len(records) else None
                ),
                "page_complete": offset == 0 and end == len(records),
                "windows": evidence["windows"],
                "source_tools": evidence["source_tools"],
                "limitations": evidence["limitations"],
                "source_coverage_complete": evidence["complete"],
                "source_coverage": evidence["source_coverage"],
                "observed_events": evidence["observed_events"],
                "_evidence": {"complete": evidence["complete"]},
                "read_progress": ledger.read_progress(evidence, (offset, end), persist=False),
            }
        metadata_chars = len(_json({
            "observed_events": payload["observed_events"], "source_coverage": payload["source_coverage"],
        }))
        if len(_json(payload)) > MAX_ACTIVITY_PAGE_CHARS and metadata_chars > MAX_ACTIVITY_PAGE_CHARS // 2:
            payload["observed_events"] = []
            payload["observed_events_omitted"] = True
            payload["source_coverage"] = {
                key: {"complete": value["complete"]} for key, value in evidence["source_coverage"].items()
            }
            payload["coverage_details_omitted"] = True
        while len(_json(payload)) > MAX_ACTIVITY_PAGE_CHARS and len(payload["records"]) > 1:
            payload["records"] = payload["records"][:max(1, len(payload["records"]) // 2)]
            end = offset + len(payload["records"])
            payload.update(
                next_offset=end if end < len(records) else None,
                next_page=(
                    {"tool": "read_activity_evidence", "arguments": {"offset": end, "limit": limit}}
                    if end < len(records) else None
                ),
                page_complete=offset == 0 and end == len(records),
                read_progress=ledger.read_progress(evidence, (offset, end), persist=False),
            )
        if len(_json(payload)) > MAX_ACTIVITY_PAGE_CHARS and not payload.get("coverage_details_omitted"):
            payload["observed_events"] = []
            payload["observed_events_omitted"] = True
            payload["source_coverage"] = {
                key: {"complete": value["complete"]} for key, value in evidence["source_coverage"].items()
            }
            payload["coverage_details_omitted"] = True
        if len(_json(payload)) > MAX_ACTIVITY_PAGE_CHARS:
            return _json({"ok": False, "error": "ACTIVITY_EVIDENCE_PAGE_TOO_LARGE",
                          "message": "The evidence page exceeds the inline review limit; report this review gap."})
        ledger.read_progress(evidence, (offset, end))
        return _json(payload)

    read_activity_evidence.metadata = {"bounded_inline_result": True}
    return read_activity_evidence


ACTIVITY_REPORT_PATTERN = re.compile(
    r"工作内容|工作情况|工作汇总|日报|周报|月报|activity report|engineering report",
    re.IGNORECASE,
)
DELIVERY_FOLLOWUP_PATTERN = re.compile(
    r"^(?:继续|重新生成.*|继续.*生成.*|补.*html.*)$|"
    r"(?:为什么|为何|怎么).*(?:没有|没|未).*(?:html|文件|附件)|"
    r"\b(?:continue|regenerate|missing html|missing file)\b",
    re.IGNORECASE,
)
PROTECTED_DISCLOSURE_PATTERN = re.compile(
    r"credentials|environment variables|hidden policies|system prompt|"
    r"other users' data|凭据|其他用户数据|环境变量|"
    r"系统提示词|隐藏规则",
    re.IGNORECASE,
)


def _report_execution_guidance(command):
    """Return a compact execution contract for batch activity reports."""

    batch_tools = _activity_report_batch_tools(command)
    if not batch_tools or not _is_activity_report(command):
        return ""

    lines = [
        "\n\nActivity report execution contract:",
        "- Call each batch activity Tool once per available source where one "
        "batch covers the scope; use additional batches for remaining approved resources. "
        "Verify the requested date window and resource scope before rendering.",
        "- do not delegate one subagent per repository or project; each "
        "batch Tool already retrieves its source concurrently.",
        "- Available batch activity Tools: " + ", ".join(sorted(batch_tools)) + ". "
        "Follow each registered Tool's description and input schema for source-specific usage.",
        "- TOOL_ARGUMENTS_INVALID is a rejected request, not completed retrieval. "
        "Correct the arguments using the recovery hint and Tool schema, then retry once "
        "before declaring the source unavailable.",
    ]
    lines.extend(
        (
            "- If a source reports errors, possibly_truncated, repositories_truncated "
            "or source_coverage_complete=false, disclose "
            "that coverage limitation briefly instead of paginating or "
            "retrying per resource.",
            "- Shell exit status zero does not prove that every command succeeded. "
            "Inspect stderr and each operation's result; a later successful command cannot "
            "erase a preceding traceback or failed file read. /large_tool_results paths are virtual; "
            "use inspect_saved_output or analyze_structured_output instead of shell/Python open or jq.",
            "- The Plugin persists retrieved source results in a private evidence ledger. "
            "Normalize records by verified source identity and event; keep "
            "source IDs, timestamps, and coverage limits. Distinguish current "
            "status from transitions within the requested time window, and "
            "deduplicate exact commit SHAs without inventing identities or equating backports.",
            "- Reuse that ledger for corrections instead of retrieving the same sources again. "
            "The model remains responsible for the business report and HTML content; use normalized "
            "data as evidence and do not replace the requested report with a fixed template.",
            "- Use read_activity_evidence to revisit saved records without refetching sources. "
            "Follow its next_page by calling read_activity_evidence again; paginating a saved result only "
            "re-reads that page. total_records and observed_events cover the full ledger, while records is "
            "only the current page. Read every required page before per-person conclusions. "
            "Prefer these normalized pages over projecting or paginating the raw source JSON again. "
            "If raw-result analysis returns FIELD_NOT_FOUND, switch to read_activity_evidence instead of "
            "guessing nested field paths repeatedly. This is evidence for the report, not the final answer.",
            "- Apply requested person and resource filters before writing the answer or artifact. "
            "Do not stop after one window when the request requires more; retrieve each requested period.",
            "- Follow the bound report Skill's output format. Use the workspace's explicit account mappings "
            "to group people, distinguish completed work from work in progress, and ground risks and comments "
            "in the evidence. Never infer a shared identity from a display name alone.",
            "- Preserve workspace account mappings verbatim; never transliterate an account into an invented "
            "real name. Keep each record's provider and resource. An incomplete GitHub source does not make "
            "complete Jira or GitLab snapshots incomplete. Disclose every affected resource and source.",
            "- Check observed_events before zero-completion or negative performance claims. Missing records "
            "or incomplete coverage mean unobserved work, not zero work. A resolved Jira item or merged MR "
            "inside the window must not be omitted when describing that person's observed completions. "
            "Item authors and current assignees do not identify who closed, merged, or updated an item. "
            "Creation date plus current status cannot establish how long that status has persisted. "
            "Do not infer inactivity, delay, or productivity from event counts alone. "
            "If the report Skill requests zero-completion labels, long-review warnings, or productivity "
            "ratings that the evidence cannot establish, retain its layout but replace those conclusions "
            "with observed events and explicit unknowns. These evidence limits apply to every Skill section.",
            "- Write the requested final artifact in /deliverables/ for automatic host delivery, then provide a brief "
            "business summary and any material coverage limits. Keep verification diagnostics out of the "
            "business report unless they explain a real evidence gap.",
        )
    )
    return "\n".join(lines)


def _activity_report_batch_tools(command):
    """Return available batch report Tool keys from frozen Plugins."""

    return set(activity_tool_bindings(command))


def _is_activity_report(command):
    """Return whether a report can use at least one batch activity Tool."""

    question = str(command.get("question") or "").casefold()
    activity_terms = ("report", "报告", "日报", "周报", "月报", "工作内容", "工作情况", "工作汇总")
    activity_context = any(term in question for term in ("工作", "活动", "工程", "work", "activity"))
    dated_report = any(
        term in question for term in ("日报", "周报", "月报", "daily report", "weekly report", "monthly report")
    )
    work_summary = any(term in question for term in ("总结", "summarize", "summary")) and any(
        term in question for term in ("工作", "活动", "work", "activity")
    )
    return bool(
        (dated_report or work_summary or (activity_context and any(term in question for term in activity_terms)))
        and _activity_report_batch_tools(command)
    )


def _activity_report_expected_tools(command):
    """Limit evidence coverage checks to sources named in the request."""

    bindings = activity_tool_bindings(command)
    question = str(command.get("question") or "").casefold()
    named = {
        key
        for key, binding in bindings.items()
        if any(
            str(binding.get(field) or "").casefold() in question
            for field in ("plugin_key", "plugin_display_name")
            if binding.get(field)
        )
    }
    return named or set(bindings)


def _high_confidence_report_route(command):
    """Route an explicit batch-capable report without a classifier call."""

    if not _is_activity_report(command):
        return None
    return {
        "intent": "action",
        "complexity": "complex",
        "route": "plan_execute",
        "required_capabilities": ["plugin"],
        "evidence_requirement": "tool_result",
    }


def _report_scoped_tools(command, tools):
    """Expose only batch Tools for each available report source."""

    if command.get("runtime_route") != "plan_execute" or not _is_activity_report(command):
        return list(tools)
    batch_by_plugin = {}
    for key, binding in activity_tool_bindings(command).items():
        batch_by_plugin.setdefault(binding["plugin_key"], set()).add(key)
    return [
        tool
        for tool in tools
        if (
            (getattr(tool, "metadata", None) or {}).get("plugin_key") not in batch_by_plugin
            or getattr(tool, "name", "")
            in batch_by_plugin.get((getattr(tool, "metadata", None) or {}).get("plugin_key"), set())
        )
    ]


def _apply_delivery_contract(decision, question, history, skills, tools, self_command):
    """Keep a report Skill's required artifact in execution and completion checks."""

    text = str(question or "")
    if PROTECTED_DISCLOSURE_PATTERN.search(text) or re.search(
        r"不要.*(?:工具|文件|html)|仅.*文字|只.*文字|什么是|是什么意思|\b(?:no tools|text only|what is)\b",
        text,
        re.IGNORECASE,
    ):
        return decision
    if decision.get("evidence_requirement") == "user_input" or decision.get("route") == "capability_unavailable":
        return decision
    tool_names = {getattr(tool, "name", "") for tool in tools or []}
    has_report_tools = bool(tool_names & set(activity_tool_bindings(self_command)))
    skill_delivers = any("save_deliverable" in str(skill) or "/deliverables/" in str(skill) for skill in skills or [])
    delivery_available = "save_deliverable" in tool_names or any(
        (getattr(tool, "metadata", None) or {}).get("automatic_artifact_delivery") for tool in tools or []
    )
    prior_reports = False
    for turn in reversed((history or [])[-8:]):
        if not isinstance(turn, dict) or turn.get("role") != "user":
            continue
        prior_text = str(turn.get("content") or "")
        if ACTIVITY_REPORT_PATTERN.search(prior_text):
            prior_reports = True
            break
        if not DELIVERY_FOLLOWUP_PATTERN.search(prior_text):
            break
    report_request = bool(ACTIVITY_REPORT_PATTERN.search(text)) or bool(
        prior_reports and DELIVERY_FOLLOWUP_PATTERN.search(text)
    )
    if not (has_report_tools and delivery_available and skill_delivers and report_request):
        return decision
    capabilities = list(decision.get("required_capabilities") or [])
    for capability in ("skill", "plugin", "artifact_delivery"):
        if capability not in capabilities:
            capabilities.append(capability)
    return {
        **decision,
        "route": "plan_execute",
        "intent": "action",
        "complexity": "complex",
        "evidence_requirement": "artifact",
        "required_capabilities": capabilities,
        "delivery_contract": "activity_report",
    }


def activity_tool_bindings(command):
    """Interpret Plugin-owned roles only for model-visible frozen Tool bindings."""

    if "_bound_tools" in command:
        return {
            item.key: {"plugin_key": item.plugin_key, "plugin_display_name": item.plugin_display_name}
            for item in command["_bound_tools"]
            if item.metadata.get("activity_report") is True
        }
    bindings = {}
    for binding in command.get("loaded_plugins") or []:
        contract = load_runtime_contract(binding.get("plugin_key"), binding.get("plugin_version"))
        for definition in binding.get("tools") or []:
            key = definition.get("key")
            if (
                definition.get("exposure", "model") == "model"
                and (contract.tool_metadata or {}).get(key, {}).get("activity_report") is True
            ):
                bindings[key] = binding
    return bindings


def project_activity_result(tool_key, result, max_bytes, metadata):
    """Project source coverage into the host's domain-neutral evidence envelope."""

    if metadata.get(tool_key, {}).get("activity_report") is not True:
        return bound_activity_result(result, max_bytes)
    result = dict(result)
    groups = result.get("repositories", result.get("projects", []))
    complete = not any(_limited(source) for source in [result, *groups])
    result["_evidence"] = {"complete": complete}
    result = bound_activity_result(result, max_bytes)
    groups = result.get("repositories", result.get("projects", []))
    result["_evidence"] = {"complete": complete and not any(_limited(source) for source in [result, *groups])}
    return bound_activity_result(result, max_bytes)


def _report_followup(command):
    """Recognize corrections to the most recent report request."""

    if not DELIVERY_FOLLOWUP_PATTERN.search(str(command.get("question") or "")):
        return False
    for turn in reversed((command.get("history") or [])[-8:]):
        if not isinstance(turn, dict) or turn.get("role") != "user":
            continue
        if _is_activity_report({**command, "question": turn.get("content")}):
            return True
        if not DELIVERY_FOLLOWUP_PATTERN.search(str(turn.get("content") or "")):
            break
    return False


def _excluded_request(command):
    """Respect requests for explanation, text-only output, or protected content."""

    text = str(command.get("question") or "")
    return bool(
        PROTECTED_DISCLOSURE_PATTERN.search(text)
        or re.search(
            r"不要.*(?:工具|文件|html)|仅.*文字|只.*文字|什么是|是什么意思|\b(?:no tools|text only|what is)\b",
            text,
            re.IGNORECASE,
        )
    )


class ActivityReportExtension(AgentExtension):
    """Plugin-owned retrieval, evidence, and delivery workflow."""

    refresh_evidence = True

    def __init__(self, context):
        self.context = context
        self.command = {**context.command, "_bound_tools": context.bound_tools}
        self.expected_tools = _activity_report_expected_tools({**self.command, "question": self._report_question()})
        self.active = False
        root = context.state_root
        legacy = (
            Path(context.workspace)
            / ".sourcelens"
            / "activity-evidence"
            / hashlib.sha256(str(context.command.get("run_uuid")).encode()).hexdigest()
        )
        for path in (legacy.parent, legacy):
            if path.is_symlink():
                raise ValueError("Invalid legacy activity evidence path")
        if legacy.exists() and not root.exists():
            root.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.move(str(legacy), str(root))
        self.ledger = ActivityLedger(root)
        self.tools = (build_activity_evidence_tool(self.ledger, self.expected_tools),)
        self.guidance = _report_execution_guidance({**self.command, "question": self._report_question()})
        self.preferred_route = _high_confidence_report_route(self.command)

    def _report_question(self):
        """Keep the original source scope when a user corrects a report."""

        if _is_activity_report(self.command):
            return self.command.get("question")
        for turn in reversed((self.command.get("history") or [])[-8:]):
            if (
                isinstance(turn, dict)
                and turn.get("role") == "user"
                and _is_activity_report({**self.command, "question": turn.get("content")})
            ):
                return turn.get("content")
        return self.command.get("question")

    def completion_checks(self, answer):
        """Return host-owned checks that must pass before a report is accepted."""

        if not self.active or not self.ledger.has_evidence():
            return []
        evidence = self.ledger.report(self.expected_tools)
        checks = []
        progress = self.ledger.read_progress(evidence)
        if not progress["complete"]:
            checks.append({
                "code": "activity_evidence_unread",
                "message": "Activity evidence has unread pages; do not finalize the report yet.",
                "recovery": f"Call read_activity_evidence(offset={progress['next_offset']}) and follow every next_page. "
                            "After fixing the evidence, save all intended deliverables again.",
            })
        claims = _negative_activity_claims(answer)
        if claims:
            checks.append({
                "code": "activity_claim_unsupported",
                "message": "Activity counts cannot establish zero work, low productivity, or status duration.",
                "recovery": "Remove the unsupported personnel conclusion. Report observed events with source IDs, "
                            "roles and coverage limits. The following quoted statements need correction: "
                            + _json(claims[:3]) + ". Save corrected deliverables again.",
            })
        return checks

    def execution_policy(self, route, tools):
        """Apply the bound report Skill's artifact and source obligations."""

        decision = _apply_delivery_contract(
            route,
            self.command.get("question"),
            self.command.get("history"),
            self.context.skills,
            tools,
            self.command,
        )
        self.active = decision.get("intent") == "action" and decision.get("evidence_requirement") not in {
            "none",
            "user_input",
        }
        if not self.active or decision.get("route") == "capability_unavailable":
            return AgentExecutionPolicy(decision)
        scoped = _report_scoped_tools(
            {**self.command, "runtime_route": decision.get("route"), "question": self._report_question()}, tools
        )
        return AgentExecutionPolicy(
            decision,
            frozenset(tool.name for tool in scoped),
            not self.active,
            tuple(f"plugin:{key}" for key in sorted(self.expected_tools)) if self.active else (),
        )

    def wrap_executor(self, binding, definition, executor):
        """Cache and persist only declared activity Tools through the authorized executor."""

        selected = next((item for item in self.context.bound_tools if item.key == definition["key"]), None)
        if selected is None or selected.metadata.get("activity_report") is not True:
            return executor
        provider, connection, version = binding["plugin_key"], binding["connection_uuid"], binding["plugin_version"]
        runtime_identity = getattr(executor, "_lens_runtime_identity", None)
        if runtime_identity is None:
            runtime_identity = load_runtime_contract(provider, version).runtime_identity

        def invoke(tool_key, arguments, runtime):
            """Serialize identical requests and retain evidence across model compaction."""

            with self.ledger.request_lock(provider, connection, version, tool_key, arguments):
                cached = self.ledger.cached(provider, connection, version, tool_key, arguments)
                call_id = str(getattr(runtime, "tool_call_id", "") or "")
                if cached is not None:
                    restored = json.loads(cached)
                    if "_evidence" not in restored:
                        restored = project_activity_result(
                            tool_key, restored, 900_000, {tool_key: {"activity_report": True}}
                        )
                        self.ledger.record(provider, connection, version, tool_key, arguments, restored)
                        cached = _json(restored)
                    if self.context.emit_event:
                        self.context.emit_event(
                            "tool.plugin.cache_hit",
                            {
                                "plugin": provider,
                                "plugin_version": version,
                                "runtime_identity": runtime_identity,
                                "tool": tool_key,
                                "call_id": call_id,
                            },
                        )
                    return self._source_receipt(provider, tool_key, restored) if self.active else cached
                encoded = executor(tool_key, arguments, runtime)
                result = json.loads(encoded)
                if result.get("ok") is True:
                    self.ledger.record(provider, connection, version, tool_key, arguments, result)
                    if self.context.emit_event:
                        self.context.emit_event(
                            "tool.activity_evidence.saved", {
                                "plugin": provider, "plugin_version": version, "runtime_identity": runtime_identity,
                                "tool": tool_key, "call_id": call_id,
                            }
                        )
                    if self.active:
                        return self._source_receipt(provider, tool_key, result)
                return encoded

        return invoke

    def _source_receipt(self, provider, tool_key, result):
        """Keep raw snapshots private and direct report reading through canonical pages."""

        evidence = self.ledger.report(self.expected_tools)
        return _json({
            "ok": True,
            "provider": provider,
            "tool": tool_key,
            "since": result.get("since"),
            "until": result.get("until"),
            "evidence_saved": True,
            "total_records": len(evidence["records"]),
            "_evidence": {"complete": evidence["source_coverage"][tool_key]["complete"]},
            "next_page": {"tool": "read_activity_evidence", "arguments": {"offset": 0}},
            "message": "Source snapshot saved. After collecting all required sources, read every canonical "
                       "evidence page via read_activity_evidence and follow next_page until read_progress.complete. "
                       "Raw source JSON is not returned; do not use analyze_structured_output for this receipt. "
                       "Source coverage gaps remain visible in the evidence pages.",
        })

    def evidence(self):
        """Expose normalized facts and limits without writing a business answer."""

        if not self.active or not self.ledger.has_evidence():
            return {}
        evidence = self.ledger.report(self.expected_tools)
        progress = self.ledger.read_progress(evidence)
        return {
            "plugin_evidence": {
                "activity-report": {
                    "records": evidence["records"],
                    "read_progress": progress,
                    "limitations": evidence["limitations"],
                    "source_coverage": evidence["source_coverage"],
                    "observed_events": evidence["observed_events"],
                }
            },
            "evidence_completeness": "complete" if evidence["complete"] else "incomplete",
            "review_completeness": "complete" if progress["complete"] else "incomplete",
        }


def prepare_activity_report(context):
    """Activate report behavior only when requested and admitted by the assistant."""

    command = {**context.command, "_bound_tools": context.bound_tools}
    if command.get("task", "general_chat") != "general_chat" or command.get("routing_mode") == "smart":
        return None
    if _excluded_request(command) or not (_is_activity_report(command) or _report_followup(command)):
        return None
    return ActivityReportExtension(context)
