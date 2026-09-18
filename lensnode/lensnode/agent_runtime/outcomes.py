"""Terminal outcome policy for LensNode agent runs."""

from .capability_protocol import CAPABILITY_FAMILIES
from .prompts import (
    detect_answer_language as _detect_answer_language,
    pick_text as _pick_text,
)
def _capability_termination_detail(capability, tool=""):
    """Return a secret-safe capability termination contract."""

    return {
        "reason": "capability_unavailable",
        "capability": capability,
        "error_type": "capability",
        "tool": tool,
        "recovery": (
            "Use an assistant with the required operation or ask an "
            "administrator to bind that capability."
        ),
    }


def _evidence_termination_detail(
    evidence_requirement,
    capability="",
):
    """Return a secret-safe contract for missing execution evidence."""

    if not capability:
        capability = (
            "artifact_delivery"
            if evidence_requirement == "artifact"
            else "tool"
        )
    return {
        "reason": "evidence_unavailable",
        "capability": capability,
        "error_type": "verification",
        "tool": "",
        "recovery": (
            "Review the execution details and retry; no verified result "
            "was returned."
        ),
    }


def _finalize_runtime_outcome(
    *,
    capability_middleware,
    evidence_requirement,
    required_capabilities=None,
    truncated,
    stop_reason,
    execution_gate_enabled=True,
    runtime_evidence=None,
):
    """Resolve a run outcome after observing actual tool execution."""

    if not execution_gate_enabled:
        if truncated:
            return "partial", {
                "reason": stop_reason or "execution_limit",
            }
        return "completed", {}
    if capability_middleware is None:
        if truncated:
            return "partial", {
                "reason": stop_reason or "execution_limit",
            }
        return "completed", {}

    successful_evidence = getattr(
        capability_middleware,
        "successful_evidence",
        None,
    )
    if successful_evidence is None:
        successful = set(
            getattr(
                capability_middleware,
                "successful_capabilities",
                set(),
            )
        )
    else:
        successful = {
            str(evidence.get("capability") or "")
            for evidence in successful_evidence
            if isinstance(evidence, dict)
            and evidence.get("tool")
            and evidence.get("request_sha256")
        }
    required = {
        capability
        for capability in required_capabilities or []
        if capability in CAPABILITY_FAMILIES or capability == "tool"
    }
    relevant_successes = successful & required
    failed = set(
        getattr(capability_middleware, "failed_capabilities", set())
    )
    recovered = set(
        getattr(capability_middleware, "recovered_capabilities", set())
    )
    failure_records = getattr(
        capability_middleware,
        "failure_records",
        None,
    )
    if isinstance(failure_records, dict):
        unresolved_records = [
            detail
            for detail in failure_records.values()
            if isinstance(detail, dict)
            and detail.get("scope") == "unresolved"
            and detail.get("capability") in required
        ]
        unrecovered_failures = {
            detail.get("capability") for detail in unresolved_records
        }
        unresolved_sources = {
            detail.get("source")
            for detail in unresolved_records
            if detail.get("source")
        }
    else:
        unresolved_records = []
        unrecovered_failures = (failed - recovered) & required
        unresolved_sources = set()
    exhaustion_details = getattr(
        capability_middleware,
        "exhaustion_details",
        [],
    )
    termination_detail = next(
        (
            dict(detail)
            for detail in exhaustion_details
            if detail.get("capability") in required
            and (
                not unresolved_sources
                or detail.get("source") in unresolved_sources
            )
        ),
        {},
    )
    if not termination_detail:
        termination_detail = dict(
            getattr(capability_middleware, "termination_detail", {})
        )
        if termination_detail.get("capability") not in required:
            termination_detail = {}

    if evidence_requirement == "adaptive":
        if isinstance(failure_records, dict):
            unresolved_records = [
                detail
                for detail in failure_records.values()
                if isinstance(detail, dict)
                and detail.get("scope") == "unresolved"
            ]
        else:
            unresolved_records = []
        if unresolved_records:
            if not termination_detail:
                failure = unresolved_records[0]
                termination_detail = {
                    "reason": "execution_failed",
                    "capability": failure.get("capability") or "tool",
                    "error_type": failure.get("error_type") or "tool",
                    "tool": failure.get("tool") or "",
                    "source": failure.get("source") or "",
                }
            if truncated and stop_reason:
                termination_detail["trigger"] = stop_reason
            return "partial", termination_detail
        if truncated:
            return "partial", {
                "reason": stop_reason or "execution_limit",
            }
        return "completed", {}

    if evidence_requirement == "tool_result" and unrecovered_failures:
        if not termination_detail:
            termination_detail = {
                "reason": "execution_failed",
                "capability": next(iter(sorted(unrecovered_failures))),
            }
        if truncated and stop_reason:
            termination_detail["trigger"] = stop_reason
        return "partial", termination_detail

    if evidence_requirement == "tool_result" and not relevant_successes:
        if not termination_detail:
            capability = next(iter(sorted(required)), "tool")
            termination_detail = _evidence_termination_detail(
                evidence_requirement,
                capability,
            )
        if truncated and stop_reason:
            termination_detail["trigger"] = stop_reason
        return "partial", termination_detail

    if (
        evidence_requirement == "artifact"
        and "artifact_delivery" not in successful
    ):
        useful_capabilities = required - {"artifact_delivery"}
        useful_evidence = bool(successful & useful_capabilities)
        if not termination_detail:
            termination_detail = _evidence_termination_detail(
                evidence_requirement
            )
        if truncated and stop_reason:
            termination_detail["trigger"] = stop_reason
        return "partial", termination_detail

    delivered_artifact_wrapup = (
        truncated
        and stop_reason in {"token_budget_wrapup", "token_capped"}
        and "artifact_delivery" in successful
        and (
            evidence_requirement == "artifact"
            or bool(relevant_successes)
        )
    )
    if delivered_artifact_wrapup:
        return "completed", {}

    record_validation = (runtime_evidence or {}).get(
        "record_validation"
    ) or {}
    validated_token_wrapup = (
        truncated
        and stop_reason in {"token_budget_wrapup", "token_capped"}
        and record_validation.get("valid") is True
        and record_validation.get("count_matches") is True
        and bool(record_validation.get("unique_by"))
    )
    if validated_token_wrapup:
        return "completed", {}
    if truncated:
        return "partial", {
            "reason": stop_reason or "execution_limit",
        }
    return "completed", {}


