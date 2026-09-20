"""Sanitize LensNode runtime events before exposing them to end users."""

from datetime import datetime

PUBLIC_EVENT_TYPES = {
    "artifact.created",
    "capability.blocked",
    "document.progress",
    "execution.failed",
    "verification.failed",
    "phase.changed",
    "plan.updated",
    "route.selected",
    "stage.updated",
}

ROUTE_VALUES = {
    "intent": {"informational", "action", "clarification"},
    "complexity": {"simple", "complex"},
    "route": {
        "capability_unavailable",
        "direct_answer",
        "direct_execute",
        "plan_execute",
    },
    "evidence_requirement": {
        "none",
        "tool_result",
        "artifact",
        "user_input",
    },
}

PUBLIC_CAPABILITIES = {
    "skill",
    "mcp",
    "workspace",
    "artifact_delivery",
    "tool",
}

PUBLIC_PHASES = {
    "analyzing",
    "planning",
    "executing",
    "answering",
    "completed",
}

PUBLIC_PLAN_STATUSES = {"pending", "in_progress", "completed"}

PUBLIC_STAGE_STATUSES = {
    "pending",
    "in_progress",
    "completed",
    "failed",
    "skipped",
}

PUBLIC_DOCUMENT_PROGRESS_STAGES = {
    "downloading",
    "extracting_text",
    "recognizing_images",
    "ready",
}

TOOL_ACTIVITY_STATUSES = {
    "done": "completed",
    "failed": "failed",
    "invoke": "in_progress",
    "start": "in_progress",
    "timeout": "failed",
}

BUSINESS_ACTIVITY_KINDS = {
    "analyzing_results",
    "count_results",
    "get_order_detail",
    "group_results",
    "query_orders",
    "querying_data",
}

PUBLIC_TERMINATION_REASONS = {
    "capability_unavailable",
    "execution_failed",
    "evidence_unavailable",
    "evidence_insufficient",
    "execution_limit",
    "turn_limit",
    "soft_deadline",
    "token_budget",
    "safety_terminated",
    "model_length_capped",
    "token_capped",
    "token_budget_wrapup",
    "loop_capped",
    "runtime_failure",
    "needs_user_input",
}

PUBLIC_ERROR_TYPES = {
    "capability",
    "configuration",
    "policy",
    "transient",
    "request",
    "tool",
    "verification",
}

PUBLIC_RUNTIME_CODES = {
    "EMPTY_AGENT_RESPONSE",
    "MODEL_STREAM_ERROR",
    "MODEL_UNAVAILABLE",
    "MODEL_TIMEOUT",
    "NO_ACTIVITY_TIMEOUT",
    "RUN_CANCELLED",
    "RUN_TIMEOUT",
}

RECOVERY_MESSAGES = {
    "capability": (
        "Use an assistant with the required operation or ask an "
        "administrator to bind that capability."
    ),
    "configuration": "Ask an administrator to configure or authorize it.",
    "policy": "Continue from the evidence already collected.",
    "transient": "Retry later or use another available capability.",
    "request": "Provide the missing or corrected input, then retry.",
    "tool": "Use another available capability or contact an administrator.",
    "verification": (
        "Review the execution details and retry; no verified result was "
        "returned."
    ),
}


def sanitize_loaded_skills(items):
    """Return Skill identities without definitions or environment data."""

    fields = (
        "skill_uuid",
        "skill_package_name",
        "skill_name",
        "version",
        "content_hash",
    )
    return [
        {key: item.get(key) for key in fields if item.get(key) is not None}
        for item in items or []
        if isinstance(item, dict)
    ]


def sanitize_loaded_mcps(items):
    """Return MCP identities without endpoints, headers, or config."""

    fields = (
        "mcp_uuid",
        "mcp_name",
        "version",
        "content_hash",
        "transport",
    )
    return [
        {key: item.get(key) for key in fields if item.get(key) is not None}
        for item in items or []
        if isinstance(item, dict)
    ]


def _text(value, limit=240):
    return str(value or "")[:limit]


def _safe_activity_id(value):
    activity_id = _text(value, 64).strip()
    if not activity_id:
        return ""
    if not activity_id.isascii() or not all(
        char.isalnum() or char in "-_" for char in activity_id
    ):
        return ""
    return activity_id


