"""Decision-gated verification of an answer inside the agent loop."""

import json
import logging
import posixpath
import threading

from contextlib import nullcontext

from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command

from ..gateway_model import RunCancelledError, RunTokenBudgetExhaustedError

LOGGER = logging.getLogger("lensnode")

DEFAULT_INTERVAL = 8
DEFAULT_MAX_NUDGES = 1
DEFAULT_MAX_CONVERGENCE_NUDGES = 2
DEFAULT_DELIVERY_TURNS = 8
PUBLICATION_GRACE_TURNS = 2

_RECHECK_GUIDANCE = (
    "Before this answer is accepted, a verification step found it is not "
    "fully supported by the evidence you retrieved. Do not answer from "
    "memory. Reuse retrieved source results and any saved evidence ledger "
    "first; correct unsupported claims using that evidence. Do not repeat "
    "the same retrieval requests or treat truncated results as complete. "
    "If specific missing facts can be retrieved with available tools, batch "
    'alternative keywords with "|" in ONE search_workspace call (for '
    'example "昇腾|Ascend|910B") and read the most relevant matched files '
    "— then give a final answer that every claim traces to retrieved "
    "evidence. Distinguish directly documented facts from derived "
    "conclusions, compatibility or adaptation, examples, plans, and "
    "actual execution. Do not describe adaptation, examples, or plans as "
    "actual operation or validation. If the workspace genuinely lacks the "
    "information or source coverage is limited, state that limitation briefly. Return the answer itself, "
    "not a search log, evidence inventory, list of files to read, or a "
    "promise to investigate later. Keep the existing answer structure and "
    "focus on the user's question."
    " The previous deliverable batch was moved out of /deliverables/. Regenerate every file intended "
    "for the final answer in /deliverables/, including unchanged files. The host stages them automatically."
)

_CONVERGENCE_GUIDANCE = (
    "You have run several search rounds. If the evidence you already have "
    "supports an answer, STOP searching now and write the final answer in "
    "a concise, user-facing structure: lead with the direct conclusion, "
    "then give only the key supporting points and necessary caveats. Use "
    "inline [[source: <path>]] markers. Do not describe your search "
    "process, enumerate retrieved evidence, list documents still to read, "
    "or add a generic follow-up reading plan. Search again only for one "
    "specific missing fact, using batched keywords (\"a|b|c\") and a "
    "narrower glob; do not repeat queries you already ran."
)
_DELIVERY_GUIDANCE = (
    "Stop broad searches and finish the required deliverable from the evidence already collected. "
    "Write the requested final artifact in /deliverables/ before the final answer. The host stages it automatically. "
    "Disclose missing or truncated sources in the artifact. A text answer or a promise to create a file "
    "does not fulfill artifact delivery."
)