def _unverified_execution_answer(
    question,
    termination_detail,
    answer_language=None,
):
    """Return a deterministic answer when no business evidence was obtained."""

    language = answer_language or _detect_answer_language(question)
    capability = termination_detail.get("capability") or "tool"
    reason = termination_detail.get("reason")
    error_type = termination_detail.get("error_type")
    if reason == "capability_unavailable":
        return _pick_text(
            "当前助手已绑定的 Skills、工具和纯模型能力都无法完成该请求，"
            "因此未调用任何业务工具。请改用支持该操作的助手，或联系管理员"
            "绑定所需能力。",
            "The bound Skills, tools, and model-only capabilities cannot "
            "complete this request, so no business tool was called. Use an "
            "assistant that supports this operation or ask an administrator "
            "to bind the required capability.",
            language,
        )
    if reason == "execution_failed":
        if error_type == "transient":
            return _pick_text(
                "已匹配到所需能力并调用了工具，但上游服务暂时异常，未能取得"
                "可验证的业务结果。请稍后重试。",
                "A matching capability was found and called, but the "
                "upstream service failed temporarily and returned no "
                "verified business result. Please retry later.",
                language,
            )
        if error_type == "configuration":
            return _pick_text(
                "已匹配到所需能力，但工具执行时遇到配置或授权错误，未能取得"
                "可验证的业务结果。请联系管理员处理后重试。",
                "A matching capability was found, but its execution failed "
                "because of configuration or authorization. Ask an "
                "administrator to resolve it, then retry.",
                language,
            )
        if error_type == "request":
            return _pick_text(
                "已匹配到所需能力，但工具请求未被接受，未能取得可验证的业务"
                "结果。请修正或补充请求参数后重试。",
                "A matching capability was found, but the tool request was "
                "not accepted. Correct or provide the required input, then "
                "retry.",
                language,
            )
        return _pick_text(
            "已匹配到所需能力，但工具执行失败，未能取得可验证的业务结果。"
            "请按页面提示处理后重试。",
            "A matching capability was found, but tool execution failed and "
            "returned no verified business result. Follow the recovery "
            "guidance and retry.",
            language,
        )
    return _pick_text(
        f"本次未能通过已配置的 {capability} 能力取得任何已验证的业务数据，"
        "因此无法可靠回答该请求，也不会展示未经工具结果证实的订单或客户"
        "信息。请检查请求和运行记录后重试。",
        f"No verified business data was obtained from the configured "
        f"{capability} capability, so the request cannot be answered "
        "reliably. Unverified order or customer information will not be "
        "shown. Review the request and runtime details, then retry.",
        language,
    )


def _route_guidance(route):
    """Return concise execution guidance for the selected runtime route."""

    if route in {"agent_execute", "adaptive"}:
        return (
            "\n\nExecution guidance: Decide from the user's request and the "
            "available tools whether to answer directly, make an obvious "
            "single tool call, or run a multi-step investigation. Use "
            "write_todos only for genuinely multi-step work (for example, "
            "three or more distinct steps, multiple independent operations, "
            "or work whose next steps depend on intermediate results). Skip "
            "it for simple answers and focused single-tool actions. Once a "
            "todo list exists, update its statuses as work progresses and "
            "finish only after the requested result is verified."
        )
    if route == "plan_execute":
        return (
            "\n\nRuntime route: plan_execute. Before any business tool, call "
            "write_todos with the complete concise high-level plan. Once "
            "execution starts, keep the task count, order, and wording fixed; "
            "later write_todos calls may only update statuses. Put newly "
            "discovered execution details under the closest existing task. "
            "Stop when the requested outcome is verified.\n\n"
            "The task subagent is available. When the plan splits into "
            "genuinely independent, heavy subtasks, delegate each one to a "
            "task subagent (issue multiple task calls in one message to run "
            "them in parallel), then synthesize their results. Do NOT "
            "delegate light work — handle it directly with batched tool "
            "calls, which is faster."
        )
    if route == "direct_execute":
        return (
            "\n\nRuntime route: direct_execute. Perform the focused action "
            "directly. A multi-step plan is not required."
        )
    return ""
