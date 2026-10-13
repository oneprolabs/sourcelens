"""Durable, attributable source evidence for model-authored activity reports."""

import hashlib
import json
import os
import re
import shutil
import threading
import uuid
from datetime import datetime
from pathlib import Path

from lensnode.plugin_agent import AgentExecutionPolicy, AgentExtension, plugin_state_root
from lensnode.plugin_package_loader import load_runtime_contract


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


def build_activity_evidence_tool(ledger, expected_tools):
    """Expose bounded saved evidence without staging files or ending the agent."""

    from langchain.tools import tool

    @tool("read_activity_evidence")
    def read_activity_evidence(offset: int = 0, limit: int = 50) -> str:
        """Read normalized activity evidence saved by the batch source tools.

        Use offset and limit to revisit records without refetching sources. Records
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
        end = min(offset + limit, len(records))
        return _json(
            {
                "ok": True,
                "records": records[offset:end],
                "total_records": len(records),
                "next_offset": end if end < len(records) else None,
                "windows": evidence["windows"],
                "source_tools": evidence["source_tools"],
                "limitations": evidence["limitations"],
                "source_coverage_complete": evidence["complete"],
                "_evidence": {"complete": evidence["complete"]},
            }
        )

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
            "- The Plugin persists retrieved source results in a private evidence ledger. "
            "Normalize records by verified source identity and event; keep "
            "source IDs, timestamps, and coverage limits. Distinguish current "
            "status from transitions within the requested time window, and "
            "deduplicate exact commit SHAs without inventing identities or equating backports.",
            "- Reuse that ledger for corrections instead of retrieving the same "
            "sources again. Prefer a deterministic template rendered from normalized "
            "data instead of regenerating the entire HTML in each model response.",
            "- Use read_activity_evidence to revisit saved records without refetching sources. "
            "This is evidence for the report, not the final answer.",
            "- Apply requested person and resource filters before writing the answer or artifact. "
            "Do not stop after one window when the request requires more; retrieve each requested period.",
            "- Follow the bound report Skill's output format. Use the workspace's explicit account mappings "
            "to group people, distinguish completed work from work in progress, and ground risks and comments "
            "in the evidence. Never infer a shared identity from a display name alone.",
            "- Generate and deliver the requested final artifact with save_deliverable, then provide a brief "
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
    skill_delivers = any("save_deliverable" in str(skill) for skill in skills or [])
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
    if not (has_report_tools and "save_deliverable" in tool_names and skill_delivers and report_request):
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
                                "tool": tool_key,
                                "parent_call_id": call_id,
                            },
                        )
                    return cached
                encoded = executor(tool_key, arguments, runtime)
                result = json.loads(encoded)
                if result.get("ok") is True:
                    self.ledger.record(provider, connection, version, tool_key, arguments, result)
                    if self.context.emit_event:
                        self.context.emit_event(
                            "tool.activity_evidence.saved", {"tool": tool_key, "parent_call_id": call_id}
                        )
                return encoded

        return invoke

    def evidence(self):
        """Expose normalized facts and limits without writing a business answer."""

        if not self.active or not self.ledger.has_evidence():
            return {}
        evidence = self.ledger.report(self.expected_tools)
        return {
            "plugin_evidence": {
                "activity-report": {
                    "records": evidence["records"],
                    "limitations": evidence["limitations"],
                }
            },
            "evidence_completeness": "complete" if evidence["complete"] else "incomplete",
        }


def prepare_activity_report(context):
    """Activate report behavior only when requested and admitted by the assistant."""

    command = {**context.command, "_bound_tools": context.bound_tools}
    if command.get("task", "general_chat") != "general_chat" or command.get("routing_mode") == "smart":
        return None
    if _excluded_request(command) or not (_is_activity_report(command) or _report_followup(command)):
        return None
    return ActivityReportExtension(context)
