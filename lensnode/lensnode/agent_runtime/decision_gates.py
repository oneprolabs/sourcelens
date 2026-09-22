"""Host-owned semantics and execution for Decision gates and analyses."""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from types import SimpleNamespace

from ..decision_contract import validate_decision_result
from ..plugin_package_loader import (
    PluginPackageLoadError,
    load_runtime_contract,
)
from ..plugin_tools import _execute_plugin_tool

GATE_SOURCE = "decision_gate"
GATE_PHASE = "P3"
PHASE_ORDER = {"P1": 1, "P3": 3}
GATE_TIMEOUT_S = 3.0
GATE_RUN_BUDGET = 8
DEFAULT_HISTORY_TURNS = 4
DEFAULT_MAX_STATE_CHARS = 4000

GATE_REGISTRY = {
    "search_needed": {
        "kind": "noul",
        "fallback": "model",
        "phase": "P1",
        "instructions": (
            "Does answering the user's latest message require searching the "
            "workspace or the provided documents?"
        ),
        "criteria_true": (
            "The message names or asks about a topic, entity, document, file, "
            "or fact, or asks to explain, summarize, compare, find, or act on "
            "something."
        ),
        "criteria_false": (
            "The message is a greeting, thanks, farewell, self-introduction, "
            "or other social pleasantry that needs no retrieval."
        ),
    },
    "evidence_requirement": {
        "kind": "noul",
        "fallback": "model",
        "phase": "P3",
        "instructions": (
            "Does answering the user's latest message require tool, document, "
            "or user-provided evidence rather than a direct answer?"
        ),
    },
    "evidence_sufficient": {
        "kind": "noul",
        "fallback": "fixed",
        "phase": "P3",
        "instructions": (
            "Does the answer rely only on evidence that was actually "
            "retrieved or provided?"
        ),
        "criteria_true": (
            "Every factual claim in the answer is backed by retrieved tool "
            "output, a provided document, or the user's own input."
        ),
        "criteria_false": (
            "The answer asserts facts that were not retrieved and are not "
            "present in the provided material."
        ),
    },
    "answer_supported": {
        "kind": "choice",
        "fallback": "fixed",
        "phase": "P3",
        "pass_option": "supported",
        "instructions": (
            "Is the answer supported by the evidence that was retrieved?"
        ),
        "criteria": {
            "supported": (
                "The answer follows from retrieved or provided evidence."
            ),
            "unsupported": (
                "The answer goes beyond or contradicts the evidence."
            ),
        },
    },
}


def gate_bindings(command):
    """Return bound gate config keyed by gate name.

    Each entry carries the frozen Tool identity resolved by the backend at
    dispatch time, so the runner never reads a Plugin manifest itself.
    """

    index = {}
    for entry in command.get("decision_gates") or []:
        if not isinstance(entry, dict):
            continue
        gates = entry.get("gates")
        if not isinstance(gates, dict):
            continue
        for key, config in gates.items():
            if key in index:
                continue
            index[key] = {
                **(config if isinstance(config, dict) else {}),
                "plugin_key": str(entry.get("plugin_key") or ""),
                "plugin_version": str(entry.get("plugin_version") or ""),
                "connection_uuid": str(entry.get("connection_uuid") or ""),
            }
    return index


def run_decision_tool(
    command,
    config,
    http_client,
    plugin_http_pool,
    contract,
    connection_uuid,
    plugin_key,
    plugin_version,
    tool_key,
    arguments,
    call_id,
    source,
    emit,
    timeout,
):
    """Run one Decision Tool call through the frozen Plugin pipeline.

    Shared by gates and analyses: same snapshot/lease/material flow, same
    host HTTP policy, same bounded timeout.
    """

    executor = ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(
            _execute_plugin_tool,
            command,
            config,
            http_client,
            connection_uuid,
            plugin_key,
            plugin_version,
            tool_key,
            SimpleNamespace(tool_call_id=call_id),
            arguments,
            contract.execute_tool,
            emit,
            plugin_http_pool=plugin_http_pool,
            http_origins=contract.http_origins,
            http_post_paths=contract.http_post_paths,
            source=source,
        )
        return future.result(timeout=timeout), ""
    except FuturesTimeoutError:
        return None, "timeout"
    except Exception:
        return None, "error"
    finally:
        executor.shutdown(wait=False)