class EvidenceGateMiddleware(AgentMiddleware):
    """Verify a proposed answer against the bound Decision gates.

    When the model proposes a final answer (an AI message with no tool
    calls), the bound ``evidence_sufficient`` / ``answer_supported`` gates
    judge whether the conclusion is actually grounded in retrieved
    evidence. An unsupported answer is bounced back, at most
    ``max_nudges`` times, with targeted guidance instead of being
    delivered.

    A separate convergence guard runs *before* the model: every
    ``interval`` model calls it appends a nudge to answer from the evidence
    already gathered, and after ``max_convergence_nudges`` nudges it ends
    the loop, so a runaway search cannot consume the whole recursion
    budget. It is deliberately hooked on ``before_model`` (after any tool
    results), never while tool calls are still pending, so it can never
    leave an AI message with unanswered tool calls.

    The middleware is a no-op unless the run actually binds an
    answer-grounding gate, so unconfigured assistants pay nothing.
    """

    def __init__(
        self,
        policy,
        question,
        *,
        evidence=None,
        interval=DEFAULT_INTERVAL,
        max_nudges=DEFAULT_MAX_NUDGES,
        max_convergence_nudges=DEFAULT_MAX_CONVERGENCE_NUDGES,
        emit_event=None,
        review_scope=None,
        on_recheck=None,
        review_state=None,
        completion_check=None,
        prepare_deliverables=None,
    ):
        self.policy = policy
        self.question = str(question or "")
        self.evidence = evidence
        self.interval = max(int(interval), 1)
        self.max_nudges = max(int(max_nudges), 0)
        self.max_convergence_nudges = max(int(max_convergence_nudges), 0)
        self.emit_event = emit_event
        self.review_scope = review_scope
        self.on_recheck = on_recheck
        self.review_state = review_state if review_state is not None else {}
        self.completion_check = completion_check
        self.prepare_deliverables = prepare_deliverables
        self.model_calls = int(self.review_state.get("model_calls") or 0)
        self.answer_nudges = int(self.review_state.get("answer_nudges") or 0)
        self.convergence_nudges = int(self.review_state.get("convergence_nudges") or 0)
        self.delivery_turns = int(self.review_state.get("delivery_turns") or 0)
        self.correction_calls_remaining = int(self.review_state.get("correction_calls_remaining") or 0)
        self.saw_final_answer = False
        self._last_verification = None
        self._last_tool_evidence = []
        self._tool_results = {}
        self._tool_results_lock = threading.Lock()

    def wrap_tool_call(self, request, handler):
        """Keep bounded source material before outer middleware evicts it to a file."""

        result = handler(request)
        self._remember_tool_result(request, result)
        return result

    async def awrap_tool_call(self, request, handler):
        """Retain the same review material for asynchronous tool execution."""

        result = await handler(request)
        self._remember_tool_result(request, result)
        return result

    def _review_limit(self):
        """Use the configured reviewer capacity for each retained source result."""

        review_limit = getattr(self.policy, "review_evidence_limit", None)
        return review_limit() if callable(review_limit) else 1200

    def _remember_tool_result(self, request, result):
        """Retain at most 32 bounded results without changing graph-visible messages."""

        if not self._active():
            return
        if isinstance(result, Command):
            results = (result.update or {}).get("messages", []) if isinstance(result.update, dict) else []
        else:
            results = [result]
        for message in results:
            call = request.tool_call
            if getattr(message, "tool_call_id", None) != call.get("id"):
                continue
            material = _tool_evidence({"messages": [AIMessage(content="", tool_calls=[call]), message]},
                                      limit=self._review_limit())
            if not material:
                continue
            with self._tool_results_lock:
                self._tool_results[call["id"]] = material[0]
                while len(self._tool_results) > 32:
                    self._tool_results.pop(next(iter(self._tool_results)))

    def _emit(self, event, payload):
        if event == "deepagents.evidence.verified":
            self.review_state.update(answer_nudges=self.answer_nudges, action=payload.get("action"))
        if self.emit_event is not None:
            try:
                self.emit_event(event, payload or {})
            except Exception:
                LOGGER.exception("Failed to emit evidence gate event")

    def _active(self):
        checker = getattr(self.policy, "has_evidence_gates", None)
        return bool(checker is not None and checker())

    def observe_tools(self, state):
        """Retain bounded graph material for a final answer produced outside the graph."""

        with self._tool_results_lock:
            retained = dict(self._tool_results)
        material = _tool_evidence(state, limit=self._review_limit(), retained=retained)
        if material and material != self._last_tool_evidence:
            self._last_tool_evidence = material
            self._last_verification = None

    def _verify(self, answer, state=None):
        evidence = self.evidence
        base_signature = _evidence_signature(evidence)
        if state is not None:
            self.observe_tools(state)
        message_evidence = self._last_tool_evidence
        if message_evidence:
            evidence = {
                **(evidence if isinstance(evidence, dict) else {}),
                "retrieved_evidence": message_evidence,
                "evidence_completeness": (
                    "incomplete"
                    if (isinstance(evidence, dict) and evidence.get("evidence_completeness") == "incomplete")
                    or any(item.get("incomplete") for item in message_evidence)
                    else "complete"
                ),
                "review_completeness": (
                    "incomplete"
                    if (isinstance(evidence, dict) and evidence.get("review_completeness") == "incomplete")
                    or any(item.get("truncated") for item in message_evidence)
                    else "complete"
                ),
            }
        signature = _evidence_signature(evidence)
        if self._last_verification is not None:
            cached_answer, _, cached_signature, cached_verdicts = self._last_verification
            if cached_answer == answer and cached_signature == signature:
                return dict(cached_verdicts)
        try:
            if evidence is None:
                verdicts = self.policy.post_run_checks(
                    self.question,
                    answer,
                )
            else:
                verdicts = self.policy.post_run_checks(
                    self.question,
                    answer,
                    evidence,
                )
        except (RunCancelledError, RunTokenBudgetExhaustedError):
            raise
        except Exception:
            LOGGER.exception("Evidence gate verification failed")
            return None
        if not isinstance(verdicts, dict) or not verdicts:
            return None
        if isinstance(evidence, dict) and evidence.get("evidence_completeness") == "incomplete":
            verdicts = {**verdicts, "evidence_completeness": "incomplete"}
        if isinstance(evidence, dict) and evidence.get("review_completeness") == "incomplete":
            verdicts = {**verdicts, "review_completeness": "incomplete", "answer_supported": "inconclusive"}
        self._last_verification = (answer, base_signature, signature, dict(verdicts))
        return verdicts

    def cached_verdicts(self, answer, base_evidence):
        """Return the last verdict only for an unchanged answer and runtime evidence."""

        if self._last_verification is None:
            return None
        cached_answer, base_signature, _, verdicts = self._last_verification
        if cached_answer != answer or base_signature != _evidence_signature(base_evidence):
            return None
        return dict(verdicts)

    def final_verdicts(self, answer):
        """Review final text with retained graph material, without restarting execution."""

        if not self._active():
            return {}
        cached = self.cached_verdicts(answer, self.evidence)
        if cached is not None:
            return cached
        scope = self.review_scope() if self.review_scope else nullcontext()
        with scope:
            verdicts = self._verify(answer)
        return verdicts or {"review_available": False, "answer_supported": "inconclusive"}

    @hook_config(can_jump_to=["end"])
    def before_model(self, state, runtime):
        return self._before_model(state)

    @hook_config(can_jump_to=["end"])
    async def abefore_model(self, state, runtime):
        return self._before_model(state)

    @hook_config(can_jump_to=["model"])
    def after_model(self, state, runtime):
        return self._after_model(state)

    @hook_config(can_jump_to=["model"])
    async def aafter_model(self, state, runtime):
        return self._after_model(state)

    def _before_model(self, state=None):
        if not self._active() or self.saw_final_answer:
            return None
        if self.max_convergence_nudges <= 0:
            return None
        self.model_calls += 1
        self.review_state["model_calls"] = self.model_calls
        if self.correction_calls_remaining > 0:
            self.correction_calls_remaining -= 1
            self.review_state["correction_calls_remaining"] = self.correction_calls_remaining
            return None
        if self.convergence_nudges >= self.max_convergence_nudges:
            artifact_required = (
                isinstance(self.evidence, dict)
                and self.evidence.get("artifact_required")
            )
            publication_grace = False
            if artifact_required and self.delivery_turns >= DEFAULT_DELIVERY_TURNS:
                grace_used = int(self.review_state.get("publication_grace_used") or 0)
                messages = (state or {}).get("messages") or []
                recent_write = False
                for message in reversed(messages):
                    if getattr(message, "type", "") != "tool":
                        break
                    if _successful_artifact_write(message):
                        recent_write = True
                        break
                if grace_used < PUBLICATION_GRACE_TURNS and (recent_write or grace_used):
                    self.review_state["publication_grace_used"] = grace_used + 1
                    publication_grace = True
            if artifact_required and (
                self.delivery_turns < DEFAULT_DELIVERY_TURNS or publication_grace
            ):
                self.delivery_turns += 1
                self.review_state["delivery_turns"] = self.delivery_turns
                self._emit(
                    "deepagents.evidence.convergence",
                    {
                        "action": "deliver",
                        "turn": self.model_calls,
                        "delivery_turn": self.delivery_turns,
                    },
                )
                if self.delivery_turns == 1:
                    return {"messages": [HumanMessage(content=_DELIVERY_GUIDANCE)]}
                return None
            if isinstance(self.evidence, dict):
                self.evidence["convergence_exhausted"] = True
            self._emit(
                "deepagents.evidence.convergence",
                {"action": "end", "turn": self.model_calls},
            )
            return {"jump_to": "end"}
        if self.model_calls % self.interval != 0:
            return None
        self.convergence_nudges += 1
        self.review_state["convergence_nudges"] = self.convergence_nudges
        self._emit(
            "deepagents.evidence.convergence",
            {"action": "nudge", "turn": self.model_calls},
        )
        guidance = _CONVERGENCE_GUIDANCE
        if isinstance(self.evidence, dict) and self.evidence.get("artifact_required"):
            guidance = _DELIVERY_GUIDANCE
        return {"messages": [HumanMessage(content=guidance)]}

    def _after_model(self, state):
        messages = state.get("messages") or []
        if not messages:
            return None
        last = messages[-1]
        if not isinstance(last, AIMessage):
            return None
        if getattr(last, "tool_calls", None):
            # Tool calls are still pending; the tools node answers them
            # before the next model call, so never jump to the model here.
            return None
        answer = _message_text(last)
        if not answer.strip():
            return None
        if self.prepare_deliverables is not None:
            issues = self.prepare_deliverables() or []
            if issues:
                attempts = int(self.review_state.get("automatic_delivery_corrections") or 0)
                if attempts >= 2:
                    return None
                self.review_state["automatic_delivery_corrections"] = attempts + 1
                self.saw_final_answer = False
                self.correction_calls_remaining = self.interval
                self.review_state["correction_calls_remaining"] = self.correction_calls_remaining
                recovery = "\n".join(str(issue.get("recovery") or issue.get("message") or issue.get("code"))
                                     for issue in issues[:3])
                return {"jump_to": "model", "messages": [HumanMessage(content=recovery)]}
        if self.completion_check is not None:
            try:
                checks = self.completion_check(answer) or []
            except Exception:
                LOGGER.exception("Completion check failed")
                checks = [{"code": "completion_check_failed", "recovery": "Completion verification failed. Retry once."}]
            seen = set(self.review_state.get("completion_check_codes") or [])
            checks = [check for check in checks if check.get("code") not in seen]
            if checks:
                seen.add(checks[0].get("code", "completion_check"))
                self.review_state["completion_check_codes"] = sorted(seen)
                self.saw_final_answer = False
                self.correction_calls_remaining = self.interval
                self.review_state["correction_calls_remaining"] = self.correction_calls_remaining
                if self.on_recheck is not None:
                    self.on_recheck()
                self._emit(
                    "deepagents.evidence.verified",
                    {"action": "recheck", "fallback_reason": checks[0].get("code", "completion_check")},
                )
                recovery = checks[0].get("recovery") or checks[0].get("message") or "Correct the answer from the evidence."
                return {"jump_to": "model", "messages": [HumanMessage(content=recovery)]}
        return self._review_answer(answer, state)

    def _review_answer(self, answer, state):
        """Review the selected text with its original graph evidence and coverage."""

        if not self._active():
            return None
        if (
            isinstance(self.evidence, dict)
            and self.evidence.get("artifact_required")
            and not self.evidence.get("_deliverables")
            and not self.review_state.get("delivery_nudged")
        ):
            self.review_state["delivery_nudged"] = True
            return {"jump_to": "model", "messages": [HumanMessage(content=_DELIVERY_GUIDANCE)]}
        self.saw_final_answer = True
        scope = self.review_scope() if self.review_scope else nullcontext()
        with scope:
            verdicts = self._verify(answer, state)
            if verdicts is None:
                return None
            contradicted = (
                verdicts.get("evidence_sufficient") is False
                or verdicts.get("answer_supported") == "unsupported"
                or verdicts.get("evidence_strength") in {"unsupported", "contradicted"}
            )
            if verdicts.get("review_available") is False and not contradicted:
                self._emit(
                    "deepagents.evidence.verified",
                    {"verdicts": verdicts, "action": "inconclusive", "fallback_reason": "review_unavailable"},
                )
                return None
            if verdicts.get("evidence_completeness") == "incomplete" and not contradicted:
                self._emit(
                    "deepagents.evidence.verified",
                    {"verdicts": verdicts, "action": "inconclusive", "fallback_reason": "review_material_incomplete"},
                )
                return None
            if verdicts.get("review_completeness") == "incomplete":
                self._emit(
                    "deepagents.evidence.verified",
                    {"verdicts": verdicts, "action": "inconclusive", "fallback_reason": "review_material_truncated"},
                )
                return None
            if _verdicts_supported(verdicts):
                self._emit(
                    "deepagents.evidence.verified",
                    {"verdicts": verdicts, "action": "accept"},
                )
                return None
            if self.answer_nudges >= self.max_nudges:
                self._emit(
                    "deepagents.evidence.verified",
                    {"verdicts": verdicts, "action": "accept_best_effort", "fallback_reason": "review_not_passed"},
                )
                return None
            self.answer_nudges += 1
            self.saw_final_answer = False
            self.correction_calls_remaining = self.interval
            self.review_state["correction_calls_remaining"] = self.correction_calls_remaining
            self.review_state["answer_nudges"] = self.answer_nudges
            if self.on_recheck is not None:
                self.on_recheck()
            self._emit(
                "deepagents.evidence.verified",
                {"verdicts": verdicts, "action": "recheck"},
            )
            return {
                "messages": [HumanMessage(content=_RECHECK_GUIDANCE)],
                "jump_to": "model",
            }


