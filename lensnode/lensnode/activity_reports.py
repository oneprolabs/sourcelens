"""Durable source evidence and deterministic, attributable activity reports."""

import hashlib
import html
import json
import os
import shutil
import threading
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain_core.messages import AIMessage

ACTIVITY_TOOLS = frozenset({"github_activity_summary", "gitlab_activity_summary", "jira_activity_summary"})


def activity_ledger_root(workspace, run_uuid):
    """Isolate private evidence by Run without accepting a caller path."""

    key = hashlib.sha256(str(run_uuid).encode()).hexdigest()
    return Path(workspace) / ".sourcelens" / "activity-evidence" / key


def cleanup_activity_evidence(workspace, run_uuid):
    """Remove scratch evidence after durable delivery or checkpoint expiry."""

    root = activity_ledger_root(workspace, run_uuid)
    if root.is_symlink():
        root.unlink()
    elif root.exists():
        shutil.rmtree(root)


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
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        self.request_locks = {}
        self.entries = {}
        self.staged_fingerprint = None
        self.staged_candidates = {}
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

    def request_lock(self, provider, connection, version, tool, arguments):
        """Serialize identical concurrent activity requests within this Run."""

        key = self._key(provider, connection, version, tool, arguments)
        with self.lock:
            return self.request_locks.setdefault(key, threading.Lock())

    def record(self, provider, connection, version, tool, arguments, result):
        """Persist projected evidence atomically before model compaction."""

        if tool not in ACTIVITY_TOOLS or not isinstance(result, dict) or result.get("ok") is not True:
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

    def report(self, expected_tools, language="zh"):
        """Render facts from saved snapshots without asking a model to infer work."""

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
        for entry in sorted(latest.values(), key=lambda entry: (entry["provider"], entry["sequence"])):
            provider, result = entry["provider"], entry["result"]
            tools.add(entry["tool"])
            since, until = result.get("since", ""), result.get("until", "")
            windows.add((since, until))
            groups = result.get("repositories", result.get("projects"))
            if not isinstance(groups, list):
                groups = [{"project": "", "issues": result.get("items", [])}]
            sources = [result, *groups]
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
        if len(windows) > 1:
            limitations.append("来源时间窗口不一致 / source time windows differ")
        if not windows:
            limitations.append("尚无可验证的来源材料 / no source evidence")
        ordered = sorted(records.values(), key=lambda item: (item["person_id"], item["timestamp"], item["id"]))
        limitations = sorted(set(limitations))
        report = {
            "records": ordered,
            "limitations": limitations,
            "complete": not limitations,
            "windows": sorted(windows),
            "source_tools": sorted(tools),
            "source_snapshots": [
                {"provider": entry["provider"], "arguments": entry["arguments"], "result": entry["result"]}
                for entry in sorted(latest.values(), key=lambda entry: (entry["provider"], entry["sequence"]))
            ],
        }
        report["html"], report["answer"] = _render(report, language)
        return report


def _limited(source):
    """Read only explicit provider coverage metadata."""

    if not isinstance(source, dict):
        return True
    flag = source.get("possibly_truncated")
    return (
        bool(source.get("errors"))
        or source.get("projects_truncated") is True
        or flag is True
        or (isinstance(flag, dict) and any(value is True for value in flag.values()))
    )


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
    return {
        "provider": provider,
        "person_id": f"{provider}:{connection}:{person}",
        "person": name,
        "sources": [{"provider": provider, "resource": resource, "url": str(item.get("url", ""))}],
        "role": role,
        "kind": kind,
        "id": identity,
        "resources": [resource or str(item.get("project", ""))],
        "title": str(item.get("title", item.get("summary", item.get("message", "")))),
        "timestamp": timestamp,
        "event": event,
        "current_state": str(item.get("state", item.get("status", ""))),
        "url": str(item.get("url", "")),
    }