class DecisionRunner:
    """Evaluate bound Decision gates over the frozen Plugin Tool pipeline."""

    def __init__(
        self,
        command,
        config,
        http_client,
        plugin_http_pool=None,
        emit_event=None,
        run_uuid="",
    ):
        self._command = command or {}
        self._config = config
        self._http_client = http_client
        self._plugin_http_pool = plugin_http_pool
        self._emit = emit_event
        self._run_uuid = str(run_uuid or "")
        self._bindings = gate_bindings(self._command)
        self._used = 0
        self._sequence = {}

    def evaluate(self, gate, *, question="", history=None, tools=None):
        """Return the gate value, or None to fall back to the inner policy."""

        return self._evaluate(
            gate,
            question=question,
            history=history,
            tools=tools,
            expect_choice=False,
        )

    def evaluate_choice(self, gate, *, question="", history=None, tools=None):
        """Return the chosen option of a choice gate, or None to fall back."""

        return self._evaluate(
            gate,
            question=question,
            history=history,
            tools=tools,
            expect_choice=True,
        )

    def default_verdict(self, gate):
        """Return the fixed fail-safe verdict declared for one gate."""

        spec = GATE_REGISTRY.get(gate) or {}
        if spec.get("kind") == "choice":
            return str(spec.get("pass_option") or "")
        return True

    def _evaluate(self, gate, *, question, history, tools, expect_choice):
        started = time.monotonic()
        call_id = self._next_call_id(gate)
        binding = self._bindings.get(gate) or {}
        self._emit_event(
            "deepagents.decision.gate.start",
            {
                "gate": gate,
                "source": GATE_SOURCE,
                "call_id": call_id,
                "threshold": binding.get("threshold"),
                "margin": binding.get("margin"),
            },
        )
        verdict = "fallback"
        value = None
        reason = ""
        try:
            result, reason = self._gate_result(
                gate,
                question,
                history,
                tools,
                call_id,
            )
            if result is None:
                return None
            threshold = binding.get("threshold")
            margin = binding.get("margin")
            if expect_choice:
                if not isinstance(result.value, dict) or not result.value:
                    reason = "invalid_response"
                    return None
                option = max(
                    result.value,
                    key=lambda key: result.value[key],
                )
                if (
                    isinstance(threshold, (int, float))
                    and isinstance(margin, (int, float))
                    and result.value[option] < threshold + margin
                ):
                    reason = "low_confidence"
                    return None
                verdict = "accept"
                value = option
                return option
            if not isinstance(result.value, float):
                reason = "invalid_response"
                return None
            value = result.value
            if (
                isinstance(threshold, (int, float))
                and isinstance(margin, (int, float))
                and abs(value - threshold) < margin
            ):
                reason = "low_confidence"
                return None
            verdict = "accept"
            return value
        finally:
            self._emit_event(
                "deepagents.decision.gate.done",
                {
                    "gate": gate,
                    "source": GATE_SOURCE,
                    "verdict": verdict,
                    "value": value,
                    "threshold": binding.get("threshold"),
                    "margin": binding.get("margin"),
                    "fallback_reason": reason,
                    "duration_ms": int(
                        (time.monotonic() - started) * 1000
                    ),
                    "call_id": call_id,
                },
            )

    def _gate_result(self, gate, question, history, tools, call_id):
        spec = GATE_REGISTRY.get(gate)
        if spec is None:
            return None, "unknown_gate"
        if PHASE_ORDER.get(spec.get("phase"), 99) > PHASE_ORDER.get(
            GATE_PHASE, 0
        ):
            return None, "not_bound"
        binding = self._bindings.get(gate) or {}
        if not binding or not binding.get("tool_key"):
            return None, "unknown_gate"
        if self._used >= GATE_RUN_BUDGET:
            return None, "budget_exceeded"
        self._used += 1
        return self._execute(gate, binding, question, history, tools, call_id)

    def _execute(self, gate, binding, question, history, tools, call_id):
        plugin_key = str(binding.get("plugin_key") or "")
        plugin_version = str(binding.get("plugin_version") or "")
        connection_uuid = str(binding.get("connection_uuid") or "")
        tool_key = str(binding.get("tool_key") or "")
        if not plugin_key or not plugin_version or not connection_uuid:
            return None, "unknown_gate"
        try:
            contract = load_runtime_contract(plugin_key, plugin_version)
        except PluginPackageLoadError:
            return None, "error"
        project = getattr(contract, "project_decision", None)
        if not callable(project):
            return None, "error"
        arguments = _gate_arguments(
            gate,
            question,
            history,
            tools,
            binding.get("max_state_chars"),
        )
        if arguments is None:
            return None, "error"
        encoded, reason = run_decision_tool(
            self._command,
            self._config,
            self._http_client,
            self._plugin_http_pool,
            contract,
            connection_uuid,
            plugin_key,
            plugin_version,
            tool_key,
            arguments,
            call_id,
            GATE_SOURCE,
            self._tagged_emit,
            GATE_TIMEOUT_S,
        )
        if encoded is None:
            return None, reason
        try:
            payload = json.loads(encoded)
        except (TypeError, ValueError):
            return None, "invalid_response"
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            return None, "error"
        expected_kind = (
            binding.get("kind")
            or (GATE_REGISTRY.get(gate) or {}).get("kind")
            or ""
        )
        result = validate_decision_result(
            project(tool_key, payload),
            expected_kind,
        )
        if result is None:
            return None, "invalid_response"
        return result, ""

    def _tagged_emit(self, event, payload):
        if isinstance(payload, dict):
            payload = {**payload, "source": GATE_SOURCE}
        self._emit_event(event, payload)

    def _emit_event(self, event, payload):
        if self._emit is not None:
            self._emit(event, payload)

    def _next_call_id(self, gate):
        sequence = self._sequence.get(gate, 0) + 1
        self._sequence[gate] = sequence
        return f"gate:{gate}:{sequence}"