def _successful_artifact_write(message):
    """Recognize successful writes among the latest batch of tool results."""

    if getattr(message, "name", "") not in {"write_file", "edit_file", "append_file"}:
        return False
    content = _message_text(message).strip()
    if getattr(message, "status", "success") == "error" or content.lower().startswith("error"):
        return False
    try:
        payload = json.loads(content)
    except (TypeError, ValueError):
        return True
    return not isinstance(payload, dict) or (payload.get("ok") is not False and not payload.get("error"))


def _message_text(message):
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            str(item.get("text") or "")
            for item in content
            if isinstance(item, dict)
        )
    return str(content or "")


def _source_coverage_incomplete(content):
    """Read only the generic Plugin evidence envelope."""

    try:
        payload = json.loads(content)
    except (TypeError, ValueError):
        return False
    if not isinstance(payload, dict):
        return False
    metadata = payload.get("_evidence")
    return payload.get("ok") is False or (isinstance(metadata, dict) and metadata.get("complete") is False)


def _tool_evidence(state, limit=1200, retained=None):
    """Return a bounded view of retrieved tool output for post-run review."""

    if not isinstance(state, dict):
        return []
    evidence = []
    operations = {}
    failures = {}
    excluded = {
        "write_todos",
        "write_file",
        "edit_file",
        "append_file",
        "save_deliverable",
        "publish_artifact",
        "ls",
        "glob",
        "delete_file",
        "mkdir",
        "decision_score",
        "decision_rank",
    }
    for message in state.get("messages") or []:
        if isinstance(message, AIMessage):
            for call in message.tool_calls or []:
                arguments = call.get("args")
                if isinstance(arguments, dict):
                    operations[call.get("id")] = (
                        call.get("name"),
                        json.dumps(arguments, sort_keys=True, separators=(",", ":")),
                    )
        if getattr(message, "type", "") != "tool":
            continue
        content = _message_text(message).strip()
        if not content:
            continue
        name = str(getattr(message, "name", "") or "tool")[:80]
        if name in excluded:
            continue
        operation = operations.get(getattr(message, "tool_call_id", None))
        if name == "read_file" and operation is not None and operation[0] == name:
            arguments = json.loads(operation[1])
            path = posixpath.normpath("/" + str(arguments.get("file_path") or "").lstrip("/"))
            if path.startswith(("/skills/", "/mcp/")):
                continue
        if name == "execute" and ("drwx" in content or content.startswith("total ")):
            continue
        item = {
            "tool": name,
            "content": content[:limit],
            "incomplete": getattr(message, "status", None) == "error"
            or (name not in {"read_file", "grep", "execute"} and _source_coverage_incomplete(content)),
            "truncated": (
                len(content) > limit
                or content.startswith("Tool result too large,")
                or "[Output was truncated due to size limits." in content
                or "[Output exceeded the capture size limit" in content
            ),
        }
        original = (retained or {}).get(getattr(message, "tool_call_id", None))
        if original is not None and original["tool"] == name:
            item = dict(original)
        if operation is not None and operation[0] == name:
            if item["incomplete"]:
                failures.setdefault(operation, []).append(item)
            else:
                for failure in failures.pop(operation, []):
                    failure.update(incomplete=False, truncated=False, recovered=True)
        evidence.append(item)
    if len(evidence) > 32:
        # Keep each tool/source represented before filling the remaining budget.
        representatives = {}
        for item in evidence:
            representatives[item["tool"]] = item
        selected = list(representatives.values())[:32]
        selected.extend(item for item in evidence if all(item is not chosen for chosen in selected))
        evidence = selected[:32]
        evidence[0] = {**evidence[0], "truncated": True}
    return evidence


def _evidence_signature(evidence):
    """Serialize evidence so in-place changes invalidate verification reuse."""

    if isinstance(evidence, dict):
        evidence = {key: value for key, value in evidence.items() if not key.startswith("_")}
    return json.dumps(evidence, ensure_ascii=False, sort_keys=True, default=str)


def _verdicts_supported(verdicts):
    """Return whether every reported verdict passed."""

    if verdicts.get("review_available") is False:
        return False
    if verdicts.get("evidence_sufficient") is False:
        return False
    option = verdicts.get("answer_supported")
    if isinstance(option, str) and option and option != "supported":
        return False
    strength = verdicts.get("evidence_strength")
    if strength == "qualified_weak":
        return True
    if strength in {
        "adapted_only",
        "example_only",
        "planned",
        "unsupported",
        "contradicted",
        "unknown",
    }:
        return False
    return True