def _date_argument(arguments, name):
    """Return one ISO date from an allowlisted command argument."""

    if not isinstance(arguments, list):
        return ""
    try:
        index = arguments.index(name)
        value = str(arguments[index + 1])
    except (IndexError, ValueError):
        return ""
    if value == "[REDACTED]" or len(value) > 64:
        return ""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.date().isoformat()
    except ValueError:
        return ""


def _safe_order_reference(value):
    """Return one allowlisted business reference without raw CLI context."""

    reference = str(value or "").strip()
    if (
        not reference
        or len(reference) > 64
        or not reference.isascii()
        or not reference[0].isalnum()
        or any(
            not (char.isalnum() or char in "._-")
            for char in reference
        )
    ):
        return ""
    return reference


def _argument_after(arguments, *parts):
    """Return the argument after one exact command or option sequence."""

    if not isinstance(arguments, list):
        return ""
    normalized = [str(item).strip().lower() for item in arguments]
    expected = list(parts)
    width = len(expected)
    for index in range(len(normalized) - width + 1):
        if normalized[index : index + width] != expected:
            continue
        try:
            return str(arguments[index + width])
        except IndexError:
            return ""
    return ""


def _order_reference_argument(arguments):
    """Return an allowlisted order reference from supported CLI shapes."""

    value = _argument_after(arguments, "order", "get")
    if not value:
        value = _argument_after(arguments, "order", "view")
    if not value:
        value = _argument_after(arguments, "--code")
    return _safe_order_reference(value)


def _contains_command(arguments, *parts):
    if not isinstance(arguments, list):
        return False
    normalized = [str(item).strip().lower() for item in arguments]
    expected = list(parts)
    width = len(expected)
    return any(
        normalized[index : index + width] == expected
        for index in range(len(normalized) - width + 1)
    )


def _tool_activity_kind(tool_name, item):
    """Return a safe semantic kind derived from an observed tool event."""

    if tool_name in {"run_skill_script", "run_skill_artifact"}:
        arguments = item.get("args_redacted")
        if _contains_command(arguments, "auth", "status"):
            return "checking_authentication"
        if _contains_command(arguments, "auth", "login"):
            return "authenticating"
        if _contains_command(arguments, "version"):
            return "checking_tool"
        if (
            _contains_command(arguments, "order")
            and isinstance(arguments, list)
            and any(
                str(argument).strip().lower() in {"--help", "-h", "help"}
                for argument in arguments
            )
        ):
            return "reading_order_commands"
        if _contains_command(arguments, "order", "list"):
            return "query_orders"
        if _contains_command(
            arguments, "order", "get"
        ) or _contains_command(arguments, "order", "view"):
            return "get_order_detail"
        return "querying_data"
    if tool_name == "analyze_structured_output":
        operation = str(item.get("operation") or "").strip().lower()
        if operation == "count":
            return "count_results"
        if operation == "group_count":
            return "group_results"
        return "analyzing_results"
    if tool_name in {"inspect_saved_output", "run_skill_transform"}:
        return "analyzing_results"
    if tool_name == "call_skill_api":
        return "querying_data"
    return ""


def _activity_stage_kind(activity_kind):
    """Return the safe stage that owns one General Chat step."""

    if activity_kind in {
        "get_order_detail",
        "query_orders",
    }:
        return "order_query"
    if activity_kind in {
        "authenticating",
        "checking_authentication",
        "checking_tool",
        "reading_order_commands",
    }:
        return "preparation"
    if activity_kind in {
        "analyzing_results",
        "count_results",
        "group_results",
    }:
        return "result_analysis"
    return "data_query"


def _sanitize_tool_activity(item):
    """Return one replayable activity without raw model/tool arguments."""

    if item.get("runtime_scope") != "general_chat":
        return None
    agent_event = _text(item.get("agent_event"), 128)
    if not agent_event.startswith("tool."):
        return None
    body = agent_event[5:]
    tool_name, separator, suffix = body.rpartition(".")
    status = TOOL_ACTIVITY_STATUSES.get(suffix)
    if not separator or not status:
        return None
    activity_id = _safe_activity_id(item.get("invocation_id"))
    kind = _tool_activity_kind(tool_name, item)
    if not activity_id or not kind:
        return None
    payload = {
        "id": activity_id,
        "kind": kind,
        "stage_kind": _activity_stage_kind(kind),
        "status": status,
    }
    if kind == "query_orders" and status == "in_progress":
        arguments = item.get("args_redacted")
        start_date = _date_argument(
            arguments,
            "--start-time",
        ) or _date_argument(arguments, "--start")
        end_date = _date_argument(
            arguments,
            "--end-time",
        ) or _date_argument(arguments, "--end")
        if start_date:
            payload["start_date"] = start_date
        if end_date:
            payload["end_date"] = end_date
    if kind in {"query_orders", "get_order_detail"}:
        order_reference = _order_reference_argument(
            item.get("args_redacted")
        )
        if order_reference:
            payload["order_ref"] = order_reference
    return {
        "event_type": "activity.recorded",
        "visibility": "user",
        "payload": payload,
    }