def _gate_arguments(gate, question, history, tools, max_state_chars):
    """Build bounded gate inputs without leaking documents or skills."""

    spec = GATE_REGISTRY.get(gate) or {}
    instructions = spec.get("instructions")
    if not instructions:
        return None
    state = _state_text(question, history, max_state_chars)
    if not state:
        return None
    if gate == "evidence_requirement":
        names = _tool_names(tools)
        if names:
            state = f"{state}\n\nAvailable tools: {names}"
    if spec.get("kind") == "choice":
        criteria = spec.get("criteria")
        if not isinstance(criteria, dict) or not criteria:
            return None
        return {
            "state": state,
            "instructions": instructions,
            "criteria": json.dumps(criteria, ensure_ascii=False),
        }
    arguments = {"state": state, "instructions": instructions}
    if spec.get("criteria_true"):
        arguments["criteria_true"] = spec["criteria_true"]
    if spec.get("criteria_false"):
        arguments["criteria_false"] = spec["criteria_false"]
    return arguments


def _state_text(question, history, max_state_chars):
    limit = (
        max_state_chars
        if type(max_state_chars) is int and max_state_chars > 0
        else DEFAULT_MAX_STATE_CHARS
    )
    lines = [str(question or "").strip()]
    turns = []
    for item in history or []:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip()
        content = str(item.get("content") or "").strip()
        if not role or not content:
            continue
        turns.append(f"{role}: {content}")
    if turns:
        lines.append("")
        lines.append("Recent conversation:")
        lines.extend(turns[-DEFAULT_HISTORY_TURNS:])
    return "\n".join(lines).strip()[:limit]


def _tool_names(tools):
    names = []
    for tool in tools or []:
        name = str(getattr(tool, "name", "") or "").strip()
        if name and name not in names:
            names.append(name)
        if len(names) >= 40:
            break
    return ", ".join(names)
