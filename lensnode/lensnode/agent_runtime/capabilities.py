"""Capability recovery and evidence boundaries for agent runs."""

import hashlib
import json
import time
from collections import defaultdict

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

from ..checkpoint import CheckpointResumeError
from ..gateway_model import _tool_result_metadata
from .capability_protocol import is_capability_family
from .messages import normalize_plan_steps as _normalize_plan_steps
class CapabilityBoundaryMiddleware(AgentMiddleware):
    """Apply bounded recovery without stopping the whole agent run."""

    def __init__(
        self,
        emit_event=None,
        required_capabilities=None,
        require_initial_plan=False,
        planning_reasoning_effort=None,
        on_state_change=None,
        max_tool_calls=0,
    ):
        self.emit_event = emit_event
        self.required_capabilities = set(required_capabilities or [])
        self.require_initial_plan = require_initial_plan
        self.planning_reasoning_effort = planning_reasoning_effort
        self.on_state_change = on_state_change
        self.max_tool_calls = max(int(max_tool_calls or 0), 0)
        self.tool_call_count = 0
        self.planning_started_at = time.monotonic()
        self.initial_plan_exists = False
        self.blocked_tools = set()
        self.blocked_capabilities = set()
        self.blocked_sources = set()
        self.blocked_requests = set()
        self.failure_counts = defaultdict(int)
        self.capability_failure_counts = defaultdict(int)
        self.capability_correction_counts = defaultdict(int)
        self.source_failure_counts = defaultdict(int)
        self.source_correction_counts = defaultdict(int)
        self.success_count = 0
        self.successful_capabilities = set()
        self.successful_evidence = []
        self.failed_capabilities = set()
        self.recovered_capabilities = set()
        self.failed_sources = set()
        self.recovered_sources = set()
        self.correction_recovery_count = 0
        self.alternative_recovery_count = 0
        self.termination_detail = {}
        self.exhaustion_details = []
        self.failure_records = {}

    def export_state(self):
        """Return a JSON-safe snapshot of execution-gate state."""

        return {
            "initial_plan_exists": self.initial_plan_exists,
            "blocked_tools": sorted(self.blocked_tools),
            "blocked_capabilities": sorted(self.blocked_capabilities),
            "blocked_sources": sorted(self.blocked_sources),
            "blocked_requests": [
                list(key) for key in sorted(self.blocked_requests)
            ],
            "failure_counts": [
                [*key, count]
                for key, count in sorted(self.failure_counts.items())
            ],
            "capability_failure_counts": dict(
                self.capability_failure_counts
            ),
            "capability_correction_counts": dict(
                self.capability_correction_counts
            ),
            "source_failure_counts": dict(self.source_failure_counts),
            "source_correction_counts": dict(
                self.source_correction_counts
            ),
            "success_count": self.success_count,
            "successful_capabilities": sorted(
                self.successful_capabilities
            ),
            "successful_evidence": [
                dict(item) for item in self.successful_evidence
            ],
            "failed_capabilities": sorted(self.failed_capabilities),
            "recovered_capabilities": sorted(
                self.recovered_capabilities
            ),
            "failed_sources": sorted(self.failed_sources),
            "recovered_sources": sorted(self.recovered_sources),
            "correction_recovery_count": self.correction_recovery_count,
            "alternative_recovery_count": self.alternative_recovery_count,
            "termination_detail": dict(self.termination_detail),
            "exhaustion_details": list(self.exhaustion_details),
            "failure_records": [
                [*key, detail]
                for key, detail in self.failure_records.items()
            ],
            "tool_call_count": self.tool_call_count,
        }

    def restore_state(self, state):
        """Restore a previously exported execution-gate snapshot."""

        if not isinstance(state, dict):
            raise CheckpointResumeError(
                "Resume checkpoint has invalid execution-gate state."
            )
        try:
            self.initial_plan_exists = bool(
                state.get("initial_plan_exists", False)
            )
            self.blocked_tools = set(state.get("blocked_tools") or [])
            self.blocked_capabilities = set(
                state.get("blocked_capabilities") or []
            )
            self.blocked_sources = set(
                state.get("blocked_sources") or []
            )
            self.blocked_requests = {
                tuple(item[:2])
                for item in state.get("blocked_requests") or []
            }
            self.failure_counts = defaultdict(
                int,
                {
                    tuple(item[:3]): int(item[3])
                    for item in state.get("failure_counts") or []
                },
            )
            self.capability_failure_counts = defaultdict(
                int,
                state.get("capability_failure_counts") or {},
            )
            self.capability_correction_counts = defaultdict(
                int,
                state.get("capability_correction_counts") or {},
            )
            self.source_failure_counts = defaultdict(
                int,
                state.get("source_failure_counts") or {},
            )
            self.source_correction_counts = defaultdict(
                int,
                state.get("source_correction_counts") or {},
            )
            self.success_count = int(state.get("success_count") or 0)
            self.successful_capabilities = set(
                state.get("successful_capabilities") or []
            )
            self.successful_evidence = [
                dict(item)
                for item in state.get("successful_evidence") or []
            ]
            self.failed_capabilities = set(
                state.get("failed_capabilities") or []
            )
            self.recovered_capabilities = set(
                state.get("recovered_capabilities") or []
            )
            self.failed_sources = set(state.get("failed_sources") or [])
            self.recovered_sources = set(
                state.get("recovered_sources") or []
            )
            self.correction_recovery_count = int(
                state.get("correction_recovery_count") or 0
            )
            self.alternative_recovery_count = int(
                state.get("alternative_recovery_count") or 0
            )
            self.termination_detail = dict(
                state.get("termination_detail") or {}
            )
            self.exhaustion_details = list(
                state.get("exhaustion_details") or []
            )
            self.failure_records = {
                tuple(item[:3]): dict(item[3])
                for item in state.get("failure_records") or []
            }
            self.tool_call_count = max(
                int(state.get("tool_call_count") or 0),
                0,
            )
        except (TypeError, ValueError, IndexError) as exc:
            raise CheckpointResumeError(
                "Resume checkpoint has invalid execution-gate state."
            ) from exc

    def _notify_state_change(self):
        if self.on_state_change is not None:
            self.on_state_change(self.export_state())

    @property
    def warning_count(self):
        """Return the number of warning-only request failures."""

        return sum(
            1
            for detail in self.failure_records.values()
            if detail.get("scope") == "warning"
        )

    @property
    def outcome(self):
        """Return the user-facing business outcome for this run."""

        if not self.termination_detail:
            return "completed"
        return "partial" if self.success_count else "blocked"

    @staticmethod
    def _tool_name(request):
        tool_call = request.tool_call or {}
        return str(
            tool_call.get("name")
            or getattr(request.tool, "name", None)
            or "tool"
        )

    @staticmethod
    def _capability_name(tool_name):
        if tool_name.startswith("mcp__") or tool_name == "tool_search":
            return "mcp"
        if tool_name.startswith("run_skill") or tool_name == "call_skill_api":
            return "skill"
        if "workspace" in tool_name or tool_name in {
            "find_files",
            "git_diff",
            "git_log",
            "summarize_recent_changes",
        }:
            return "workspace"
        if tool_name == "save_deliverable":
            return "artifact_delivery"
        return "tool"

    @classmethod
    def _capability_for_tool(cls, tool, tool_name=None):
        """Return the explicit capability family declared by a Tool."""

        metadata = getattr(tool, "metadata", None) or {}
        if isinstance(metadata, dict):
            family = metadata.get("capability_family")
            if is_capability_family(family):
                return family
        return cls._capability_name(
            str(tool_name or getattr(tool, "name", "") or "")
        )

    @classmethod
    def _evidence_capability(cls, tool_name):
        """Return the business capability proven by a tool result."""

        capability = cls._capability_name(tool_name)
        if tool_name == "tool_search" or capability == "tool":
            return None
        return capability

    @classmethod
    def _evidence_capability_for_tool(cls, tool, tool_name=None):
        """Return evidence capability using Tool metadata when present."""

        resolved_name = str(tool_name or getattr(tool, "name", "") or "")
        capability = cls._capability_for_tool(tool, resolved_name)
        if resolved_name == "tool_search" or capability == "tool":
            return None
        return capability

    @staticmethod
    def _parse_result(result):
        content = getattr(result, "content", None)
        if not isinstance(content, str):
            return None
        try:
            value = json.loads(content)
        except (TypeError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    @staticmethod
    def _normalized_arguments(request):
        tool_call = request.tool_call or {}
        arguments = tool_call.get("args") or {}
        try:
            normalized = json.dumps(
                arguments,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        except (TypeError, ValueError):
            normalized = str(arguments)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def _record_recovery(self, capability, source):
        pending_sources = self.failed_sources - self.recovered_sources
        recovery_type = None
        recovered_sources = set()
        if source in pending_sources:
            self.correction_recovery_count += 1
            self.recovered_sources.add(source)
            self.blocked_sources.discard(source)
            recovery_type = "corrected_request"
            recovered_sources.add(source)
        else:
            alternatives = {
                detail.get("source")
                for detail in self.failure_records.values()
                if detail.get("capability") != capability
                and (
                    detail.get("capability") in self.required_capabilities
                    or not self.required_capabilities
                )
                and detail.get("scope") == "unresolved"
                and detail.get("source")
            }
            if (
                capability in self.required_capabilities
                or not self.required_capabilities
            ) and alternatives:
                self.alternative_recovery_count += 1
                self.recovered_sources.update(alternatives)
                recovery_type = "alternative_capability"
                recovered_sources.update(alternatives)
        for detail in self.failure_records.values():
            if (
                detail.get("source") in recovered_sources
                and detail.get("scope") == "unresolved"
            ):
                detail["scope"] = "recovered"
                detail["affects_required_evidence"] = False
        if recovery_type is None:
            recovered = [
                detail
                for detail in self.failure_records.values()
                if detail.get("capability") == capability
                and detail.get("source") == source
                and detail.get("scope") == "warning"
            ]
            if recovered:
                for detail in recovered:
                    detail["scope"] = "recovered"
                    detail["affects_required_evidence"] = False
                self.correction_recovery_count += 1
                recovery_type = "corrected_request"
        pending_capability_sources = {
            detail.get("source")
            for detail in self.failure_records.values()
            if detail.get("capability") == capability
            and detail.get("scope") == "unresolved"
        }
        if capability in self.failed_capabilities:
            if pending_capability_sources:
                self.recovered_capabilities.discard(capability)
            else:
                self.recovered_capabilities.add(capability)
        if recovery_type and self.emit_event is not None:
            self.emit_event(
                "deepagents.capability.recovered",
                {
                    "capability": capability,
                    "source": source,
                    "recovery_type": recovery_type,
                    "correction_recovery_count": (
                        self.correction_recovery_count
                    ),
                    "alternative_recovery_count": (
                        self.alternative_recovery_count
                    ),
                },
            )

    @staticmethod
    def _source_scope(capability, tool_name, request):
        """Return a secret-safe source scope for one invocation."""

        tool_call = request.tool_call or {}
        arguments = tool_call.get("args") or {}
        if capability == "skill" and isinstance(arguments, dict):
            skill = str(arguments.get("skill") or "").strip()[:128]
            if skill:
                return f"skill:{skill}"
            return f"skill:{tool_name}"
        if capability in {"mcp", "plugin"}:
            return f"{capability}:{tool_name}"
        return f"tool:{tool_name}"

    def _record_success(self, capability, tool_name, request):
        if capability is None:
            return
        source = self._source_scope(capability, tool_name, request)
        request_sha256 = self._normalized_arguments(request)
        request_scope = (tool_name, request_sha256)
        self.blocked_requests.discard(request_scope)
        for key in list(self.failure_counts):
            if key[0] == tool_name and key[2] == request_sha256:
                del self.failure_counts[key]
        if source in self.source_failure_counts:
            self.source_failure_counts[source] = 0
        if source in self.source_correction_counts:
            self.source_correction_counts[source] = 0
        self.success_count += 1
        self.successful_capabilities.add(capability)
        self.successful_evidence.append(
            {
                "capability": capability,
                "tool": tool_name,
                "source": source,
                "request_sha256": request_sha256,
            }
        )
        self._record_recovery(capability, source)

    def _record_warning(
        self,
        key,
        capability,
        error_type,
        tool_name,
        source,
        request_sha256,
    ):
        detail = {
            "capability": capability,
            "error_type": error_type,
            "tool": tool_name,
            "source": source,
            "request_sha256": request_sha256,
            "scope": "warning",
            "required": capability in self.required_capabilities,
            "affects_required_evidence": False,
        }
        self.failure_records[key] = detail
        if self.emit_event is not None:
            self.emit_event("deepagents.capability.warning", dict(detail))

    def failure_diagnostics(self, required_capabilities, outcome):
        """Return secret-safe terminal failure scope diagnostics."""

        required = set(required_capabilities or [])
        failures = []
        for detail in self.failure_records.values():
            item = dict(detail)
            item["required"] = item.get("capability") in required
            item["affects_required_evidence"] = bool(
                item["required"]
                and item.get("scope") == "unresolved"
                and outcome != "completed"
            )
            failures.append(item)
        return {
            "unresolved_failure_count": sum(
                item.get("scope") == "unresolved" for item in failures
            ),
            "recovered_failure_count": sum(
                item.get("scope") == "recovered" for item in failures
            ),
            "warning_count": sum(
                item.get("scope") == "warning" for item in failures
            ),
            "failures": failures[:12],
        }

    @staticmethod
    def _is_non_idempotent_write(request):
        metadata = getattr(request.tool, "metadata", None) or {}
        if not isinstance(metadata, dict):
            return False
        is_write = (
            metadata.get("operation") == "write"
            or metadata.get("read_only") is False
            or metadata.get("side_effects") is True
        )
        if not is_write:
            return False
        return metadata.get("idempotent") is not True

    def _failure_budget(self, request, error_type):
        if error_type == "transient" and self._is_non_idempotent_write(
            request
        ):
            return 1
        if error_type in {"transient", "request", "tool"}:
            return 2
        return 1

    @staticmethod
    def _recovery_message(error_type):
        if error_type == "configuration":
            return "Ask an administrator to configure or authorize it."
        if error_type == "policy":
            return "Continue from the evidence already collected."
        if error_type == "transient":
            return "Retry later or use another available capability."
        if error_type == "request":
            return "Provide the missing or corrected input, then retry."
        return "Use another available capability or contact an administrator."

    def _record_result(self, request, result):
        tool_name = self._tool_name(request)
        capability = self._evidence_capability_for_tool(
            request.tool,
            tool_name,
        )
        request_sha256 = self._normalized_arguments(request)
        request_scope = (tool_name, request_sha256)
        payload = self._parse_result(result)
        if payload is None or "ok" not in payload:
            if not tool_name.startswith("mcp__"):
                return result
            if getattr(result, "status", None) != "error":
                self._record_success("mcp", tool_name, request)
                return result
            payload = {"ok": False, "error": "MCP_TOOL_FAILED"}
        if payload.get("ok") is True:
            self._record_success(capability, tool_name, request)
            return result
        if capability is None:
            return result

        metadata = _tool_result_metadata(payload, tool_name)
        error_type = metadata.get("error_type") or "tool"
        source = self._source_scope(capability, tool_name, request)
        key = (
            tool_name,
            error_type,
            request_sha256,
        )
        self.failure_counts[key] += 1
        self.capability_failure_counts[capability] += 1
        self.source_failure_counts[source] += 1
        if error_type in {"request", "tool"}:
            self.capability_correction_counts[capability] += 1
            self.source_correction_counts[source] += 1
        exact_failures = self.failure_counts[key]
        capability_failures = self.capability_failure_counts[capability]
        capability_corrections = self.capability_correction_counts[
            capability
        ]
        source_failures = self.source_failure_counts[source]
        source_corrections = self.source_correction_counts[source]
        block_source = error_type in {"configuration", "policy"}
        block_request = exact_failures >= self._failure_budget(
            request,
            error_type,
        )
        if not block_source and not block_request:
            self._record_warning(
                key,
                capability,
                error_type,
                tool_name,
                source,
                request_sha256,
            )
            return result

        self.failed_capabilities.add(capability)
        self.recovered_capabilities.discard(capability)
        self.failed_sources.add(source)
        self.recovered_sources.discard(source)

        if block_source:
            self.blocked_sources.add(source)
            blocked_scope = "source"
        else:
            self.blocked_requests.add(request_scope)
            blocked_scope = "request"
        detail = {
            "reason": "execution_failed",
            "capability": capability,
            "error_type": error_type,
            "tool": tool_name,
            "source": source,
            "recovery": self._recovery_message(error_type),
        }
        self.failure_records[key] = {
            "capability": capability,
            "error_type": error_type,
            "tool": tool_name,
            "source": source,
            "request_sha256": request_sha256,
            "scope": "unresolved",
            "required": capability in self.required_capabilities,
            "affects_required_evidence": (
                capability in self.required_capabilities
            ),
        }
        self.exhaustion_details.append(detail)
        if not self.termination_detail:
            self.termination_detail = detail
        if self.emit_event is not None:
            self.emit_event(
                "deepagents.capability.exhausted",
                {
                    **detail,
                    "blocked_scope": blocked_scope,
                    "exact_request_failures": exact_failures,
                    "capability_failures": capability_failures,
                    "capability_corrections": capability_corrections,
                    "source_failures": source_failures,
                    "source_corrections": source_corrections,
                },
            )
        return result

    def _deny_blocked_call(self, request):
        tool_call = request.tool_call or {}
        return ToolMessage(
            content=json.dumps(
                {
                    "ok": False,
                    "error": "CAPABILITY_BLOCKED",
                    "message": (
                        "This capability reached its recovery limit. "
                        "Use existing evidence and finish the answer."
                    ),
                }
            ),
            name=self._tool_name(request),
            status="error",
            tool_call_id=tool_call.get("id") or "capability-blocked",
        )

    def _requires_initial_plan(self, tool_name, tool=None):
        if not self.require_initial_plan or self.initial_plan_exists:
            return False
        business_helpers = {
            "analyze_structured_output",
            "inspect_saved_output",
            "run_skill_transform",
        }
        return (
            self._evidence_capability_for_tool(tool, tool_name) is not None
            or tool_name in business_helpers
        )

    def _deny_unplanned_call(self, request):
        tool_name = self._tool_name(request)
        if self.emit_event is not None:
            self.emit_event(
                "deepagents.plan.required",
                {"tool": tool_name, "blocked_scope": "invocation"},
            )
        tool_call = request.tool_call or {}
        return ToolMessage(
            content=json.dumps(
                {
                    "ok": False,
                    "error": "INITIAL_PLAN_REQUIRED",
                    "message": (
                        "Call write_todos with the initial execution plan "
                        "before any business tool."
                    ),
                }
            ),
            name=tool_name,
            status="error",
            tool_call_id=(
                tool_call.get("id") or "initial-plan-required"
            ),
        )

    def _observe_plan_call(self, request, result):
        if self._tool_name(request) != "write_todos":
            return
        if getattr(result, "status", None) == "error":
            return
        arguments = (request.tool_call or {}).get("args") or {}
        if _normalize_plan_steps(arguments.get("todos")):
            initial_plan_existed = self.initial_plan_exists
            self.initial_plan_exists = True
            if not initial_plan_existed and self.emit_event is not None:
                self.emit_event(
                    "deepagents.plan.ready",
                    {
                        "duration_ms": int(
                            (
                                time.monotonic()
                                - self.planning_started_at
                            )
                            * 1000
                        )
                    },
                )

    def _filter_tools(self, tools):
        if self._tool_budget_reached():
            return []
        remaining = []
        for tool in tools:
            tool_name = getattr(tool, "name", None)
            capability = self._capability_for_tool(tool)
            if tool_name in self.blocked_tools:
                continue
            if capability in self.blocked_capabilities:
                continue
            if capability in {"mcp", "plugin"}:
                source = f"{capability}:{tool_name}"
            elif capability == "skill":
                source = None
            else:
                source = f"tool:{tool_name}"
            if source in self.blocked_sources:
                continue
            remaining.append(tool)
        return remaining

    def _tool_budget_reached(self):
        return (
            self.max_tool_calls > 0
            and self.tool_call_count >= self.max_tool_calls
        )

    def _record_tool_call(self):
        self.tool_call_count += 1
        if self._tool_budget_reached() and self.emit_event is not None:
            self.emit_event(
                "deepagents.tool_budget.reached",
                {
                    "max_tool_calls": self.max_tool_calls,
                    "tool_call_count": self.tool_call_count,
                },
            )

    def _is_blocked(self, request):
        tool_name = self._tool_name(request)
        capability = self._evidence_capability_for_tool(
            request.tool,
            tool_name,
        )
        source = self._source_scope(capability, tool_name, request)
        request_scope = (
            tool_name,
            self._normalized_arguments(request),
        )
        return (
            tool_name in self.blocked_tools
            or self._capability_for_tool(request.tool, tool_name)
            in self.blocked_capabilities
            or source in self.blocked_sources
            or request_scope in self.blocked_requests
        )

    def wrap_model_call(self, request, handler):
        """Hide exhausted tools from subsequent model requests."""

        request = self._prepare_model_request(request)
        return handler(request)

    async def awrap_model_call(self, request, handler):
        """Hide exhausted tools from asynchronous model requests."""

        request = self._prepare_model_request(request)
        return await handler(request)

    def _prepare_model_request(self, request):
        """Use bounded reasoning for the initial planning-only turn."""

        overrides = {"tools": self._filter_tools(request.tools)}
        if (
            self.require_initial_plan
            and not self.initial_plan_exists
            and self.planning_reasoning_effort
        ):
            model_settings = dict(request.model_settings or {})
            model_settings["reasoning_effort"] = (
                self.planning_reasoning_effort
            )
            overrides["model_settings"] = model_settings
        return request.override(**overrides)

    def wrap_tool_call(self, request, handler):
        """Classify one synchronous tool result and enforce its budget."""

        tool_name = self._tool_name(request)
        if self._requires_initial_plan(tool_name, request.tool):
            return self._deny_unplanned_call(request)
        if self._is_blocked(request):
            return self._deny_blocked_call(request)
        if self._tool_budget_reached():
            return self._deny_tool_budget_call(request)
        self._record_tool_call()
        self._notify_state_change()
        result = handler(request)
        self._observe_plan_call(request, result)
        result = self._record_result(request, result)
        self._notify_state_change()
        return result

    async def awrap_tool_call(self, request, handler):
        """Classify one asynchronous tool result and enforce its budget."""

        tool_name = self._tool_name(request)
        if self._requires_initial_plan(tool_name, request.tool):
            return self._deny_unplanned_call(request)
        if self._is_blocked(request):
            return self._deny_blocked_call(request)
        if self._tool_budget_reached():
            return self._deny_tool_budget_call(request)
        self._record_tool_call()
        self._notify_state_change()
        result = await handler(request)
        self._observe_plan_call(request, result)
        result = self._record_result(request, result)
        self._notify_state_change()
        return result

    def _deny_tool_budget_call(self, request):
        """Prevent a same-step call from exceeding the Run budget."""

        tool_call = request.tool_call or {}
        return ToolMessage(
            content=json.dumps(
                {
                    "ok": False,
                    "error": "TOOL_BUDGET_EXHAUSTED",
                    "message": (
                        "The Run tool-call budget is exhausted. Use the "
                        "existing evidence and finish the answer."
                    ),
                }
            ),
            name=self._tool_name(request),
            status="error",
            tool_call_id=tool_call.get("id") or "tool-budget-exhausted",
        )