def sanitize_termination_detail(detail):
    """Return the allowlisted user-visible termination fields."""

    if not isinstance(detail, dict):
        return {}
    output = {}
    reason = detail.get("reason")
    if reason in PUBLIC_TERMINATION_REASONS:
        output["reason"] = reason
    trigger = detail.get("trigger")
    if trigger in PUBLIC_TERMINATION_REASONS:
        output["trigger"] = trigger
    capability = detail.get("capability")
    if capability in PUBLIC_CAPABILITIES:
        output["capability"] = capability
    error_type = detail.get("error_type")
    if error_type in PUBLIC_ERROR_TYPES:
        output["error_type"] = error_type
        output["recovery"] = RECOVERY_MESSAGES[error_type]
    code = detail.get("code")
    if code in PUBLIC_RUNTIME_CODES:
        output["code"] = code
    if reason == "needs_user_input":
        request = detail.get("request")
        if isinstance(request, dict):
            request_id = str(request.get("request_id") or "").strip()
            question = str(request.get("question") or "").strip()
            answer_type = request.get("answer_type")
            clarification_reason = request.get("reason")
            if (
                request_id
                and len(request_id) <= 128
                and question
                and len(question) <= 1_000
                and answer_type == "text"
                and clarification_reason
                in {"missing_input", "ambiguous_scope", "ambiguous_target"}
            ):
                output["request"] = {
                    "request_id": request_id,
                    "question": question,
                    "reason": clarification_reason,
                    "answer_type": "text",
                }
    return output


def _sanitize_payload(event_type, payload):
    if not isinstance(payload, dict):
        return {}
    if event_type == "route.selected":
        output = {}
        for key, allowed in ROUTE_VALUES.items():
            value = payload.get(key)
            if value in allowed:
                output[key] = value
        capabilities = payload.get("required_capabilities")
        if isinstance(capabilities, list):
            output["required_capabilities"] = [
                item
                for item in capabilities[:8]
                if item in PUBLIC_CAPABILITIES
            ]
        return output
    if event_type == "phase.changed":
        phase = payload.get("phase")
        return {"phase": phase} if phase in PUBLIC_PHASES else {}
    if event_type == "document.progress":
        stage = payload.get("stage")
        if stage not in PUBLIC_DOCUMENT_PROGRESS_STAGES:
            return {}
        return {
            "revision": _positive_int(payload.get("revision")),
            "stage": stage,
            "document_index": min(
                _positive_int(payload.get("document_index")),
                100,
            ),
            "document_total": min(
                _positive_int(payload.get("document_total")),
                100,
            ),
            "image_completed": min(
                _positive_int(payload.get("image_completed")),
                100,
            ),
            "image_total": min(
                _positive_int(payload.get("image_total")),
                100,
            ),
        }
    if event_type == "plan.updated":
        steps = []
        for item in (payload.get("steps") or [])[:12]:
            if not isinstance(item, dict):
                continue
            status = item.get("status")
            steps.append(
                {
                    "id": _text(item.get("id"), 64),
                    "title": _text(item.get("title")),
                    "status": (
                        status
                        if status in PUBLIC_PLAN_STATUSES
                        else "pending"
                    ),
                }
            )
        try:
            revision = max(int(payload.get("revision") or 0), 0)
        except (TypeError, ValueError):
            revision = 0
        return {"revision": revision, "steps": steps}
    if event_type == "stage.updated":
        stage_id = _text(payload.get("id"), 64).strip()
        title = _text(payload.get("title")).strip()
        status = payload.get("status")
        if not stage_id or not title or status not in PUBLIC_STAGE_STATUSES:
            return {}
        try:
            order = min(max(int(payload.get("order") or 1), 1), 12)
        except (TypeError, ValueError):
            order = 1
        try:
            revision = max(int(payload.get("revision") or 0), 0)
        except (TypeError, ValueError):
            revision = 0
        output = {
            "id": stage_id,
            "title": title,
            "status": status,
            "order": order,
            "revision": revision,
        }
        summary = _text(payload.get("summary")).strip()
        if summary:
            output["summary"] = summary
        return output
    if event_type in {
        "capability.blocked",
        "execution.failed",
        "verification.failed",
    }:
        return sanitize_termination_detail(payload)
    if event_type == "artifact.created":
        output = {
            key: _text(payload.get(key), 240)
            for key in ("filename", "content_type")
            if payload.get(key)
        }
        try:
            output["byte_size"] = max(int(payload.get("byte_size") or 0), 0)
        except (TypeError, ValueError):
            output["byte_size"] = 0
        return output
    return {}