def _render(report, language):
    """Escape all source text and keep links within HTTP(S)."""

    chinese = str(language).lower().startswith("zh")
    title = "可验证活动汇总" if chinese else "Verified activity summary"
    labels = (
        {
            "updated": "当期更新",
            "committed": "当期提交",
            "created": "当期创建",
            "merged": "当期合并",
            "resolved": "当期解决",
            "closed": "当期关闭",
        }
        if chinese
        else {}
    )
    notes = (
        "人员按来源账号分别列出；负责人及条目作者不代表本次更新的操作者。不同提交 SHA 不自动合并为一项工作。"
        if chinese
        else (
            "People are grouped by source account. Assignees and item authors are not necessarily update actors. "
            "Different commit SHAs are not inferred to be the same work."
        )
    )
    notes += (
        " 汇总依据当前查询的活动快照，不能据此还原全部历史操作或证明未列出的人员没有工作。"
        if chinese
        else (
            " Current activity snapshots do not reconstruct every historical action or prove that unlisted people did no work."
        )
    )
    sections = []
    window_text = "; ".join(f"{lower} — {upper}" for lower, upper in report["windows"])
    delivery = (
        "活动报告与证据文件已生成，可下载查看。"
        if chinese
        else "The activity report and evidence files are available to download."
    )
    answer = [title, window_text, notes, delivery]
    current = None
    for record in report["records"]:
        if record["person_id"] != current:
            current = record["person_id"]
            heading = f"{record['person']} ({record['provider']})"
            sections.append(f"<h2>{html.escape(heading)}</h2>")
            answer.append(f"\n{heading}")
        event = labels.get(record["event"], record["event"])
        text = f"{event} · {', '.join(record['resources'])} · {record['id']} · {record['title']}"
        answer.append(f"- {text}")
        link = ""
        url = record["url"]
        try:
            parsed = urlsplit(url)
        except ValueError:
            parsed = urlsplit("")
        if parsed.scheme in {"https", "http"} and parsed.netloc:
            link = f' <a href="{html.escape(url, quote=True)}" rel="noopener noreferrer">来源 / Source</a>'
        sections.append(
            f"<article><p>{html.escape(text)}{link}</p><small>{html.escape(record['role'])} · "
            f"{html.escape(record['timestamp'])} · {html.escape(record['current_state'])}</small></article>"
        )
    if report["limitations"]:
        answer.extend(["\n覆盖限制 / Coverage limits", *report["limitations"]])
    warnings = "".join(f"<li>{html.escape(item)}</li>" for item in report["limitations"])
    windows = html.escape(window_text)
    document = f"""<!doctype html><html lang="{'zh-CN' if chinese else 'en'}"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>
<style>body{{font:16px/1.7 system-ui,sans-serif;max-width:1000px;margin:auto;padding:24px;background:#f8fafc;color:#172033}}
article{{background:white;padding:12px 18px;margin:10px 0;border:1px solid #dbe3ee;border-radius:10px;overflow-wrap:anywhere}}
small{{color:#475569}}h2{{margin-top:32px}}a{{color:#2563eb}}aside{{padding:16px;background:#fff4d6}}</style>
<h1>{title}</h1><p>{windows}</p><p>{html.escape(notes)}</p>
{'<aside><strong>覆盖限制 / Coverage limits</strong><ul>' + warnings + '</ul></aside>' if warnings else ''}
{''.join(sections)}</html>"""
    return document, "\n".join(answer)


def finalize_activity_report(ledger, expected_tools, command, resources, config, emit_event, language="zh"):
    """Stage the exact report and evidence generated from the final ledger."""

    from .agent_tools import _build_save_deliverable_tool

    report = ledger.report(expected_tools, language)
    root = resources.root.resolve()
    contents = {
        "activity-report.html": report["html"],
        "activity-evidence.json": _json({key: value for key, value in report.items() if key not in {"html", "answer"}}),
    }
    fingerprint = hashlib.sha256(_json(contents).encode()).hexdigest()
    manifest = command.setdefault("_deliverables", {})
    reusable = (
        ledger.staged_fingerprint == fingerprint
        and all(manifest.get(filename) == candidate for filename, candidate in ledger.staged_candidates.items())
        and set(ledger.staged_candidates) == set(contents)
    )
    candidates = dict(ledger.staged_candidates) if reusable else {}
    manifest.clear()
    manifest.update(candidates)
    save = _build_save_deliverable_tool(command, resources, config, emit_event)
    for filename, content in contents.items():
        temporary = root / f".activity-{uuid.uuid4().hex}.tmp"
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, root / filename)
        if not reusable:
            result = json.loads(save.invoke({"path": filename}))
            if result.get("ok") is not True:
                manifest.clear()
                raise RuntimeError("ACTIVITY_REPORT_DELIVERY_FAILED")
    ledger.staged_fingerprint = fingerprint
    ledger.staged_candidates = dict(manifest)
    if emit_event is not None:
        emit_event(
            "deepagents.activity_report.rendered",
            {
                "records": len(report["records"]),
                "complete": report["complete"],
                "html_sha256": hashlib.sha256(report["html"].encode()).hexdigest(),
            },
        )
    return report


def build_activity_report_tool(ledger, expected_tools, command, resources, config, emit_event, language="zh"):
    """Let the agent finish a report without generating HTML tokens."""

    from langchain.tools import tool

    @tool("render_activity_report")
    def render_activity_report() -> str:
        """Render and stage the activity report from saved batch-source evidence.

        Call each available batch activity tool first with the same requested time
        window. Then call this tool once. It writes the report and evidence JSON,
        stages their downloads, and returns the grounded final answer. Do not write
        HTML yourself or refetch sources to correct a report.
        """

        report = finalize_activity_report(ledger, expected_tools, command, resources, config, emit_event, language)
        return _json(
            {
                "ok": True,
                "answer": report["answer"],
                "complete": report["complete"],
                "files": ["activity-report.html", "activity-evidence.json"],
            }
        )

    return render_activity_report


class ActivityReportMiddleware(AgentMiddleware):
    """Finish from saved evidence before another model-generated report turn."""

    def __init__(self, ledger, expected_tools, render):
        self.ledger = ledger
        self.expected_tools = set(expected_tools)
        self.render = render

    @hook_config(can_jump_to=["end"])
    def before_model(self, state, runtime):
        """Stop generation once each requested source has produced evidence."""

        with self.ledger.lock:
            retrieved = {entry["tool"] for entry in self.ledger.entries.values()}
        if not self.expected_tools or not self.expected_tools.issubset(retrieved):
            return None
        report = self.render()
        return {"jump_to": "end", "messages": [AIMessage(content=report["answer"])]}

    @hook_config(can_jump_to=["end"])
    async def abefore_model(self, state, runtime):
        """Move file upload out of the asynchronous graph's event loop."""

        import asyncio

        return await asyncio.to_thread(self.before_model, state, runtime)
