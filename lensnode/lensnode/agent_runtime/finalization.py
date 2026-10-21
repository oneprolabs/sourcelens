"""Final acceptance of a graph execution's answer, attachments, and outcome."""

import logging
from copy import deepcopy
from dataclasses import dataclass

from ..checkpoint import save_decision_gates
from ..gateway_model import RunCancelledError
from ..plugin_agent import AgentExtensions
from .outcomes import _finalize_runtime_outcome, _unverified_execution_answer
from .prompts import command_answer_language as _command_answer_language
from .prompts import detect_answer_language as _detect_answer_language
from .prompts import pick_text as _pick_text

LOGGER = logging.getLogger("lensnode")


@dataclass(frozen=True)
class FinalizedRun:
    """Snapshot accepted after every terminal check has been evaluated."""

    answer: str
    outcome: str
    termination_detail: dict
    stop_reason: str | None
    deliverables: dict[str, str]


def finalize_run(state, answer, *, truncated, termination_reason):
    """Resolve a graph result once, after refreshing evidence and completion checks."""

    if state.cancel_event is not None and state.cancel_event.is_set():
        raise RunCancelledError("Run was cancelled before final acceptance.")
    manifest = state.command.setdefault("_deliverables", state.runtime_evidence.get("_deliverables", {}))
    state.runtime_evidence["_deliverables"] = manifest
    _update_extension_evidence(state)
    extensions = getattr(state, "agent_extensions", AgentExtensions())
    issues = list(extensions.completion_checks(answer) or [])
    issues.extend(state.runtime_evidence.get("delivery_issues", []))
    if issues:
        state.runtime_evidence["completion_issues"] = issues
        state.command.get("_deliverables", {}).clear()
        answer = _enforce_completion_status(answer, state.command, issues)
    else:
        answer = _enforce_delivery_status(answer, state.command, state.evidence_requirement)
    stop_reason = state.model.stop_reason
    if not truncated and termination_reason is None and stop_reason == "token_budget_wrapup":
        stop_reason = None
    verdicts = _post_run_decision_gates(state, answer)
    if state.cancel_event is not None and state.cancel_event.is_set():
        raise RunCancelledError("Run was cancelled during final acceptance.")
    outcome, detail = _finalize_runtime_outcome(
        capability_middleware=state.capability_middleware,
        evidence_requirement=state.evidence_requirement,
        required_capabilities=state.required_capabilities,
        truncated=truncated or bool(termination_reason),
        stop_reason=termination_reason or stop_reason,
        execution_gate_enabled=state.runtime_mode.execution_gates,
        runtime_evidence=state.runtime_evidence,
        decision_gates=verdicts,
        answer_review_enabled=state.command.get("deep_thinking") is not False,
    )
    if outcome == "blocked":
        answer = _unverified_execution_answer(
            state.question, detail, answer_language=_command_answer_language(state.command)
        )
    return FinalizedRun(answer, outcome, deepcopy(detail), stop_reason,
                        dict(state.command.get("_deliverables") or {}))


def _update_extension_evidence(state):
    """Merge Plugin snapshots without discarding host-observed evidence gaps."""

    extensions = getattr(state, "agent_extensions", AgentExtensions())
    extension_evidence = extensions.evidence()
    if state.runtime_evidence.get("evidence_completeness") == "incomplete":
        extension_evidence.pop("evidence_completeness", None)
    state.runtime_evidence.update(extension_evidence)


def _post_run_decision_gates(state, answer):
    """Review the final answer, reusing matching graph or checkpoint verdicts."""

    if (getattr(state, "command", None) or {}).get("deep_thinking") is False:
        return {}
    resume_state = getattr(state, "resume_state", None)
    if (
        resume_state is not None
        and resume_state.decision_gates
        and not getattr(getattr(state, "agent_extensions", None), "refresh_evidence", False)
    ):
        return dict(resume_state.decision_gates)
    policy = getattr(state, "decision_policy", None)
    if policy is None:
        return {}
    runtime_evidence = getattr(state, "runtime_evidence", None)
    evidence_middleware = getattr(state, "evidence_middleware", None)
    if evidence_middleware is not None:
        verdicts = evidence_middleware.final_verdicts(answer)
    else:
        verdicts = policy.post_run_checks(
            state.question,
            answer,
            runtime_evidence,
        )
    if verdicts and getattr(state, "checkpoint_ready", False):
        try:
            save_decision_gates(
                state.run_uuid,
                state.config.workspace_path,
                verdicts,
            )
        except Exception:
            LOGGER.exception("Failed to persist decision gate verdicts")
    return verdicts


def _enforce_completion_status(answer, command, checks):
    """Withdraw an unsupported final assertion after bounded repair is exhausted."""

    if not checks:
        return answer
    if any(check.get("code") == "artifact_delivery_failed" for check in checks):
        return _pick_text(
            "文件交付未完成，本次附件未发布。请重试生成。",
            "File delivery is incomplete. The attachments were not published. Please retry.",
            _detect_answer_language(answer) if answer.strip() else _command_answer_language(command),
        )
    if any(check.get("code") in {"tool_evidence_unread", "activity_evidence_unread"} for check in checks):
        return _pick_text(
            "结果核验未通过：尚有来源记录未完成核对，本次结果及附件未发布。请补齐证据后重试。",
            "The result could not be verified. Some source records remain unread; the result and attachments "
            "were not published. Complete the evidence review and retry.",
            _detect_answer_language(answer) if answer.strip() else _command_answer_language(command),
        )
    return _pick_text(
        "结果核验未通过，本次结果不能视为已验证。请补齐证据或修正缺乏支持的结论后重试。",
        "The result could not be verified. Complete the evidence review or correct unsupported conclusions and retry.",
        _detect_answer_language(answer) if answer.strip() else _command_answer_language(command),
    )


def _enforce_delivery_status(answer, command, evidence_requirement):
    """Report an unfulfilled artifact request using the actual saved attachment state."""

    if evidence_requirement != "artifact" or command.get("_deliverables"):
        return answer
    return _pick_text(
        "文件交付未完成，本次没有可下载的附件。请重试生成。",
        "File delivery is incomplete. No downloadable attachment was saved for this run. Please retry.",
        _detect_answer_language(answer) if answer.strip() else _command_answer_language(command),
    )