def sanitize_runtime_event(item):
    """Return a public event without raw tool arguments or model output."""

    if not isinstance(item, dict):
        return None
    event_type = item.get("event_type")
    if (
        item.get("visibility") == "user"
        and event_type in PUBLIC_EVENT_TYPES
    ):
        activity = (
            "completed"
            if event_type == "phase.changed"
            and (item.get("payload") or {}).get("phase") == "completed"
            else "running"
        )
        output = {
            "agent_event": f"workflow.{event_type}",
            "activity": activity,
            "event_type": event_type,
            "visibility": "user",
            "payload": _sanitize_payload(event_type, item.get("payload")),
        }
        assistant_name = _text(item.get("assistant_name"), 160).strip()
        if assistant_name:
            output["assistant_name"] = assistant_name
        return output
    agent_event = _text(item.get("agent_event"), 128)
    if (
        item.get("runtime_scope") == "general_chat"
        and agent_event.startswith("model.round.")
    ):
        return None
    tool_activity = _sanitize_tool_activity(item)
    if tool_activity is not None:
        return tool_activity
    activity = _text(item.get("activity"), 64)
    if not agent_event and not activity:
        return None
    output = {
        "agent_event": agent_event,
        "activity": activity,
    }
    assistant_name = _text(item.get("assistant_name"), 160).strip()
    if assistant_name:
        output["assistant_name"] = assistant_name
    return output


def public_step_detail(detail):
    """Return only safe runtime events from one persisted step detail."""

    if not isinstance(detail, dict):
        return {"events": []}
    events = []
    activity_by_id = {}
    has_business_result = False
    summary_context = {}
    for item in detail.get("events") or []:
        agent_event = _text(item.get("agent_event"), 128)
        if (
            item.get("runtime_scope") == "general_chat"
            and agent_event == "deepagents.runtime.done"
            and has_business_result
            and _positive_int(item.get("answer_chars"))
        ):
            events.append(
                _summary_activity_event("completed", summary_context)
            )
            continue
        event = sanitize_runtime_event(item)
        if event is None:
            continue
        if event.get("event_type") == "activity.recorded":
            payload = event["payload"]
            activity_id = payload["id"]
            previous = activity_by_id.get(activity_id)
            if previous is not None:
                for key in (
                    "kind",
                    "stage_kind",
                    "start_date",
                    "end_date",
                    "order_ref",
                ):
                    if previous.get(key):
                        payload[key] = previous[key]
            activity_by_id[activity_id] = dict(payload)
            if (
                payload.get("status") == "completed"
                and payload.get("kind") in BUSINESS_ACTIVITY_KINDS
            ):
                has_business_result = True
                summary_context = {
                    key: payload[key]
                    for key in ("order_ref",)
                    if payload.get(key)
                }
        events.append(event)
    return {"events": events}


def _positive_int(value):
    """Return a non-negative int for one internal counter."""

    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def _summary_activity_event(status, context):
    """Return one generic final-result activity with safe context only."""

    payload = {
        "id": "summarize-results",
        "kind": "summarizing_results",
        "stage_kind": "result_analysis",
        "status": status,
    }
    order_reference = _safe_order_reference(context.get("order_ref"))
    if order_reference:
        payload["order_ref"] = order_reference
    return {
        "event_type": "activity.recorded",
        "visibility": "user",
        "payload": payload,
    }
