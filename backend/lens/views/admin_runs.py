"""Admin observability views and helpers for Q&A runs."""

import asyncio
import base64
import hashlib
import json
import uuid as uuid_lib

from agentcore_metering.adapters.django.models import LLMUsage
from asgiref.sync import sync_to_async
from django.db import transaction
from django.db.models import Count, Max, Min, Q, TextField
from django.db.models.functions import Cast
from django.http import HttpResponse, StreamingHttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.access import get_effective_feature_keys
from accounts.permissions import HasRequiredFeature
from lens.attachments import get_session_image_attachments
from lens.citations import public_run_citations
from lens.document_attachments import (
    document_attachment_response,
    get_run_document_attachments,
)
from lens.models import LensNode, Run, RunExecution, RunTraceEvent
from lens.runtime_events import (
    sanitize_loaded_mcps,
    sanitize_loaded_skills,
    sanitize_termination_detail,
)
from lens.serializers import MessageAttachmentSerializer, RunOutputFileSerializer
from lens.services import (
    TERMINAL_RUN_STATUSES,
    cancel_descendant_runs,
    cancel_run_on_lensnode,
    create_execution_run,
    resume_awaiting_run,
    supports_run_admission_checkpoint,
    supports_run_checkpoint_resume,
)

from .base import _authenticate_stream_request

TRAJECTORY_STREAM_BATCH_SIZE = 500
TRAJECTORY_STREAM_POLL_INTERVAL_SECONDS = 0.3
TRAJECTORY_STREAM_PING_INTERVAL_SECONDS = 15
TRAJECTORY_STREAM_TERMINAL_QUIET_POLLS = 3


def _admin_safe_int(value, default, *, minimum=1, maximum=None):
    """Return a clamped positive int parsed from a query value."""

    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(parsed, maximum)
    return parsed


def _admin_run_duration(run):
    """Return execution seconds (started -> finished) or None."""

    return _admin_duration_seconds(run.started_at, run.finished_at)


def _sanitize_delegated_assistants(items):
    """Return delegation identities without runtime config or secrets."""

    result = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        result.append(
            {
                key: item[key]
                for key in (
                    "uuid",
                    "name",
                    "description",
                    "capability",
                    "task",
                )
                if item.get(key) is not None
            }
        )
    return result


def _admin_duration_seconds(started_at, finished_at):
    """Return a non-negative duration in seconds or None."""

    if not started_at or not finished_at:
        return None
    return max(0, round((finished_at - started_at).total_seconds(), 1))


def _admin_step_detail(step):
    """Return a mapping and object-only events from persisted step data."""

    detail = step.detail if isinstance(step.detail, dict) else {}
    events = detail.get("events")
    if not isinstance(events, list):
        events = []
    return detail, [event for event in events if isinstance(event, dict)]


def _admin_run_step_counts(run):
    """Aggregate event/subagent/LLM counts and token usage from steps."""

    counts = {
        "event_count": 0,
        "subagent_count": 0,
        "subagent_denied_count": 0,
        "structured_analysis_calls": 0,
        "structured_validation_calls": 0,
        "transform_calls": 0,
        "llm_calls": 0,
        "total_tokens": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "planned_evidence": {},
    }
    total_cost = 0.0
    has_cost = False
    for step in run.steps.all():
        detail, events = _admin_step_detail(step)
        for event in events:
            counts["event_count"] += 1
            agent_event = event.get("agent_event")
            if agent_event == "tool.task.invoke":
                counts["subagent_count"] += 1
            elif agent_event == "tool.task.denied":
                counts["subagent_denied_count"] += 1
            elif agent_event == "tool.analyze_structured_output.start":
                if event.get("operation") == "validate_records":
                    prefix = "structured_validation"
                else:
                    prefix = "structured_analysis"
                counts[f"{prefix}_calls"] += 1
            elif agent_event == "tool.run_skill_transform.start":
                counts["transform_calls"] += 1
            elif agent_event == "llm.response":
                counts["llm_calls"] += 1
                counts["total_tokens"] += event.get("total_tokens") or 0
                counts["prompt_tokens"] += event.get("prompt_tokens") or 0
                counts["completion_tokens"] += event.get("completion_tokens") or 0
                cost = event.get("cost")
                if cost:
                    total_cost += cost
                    has_cost = True
            elif agent_event == "planned_evidence.metrics":
                counts["planned_evidence"] = {
                    key: event.get(key)
                    for key in (
                        "plan_version",
                        "planned_operation_count",
                        "planned_codegraph_operations",
                        "model_call_count",
                        "retrieval_call_count",
                        "codegraph_call_count",
                        "literal_search_call_count",
                        "file_read_call_count",
                        "evidence_tokens",
                        "evidence_item_count",
                        "evidence_files",
                        "deduplicated_item_count",
                        "fallback_rounds",
                        "evidence_gap_count",
                        "citation_count",
                        "unsupported_claim_count",
                        "citation_coverage_ratio",
                        "sufficient",
                        "gap_categories",
                    )
                    if key in event
                }
        # Control-plane preprocess calls (query rewrite, vision intent)
        # record their usage on the step itself, not as node events.
        usage = detail.get("usage")
        if usage:
            counts["llm_calls"] += 1
            counts["total_tokens"] += usage.get("total_tokens") or 0
            counts["prompt_tokens"] += usage.get("prompt_tokens") or 0
            counts["completion_tokens"] += usage.get("completion_tokens") or 0
            cost = usage.get("cost")
            if cost:
                total_cost += cost
                has_cost = True
    counts["subagent_count"] = max(
        0,
        counts["subagent_count"] - counts["subagent_denied_count"],
    )
    counts["total_cost"] = round(total_cost, 6) if has_cost else None
    return counts


def _duration_ms(started_at, finished_at):
    """Return non-negative elapsed milliseconds or None."""

    if not started_at or not finished_at:
        return None
    return max(0, round((finished_at - started_at).total_seconds() * 1000))


def _admin_run_model_usage(run):
    """Return metered model calls correlated to one Run UUID."""

    run_uuids = [str(run.uuid)]
    frontier = [run.pk]
    while frontier:
        children = list(
            Run.objects.filter(parent_run_id__in=frontier).values_list(
                "pk", "uuid"
            )
        )
        frontier = [pk for pk, _uuid in children]
        run_uuids.extend(str(run_uuid) for _pk, run_uuid in children)
    usages = LLMUsage.objects.filter(
        metadata__run_uuid__in=run_uuids,
    ).order_by("created_at")
    calls = []
    total_cost = 0.0
    has_cost = False
    for usage in usages:
        metadata = usage.metadata or {}
        if usage.cost is not None:
            total_cost += float(usage.cost)
            has_cost = True
        calls.append(
            {
                "uuid": str(usage.id),
                "model": usage.model,
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "total_tokens": usage.total_tokens,
                "cached_tokens": usage.cached_tokens,
                "reasoning_tokens": usage.reasoning_tokens,
                "cost": float(usage.cost) if usage.cost is not None else None,
                "cost_currency": usage.cost_currency,
                "success": usage.success,
                "is_streaming": usage.is_streaming,
                "is_subagent": bool(metadata.get("is_subagent"))
                or str(metadata.get("run_uuid") or "") != str(run.uuid),
                "source_type": metadata.get("source_type"),
                "started_at": (
                    usage.started_at.isoformat() if usage.started_at else None
                ),
                "finished_at": usage.created_at.isoformat(),
                "duration_ms": _duration_ms(
                    usage.started_at,
                    usage.created_at,
                ),
                "ttft_ms": _duration_ms(
                    usage.started_at,
                    usage.first_chunk_at,
                ),
            }
        )
    if not calls:
        return None
    return {
        "llm_calls": len(calls),
        "models_used": list(
            dict.fromkeys(item["model"] for item in calls if item["model"])
        ),
        "subagent_model_calls": sum(1 for item in calls if item["is_subagent"]),
        "total_tokens": sum(item["total_tokens"] for item in calls),
        "prompt_tokens": sum(item["prompt_tokens"] for item in calls),
        "completion_tokens": sum(item["completion_tokens"] for item in calls),
        "cached_tokens": sum(item["cached_tokens"] for item in calls),
        "reasoning_tokens": sum(item["reasoning_tokens"] for item in calls),
        "total_cost": round(total_cost, 6) if has_cost else None,
        "model_calls": calls,
    }


def _admin_run_failure_summary(run):
    """Return safe terminal failure-scope diagnostics from Run events."""

    empty = {
        "unresolved_failure_count": 0,
        "recovered_failure_count": 0,
        "warning_count": 0,
        "failures": [],
    }
    allowed_capabilities = {
        "artifact_delivery",
        "mcp",
        "skill",
        "tool",
        "workspace",
    }
    allowed_error_types = {
        "capability",
        "configuration",
        "policy",
        "request",
        "tool",
        "transient",
        "verification",
    }
    allowed_scopes = {"recovered", "unresolved", "warning"}
    outcome_event = None
    for step in run.steps.all():
        _detail, events = _admin_step_detail(step)
        for event in events:
            if event.get("agent_event") == "deepagents.runtime.outcome":
                outcome_event = event
    if outcome_event is None:
        return empty

    summary = {}
    for key in (
        "unresolved_failure_count",
        "recovered_failure_count",
        "warning_count",
    ):
        try:
            summary[key] = max(int(outcome_event.get(key) or 0), 0)
        except (TypeError, ValueError):
            summary[key] = 0
    failure_items = outcome_event.get("failures")
    if not isinstance(failure_items, list):
        failure_items = []
    failures = []
    for item in failure_items[:12]:
        if not isinstance(item, dict):
            continue
        capability = item.get("capability")
        error_type = item.get("error_type")
        scope = item.get("scope")
        if (
            capability not in allowed_capabilities
            or error_type not in allowed_error_types
            or scope not in allowed_scopes
        ):
            continue
        failures.append(
            {
                "capability": capability,
                "error_type": error_type,
                "scope": scope,
                "required": item.get("required") is True,
                "affects_required_evidence": (
                    item.get("affects_required_evidence") is True
                ),
            }
        )
    summary["failures"] = failures
    return summary


def _admin_run_row(run):
    """Serialize one run for the observability list."""

    session = run.session
    user = session.user if session else None
    assistant = session.assistant if session else None
    question = (run.input_message.content if run.input_message else "") or ""
    counts = _admin_run_step_counts(run)
    metered_usage = getattr(run, "_admin_metered_usage", None)
    if metered_usage:
        # LLMUsage is the authoritative source once a metering record exists.
        # Step events are intentionally retained as a fallback for active runs
        # and older runs created before metering was enabled.
        for key in (
            "llm_calls",
            "total_tokens",
            "prompt_tokens",
            "completion_tokens",
        ):
            counts[key] = metered_usage[key]
        counts["total_cost"] = metered_usage["total_cost"]
    execution = run.execution if hasattr(run, "execution") else None
    runtime_snapshot = execution.runtime_snapshot if execution else {}
    admitted_at = execution.admitted_at if execution else None
    model_refs = runtime_snapshot.get("model_refs") or {}
    max_tokens = execution.token_budget_max_tokens if execution else None
    budget_consumption = None
    if max_tokens:
        budget_consumption = round(counts["total_tokens"] / max_tokens, 4)
    resume_window_active = bool(
        run.resume_by
        and run.resume_by > timezone.now()
        and run.status in [Run.Status.RUNNING, Run.Status.STREAMING]
        and run.lensnode
        and run.lensnode.status == LensNode.Status.ONLINE
    )
    can_resume = False
    if resume_window_active and execution:
        can_resume = execution.status in [
            RunExecution.Status.QUEUED,
            RunExecution.Status.DISPATCHED,
        ] or (
            execution.status == RunExecution.Status.RUNNING
            and supports_run_checkpoint_resume(run.lensnode)
            and (
                not supports_run_admission_checkpoint(run.lensnode)
                or execution.checkpoint_ready_at is not None
            )
        )
    if hasattr(run, "tool_call_count"):
        tool_call_count = run.tool_call_count
    else:
        tool_call_count = (
            run.trace_events.filter(
                Q(event_type__startswith="tool.") | Q(event_type__startswith="subtool.")
            )
            .exclude(call_id="")
            .values("call_id")
            .distinct()
            .count()
        )
    retry_count = (
        run.retry_count if hasattr(run, "retry_count") else run.retry_runs.count()
    )
    return {
        "uuid": str(run.uuid),
        "status": run.status,
        "executor_status": execution.status if execution else run.status,
        "outcome": run.outcome,
        "termination_detail": sanitize_termination_detail(run.termination_detail),
        "username": user.username if user else None,
        "assistant_name": assistant.name if assistant else None,
        "assistant_slug": assistant.slug if assistant else None,
        "question": question[:160],
        "feedback": run.feedback,
        "feedback_updated_at": (
            run.feedback_updated_at.isoformat() if run.feedback_updated_at else None
        ),
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "admitted_at": admitted_at.isoformat() if admitted_at else None,
        "finished_at": (run.finished_at.isoformat() if run.finished_at else None),
        "duration_seconds": _admin_run_duration(run),
        "control_queue_seconds": _admin_duration_seconds(
            run.created_at,
            run.started_at,
        ),
        "admission_wait_seconds": _admin_duration_seconds(
            run.started_at,
            admitted_at,
        ),
        "lensnode_name": run.lensnode.name if run.lensnode else None,
        "lensnode_uuid": str(run.lensnode.uuid) if run.lensnode else None,
        "model_ref": model_refs.get("agent") or None,
        "event_count": counts["event_count"],
        "tool_call_count": tool_call_count,
        "retry_count": retry_count,
        "retry_of_run_uuid": (str(run.retry_of_run.uuid) if run.retry_of_run else None),
        "subagent_count": counts["subagent_count"],
        "subagent_denied_count": counts["subagent_denied_count"],
        "structured_analysis_calls": counts["structured_analysis_calls"],
        "structured_validation_calls": counts["structured_validation_calls"],
        "transform_calls": counts["transform_calls"],
        "llm_calls": counts["llm_calls"],
        "total_tokens": counts["total_tokens"],
        "prompt_tokens": counts["prompt_tokens"],
        "completion_tokens": counts["completion_tokens"],
        "cached_tokens": (
            metered_usage["cached_tokens"] if metered_usage else 0
        ),
        "reasoning_tokens": (
            metered_usage["reasoning_tokens"] if metered_usage else 0
        ),
        "subagent_model_calls": (
            metered_usage["subagent_model_calls"] if metered_usage else 0
        ),
        "total_cost": counts["total_cost"],
        "token_budget_profile": (execution.token_budget_profile if execution else None),
        "token_budget_max_tokens": max_tokens,
        "token_budget_final_reserve_tokens": (
            execution.token_budget_final_reserve_tokens if execution else None
        ),
        "budget_consumption": budget_consumption,
        "resume_by": run.resume_by.isoformat() if run.resume_by else None,
        "available_actions": {
            "cancel": run.status
            in [Run.Status.QUEUED, Run.Status.RUNNING, Run.Status.STREAMING],
            "retry": run.status in [Run.Status.FAILED, Run.Status.CANCELLED],
            "resume": can_resume,
            "export": True,
        },
        "planned_evidence": counts["planned_evidence"],
        "routing_mode": runtime_snapshot.get("routing_mode") or "direct",
        "assistant_mode": runtime_snapshot.get("assistant_mode") or "direct",
        "allowed_assistant_uuids": runtime_snapshot.get("allowed_assistant_uuids", []),
        "delegated_assistants": _sanitize_delegated_assistants(
            runtime_snapshot.get("subagents", [])
        ),
        "created_at": run.created_at.isoformat() if run.created_at else None,
    }


RESOURCE_SKILL_TOOLS = {
    "call_skill_api",
    "run_skill_artifact",
    "run_skill_script",
    "run_skill_transform",
}
RESOURCE_CALL_START_STATUSES = {"invoke", "start"}


def _resource_identity_candidates(payload):
    """Return bounded resource identity candidates from one tool payload."""

    candidates = []
    sources = [payload]
    for key in ("arguments", "args", "input", "params"):
        value = payload.get(key) if isinstance(payload, dict) else None
        if isinstance(value, dict):
            sources.append(value)
    keys = (
        "mcp_name",
        "mcp_uuid",
        "server_name",
        "server_uuid",
        "skill_name",
        "skill_package_name",
        "skill_uuid",
        "skill",
        "resource_name",
        "resource_uuid",
    )
    for source in sources:
        for key in keys:
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                candidates.append(value.strip()[:180])
    return candidates


def _configured_resource_name(candidates, resources, identity_keys):
    """Match an observed identity to one configured resource name."""

    for resource in resources:
        identities = {
            str(resource.get(key)).strip() for key in identity_keys if resource.get(key)
        }
        if identities.intersection(candidates):
            return (
                resource.get("skill_name")
                or resource.get("skill_package_name")
                or resource.get("mcp_name")
                or ""
            )
    return ""


def _configured_plugin_resources(loaded_plugins):
    """Return secret-free Plugin identities and their frozen tool keys."""

    resources = []
    for item in loaded_plugins or []:
        if not isinstance(item, dict):
            continue
        plugin_key = str(item.get("plugin_key") or "").strip()[:64]
        plugin_name = str(
            item.get("plugin_display_name") or plugin_key
        ).strip()[:160]
        plugin_version = str(item.get("plugin_version") or "").strip()[:64]
        tool_keys = []
        for tool in item.get("tools") or []:
            if not isinstance(tool, dict):
                continue
            tool_key = str(tool.get("key") or "").strip()[:180]
            if tool_key and tool_key not in tool_keys:
                tool_keys.append(tool_key)
        if not plugin_key or not plugin_name:
            continue
        resources.append(
            {
                "plugin_key": plugin_key,
                "plugin_name": plugin_name,
                "plugin_version": plugin_version,
                "tool_keys": tool_keys,
            }
        )
    return resources


def _is_plugin_virtual_skill(item):
    """Return whether a loaded Skill is the context view of a Plugin."""

    if not isinstance(item, dict):
        return False
    definition = item.get("definition")
    return item.get("skill_kind") == "plugin_virtual" or (
        isinstance(definition, dict)
        and definition.get("plugin_virtual") is True
    )


def _classify_resource_call(
    name,
    payload,
    configured_skills,
    configured_mcps,
    configured_plugins,
):
    """Return the resource type and display name for one observed call."""

    candidates = set(_resource_identity_candidates(payload))
    resource_type = "tool"
    resource_name = name
    plugin_name = ""
    skill_keys = ("skill_uuid", "skill_package_name", "skill_name")
    mcp_keys = ("mcp_uuid", "mcp_name")
    plugin = next(
        (
            item
            for item in configured_plugins
            if name in item.get("tool_keys", [])
        ),
        None,
    )
    if plugin:
        resource_type = "plugin"
        plugin_name = plugin["plugin_name"]
    elif name.startswith("mcp__"):
        resource_type = "mcp"
        configured_name = _configured_resource_name(
            candidates,
            configured_mcps,
            mcp_keys,
        )
        if not configured_name:
            server_token = name.split("__")[1:2]
            server_token = server_token[0].replace("-", "_") if server_token else ""
            for resource in configured_mcps:
                configured_token = str(resource.get("mcp_name") or "")
                normalized = "".join(
                    char.lower() for char in configured_token if char.isalnum()
                )
                if server_token and server_token.replace("_", "") in normalized:
                    configured_name = configured_token
                    break
        resource_name = configured_name or name
    elif name in RESOURCE_SKILL_TOOLS or candidates.intersection(
        {
            str(value)
            for resource in configured_skills
            for key in skill_keys
            for value in [resource.get(key)]
            if value
        }
    ):
        resource_type = "skill"
        resource_name = (
            _configured_resource_name(
                candidates,
                configured_skills,
                skill_keys,
            )
            or name
        )
    return resource_type, resource_name, plugin_name


def _resource_call_from_step_event(
    event,
    configured_skills,
    configured_mcps,
    configured_plugins,
):
    """Return one resource call from a persisted step event, if applicable."""

    agent_event = str(event.get("agent_event") or "")
    if not agent_event.startswith("tool."):
        return None
    body = agent_event[5:]
    tool_name, separator, suffix = body.rpartition(".")
    if not separator or suffix not in RESOURCE_CALL_START_STATUSES:
        return None
    name = str(event.get("tool") or tool_name).strip()
    payload = dict(event)
    if event.get("skill"):
        payload["skill_name"] = event["skill"]
    if event.get("server"):
        payload["mcp_name"] = event["server"]
    resource_type, resource_name, plugin_name = _classify_resource_call(
        name,
        payload,
        configured_skills,
        configured_mcps,
        configured_plugins,
    )
    call = {
        "resource_type": resource_type,
        "name": resource_name,
        "invocation_id": str(
            event.get("invocation_id") or event.get("call_id") or ""
        ).strip(),
    }
    if plugin_name:
        call["plugin_name"] = plugin_name
    return call


def _admin_run_resource_usage(run, execution):
    """Summarize configured resources and distinct observed tool calls."""

    loaded_skills = execution.loaded_skills if execution else []
    configured_skills = sanitize_loaded_skills(
        [item for item in loaded_skills if not _is_plugin_virtual_skill(item)]
    )
    configured_mcps = sanitize_loaded_mcps(execution.loaded_mcps if execution else [])
    configured_plugins = _configured_plugin_resources(
        execution.loaded_plugins if execution else []
    )
    calls = {}
    resources = {}

    def add_configured_resource(resource_type, item):
        if resource_type == "skill":
            name = item.get("skill_name") or item.get("skill_package_name")
        else:
            name = item.get("mcp_name")
        name = name or "-"
        key = (resource_type, "", name)
        resources.setdefault(
            key,
            {
                "resource_type": resource_type,
                "name": name,
                "configured": True,
                "calls": 0,
            },
        )

    for item in configured_skills:
        add_configured_resource("skill", item)
    for item in configured_mcps:
        add_configured_resource("mcp", item)

    def add_call(resource_type, resource_name, plugin_name=""):
        key = (resource_type, plugin_name, resource_name)
        item = calls.setdefault(
            key,
            {
                "resource_type": resource_type,
                "name": resource_name,
                "calls": 0,
            },
        )
        if plugin_name:
            item["plugin_name"] = plugin_name
        item["calls"] += 1
        if key in resources:
            resources[key]["calls"] += 1
        else:
            resource = {
                "resource_type": resource_type,
                "name": resource_name,
                "configured": resource_type == "plugin",
                "calls": 1,
            }
            if plugin_name:
                resource["plugin_name"] = plugin_name
            resources[key] = resource

    step_calls = []
    for step in run.steps.all():
        detail = step.detail if isinstance(step.detail, dict) else {}
        events = detail.get("events")
        if not isinstance(events, list):
            continue
        for event in events:
            if not isinstance(event, dict):
                continue
            call = _resource_call_from_step_event(
                event,
                configured_skills,
                configured_mcps,
                configured_plugins,
            )
            if call:
                step_calls.append(call)

    if step_calls:
        seen_invocations = set()
        for call in step_calls:
            invocation_id = call["invocation_id"]
            if invocation_id and invocation_id in seen_invocations:
                continue
            if invocation_id:
                seen_invocations.add(invocation_id)
            add_call(
                call["resource_type"],
                call["name"],
                call.get("plugin_name", ""),
            )
    else:
        seen_call_ids = set()
        events = run.trace_events.filter(
            Q(event_type__startswith="tool.") | Q(event_type__startswith="subtool.")
        ).exclude(call_id="")
        for event in events.order_by("sequence"):
            payload = event.payload if isinstance(event.payload, dict) else {}
            name = str(payload.get("name") or payload.get("tool_name") or "").strip()
            if not name or event.call_id in seen_call_ids:
                continue
            seen_call_ids.add(event.call_id)
            resource_type, resource_name, plugin_name = _classify_resource_call(
                name,
                payload,
                configured_skills,
                configured_mcps,
                configured_plugins,
            )
            add_call(resource_type, resource_name, plugin_name)

    return {
        "configured_skills": configured_skills,
        "configured_mcps": configured_mcps,
        "configured_plugins": configured_plugins,
        "calls": list(calls.values()),
        "resources": list(resources.values()),
        "configured_count": (
            len(configured_skills)
            + len(configured_mcps)
            + len(configured_plugins)
        ),
        "called_resource_count": sum(item["calls"] > 0 for item in resources.values()),
        "total_calls": sum(item["calls"] for item in calls.values()),
    }


def _admin_run_detail(run):
    """Serialize a run with full Q&A, timeline and execution snapshot."""

    # Attach metered usage before serializing the summary fields so the detail
    # row and the dedicated usage payload use the same authoritative totals.
    model_usage = _admin_run_model_usage(run)
    run._admin_metered_usage = model_usage
    row = _admin_run_row(run)
    out = run.output_message
    assistant = run.session.assistant if run.session else None
    execution = run.execution if hasattr(run, "execution") else None
    runtime_snapshot = execution.runtime_snapshot if execution else {}
    agent_rounds = execution.agent_rounds if execution else None
    if agent_rounds is None and assistant:
        agent_rounds = assistant.agent_rounds
    steps = []
    for step in run.steps.all():
        detail, events = _admin_step_detail(step)
        item = {
            "step_type": step.step_type,
            "status": step.status,
            "sequence": step.sequence,
            "events": events,
            "usage": detail.get("usage"),
            "updated_at": (step.updated_at.isoformat() if step.updated_at else None),
        }
        if step.step_type == "multimodal":
            reason = detail.get("reason")
            if isinstance(reason, str) and reason.isascii() and len(reason) <= 64:
                item["failure_reason"] = reason
        if step.step_type == "multimodal":
            item["multimodal"] = {
                "query": detail.get("query"),
                "image_count": detail.get("image_count"),
                "rewritten": detail.get("rewritten"),
            }
        steps.append(item)
    attachments = []
    direct_attachment_uuids = set()
    if run.input_message:
        direct_attachment_uuids = {
            str(value)
            for value in run.input_message.attachments.values_list("uuid", flat=True)
        }
        direct_attachment_uuids.update(
            str(value)
            for value in (run.execution.runtime_snapshot or {}).get(
                "direct_attachment_uuids",
                [],
            )
        )
        selected = (run.execution.runtime_snapshot or {}).get(
            "session_attachment_uuids", []
        )
        selected_images = get_session_image_attachments(
            run.session,
            selected,
        )
        if selected:
            attachments = MessageAttachmentSerializer(
                selected_images,
                many=True,
            ).data
        else:
            attachments = MessageAttachmentSerializer(
                run.input_message.attachments.all(),
                many=True,
            ).data
    attachments.extend(
        document_attachment_response(item)
        for item in get_run_document_attachments(
            run.uuid,
            fail_silently=True,
        )
    )
    for attachment in attachments:
        attachment["source"] = (
            "direct"
            if str(attachment.get("uuid")) in direct_attachment_uuids
            else "inherited"
        )
    output_files = RunOutputFileSerializer(run.output_files.all(), many=True).data
    row.update(
        {
            "question": (run.input_message.content if run.input_message else "") or "",
            "attachments": attachments,
            "answer": (out.content if out else "") or "",
            "citations": public_run_citations(run.citations),
            "output_files": output_files,
            "error": run.error or "",
            "agent_rounds": agent_rounds,
            "failure_summary": _admin_run_failure_summary(run),
            "trace_event_count": run.trace_events.count(),
            "steps": steps,
            "execution": (
                {
                    "task": execution.task,
                    "status": execution.status,
                    "target_dirs": execution.target_dirs,
                    "routing_mode": runtime_snapshot.get("routing_mode") or "direct",
                    "assistant_mode": (
                        runtime_snapshot.get("assistant_mode") or "direct"
                    ),
                    "allowed_assistant_uuids": runtime_snapshot.get(
                        "allowed_assistant_uuids", []
                    ),
                    "delegated_assistants": _sanitize_delegated_assistants(
                        runtime_snapshot.get("subagents", [])
                    ),
                    "loaded_skills": sanitize_loaded_skills(execution.loaded_skills),
                    "loaded_mcps": sanitize_loaded_mcps(execution.loaded_mcps),
                    "resource_usage": _admin_run_resource_usage(
                        run,
                        execution,
                    ),
                }
                if execution
                else None
            ),
        }
    )
    if model_usage:
        row.update(model_usage)
    else:
        row.update(
            {
                "cached_tokens": 0,
                "reasoning_tokens": 0,
                "subagent_model_calls": 0,
                "models_used": [],
                "model_calls": [],
            }
        )
    return row


class AdminRunListView(APIView):
    """Admin-only cross-user list of Q&A runs for observability."""

    permission_classes = [HasRequiredFeature]
    required_feature = "admin_console"

    def get(self, request):
        """Return a filtered, paginated list of runs."""

        params = request.query_params
        page = _admin_safe_int(params.get("page"), 1)
        page_size = _admin_safe_int(params.get("page_size"), 20, maximum=100)
        qs = (
            Run.objects.filter(parent_run__isnull=True)
            .select_related(
                "session__user",
                "session__assistant",
                "input_message",
                "lensnode",
                "execution",
                "retry_of_run",
            )
            .prefetch_related("steps")
            .order_by("-created_at")
        )
        username = (params.get("username") or "").strip()
        if username:
            qs = qs.filter(session__user__username__icontains=username)
        user_id = (params.get("user_id") or "").strip()
        if user_id:
            qs = qs.filter(session__user_id=user_id)
        group_id = (params.get("group_id") or "").strip()
        if group_id:
            qs = qs.filter(session__user__groups__id=group_id).distinct()
        assistant = (params.get("assistant") or "").strip()
        if assistant:
            qs = qs.filter(session__assistant__slug=assistant)
        lensnode = (params.get("lensnode") or "").strip()
        if lensnode:
            try:
                lensnode_uuid = uuid_lib.UUID(lensnode)
            except ValueError:
                qs = qs.filter(lensnode__name__icontains=lensnode)
            else:
                qs = qs.filter(lensnode__uuid=lensnode_uuid)
        model = (params.get("model") or "").strip()
        if model:
            qs = qs.filter(execution__runtime_snapshot__model_refs__agent=model)
        run_status = (params.get("status") or "").strip()
        keyword = (params.get("q") or "").strip()
        if keyword:
            qs = qs.filter(input_message__content__icontains=keyword)
        start_date = parse_date((params.get("start_date") or "").strip())
        if start_date:
            qs = qs.filter(created_at__date__gte=start_date)
        end_date = parse_date((params.get("end_date") or "").strip())
        if end_date:
            qs = qs.filter(created_at__date__lte=end_date)
        summary_rows = qs.order_by().values("status").annotate(count=Count("pk"))
        status_counts = {item["status"]: item["count"] for item in summary_rows}
        summary = {
            "total": sum(status_counts.values()),
            **{
                status_value: status_counts.get(status_value, 0)
                for status_value, _label in Run.Status.choices
            },
        }
        if run_status:
            if run_status == "active":
                qs = qs.filter(status__in=[Run.Status.RUNNING, Run.Status.STREAMING])
            else:
                qs = qs.filter(status=run_status)

        qs = qs.annotate(
            tool_call_count=Count(
                "trace_events__call_id",
                filter=(
                    Q(trace_events__event_type__startswith="tool.")
                    | Q(trace_events__event_type__startswith="subtool.")
                )
                & ~Q(trace_events__call_id=""),
                distinct=True,
            ),
            retry_count=Count("retry_runs", distinct=True),
        )

        total = qs.count()
        start = (page - 1) * page_size
        page_runs = list(qs[start : start + page_size])
        run_ids = [str(run.uuid) for run in page_runs]
        child_pairs = []
        frontier = run_ids
        while frontier:
            rows = list(
                Run.objects.filter(parent_run__uuid__in=frontier).values_list(
                    "uuid", "parent_run__uuid"
                )
            )
            child_pairs.extend(rows)
            frontier = [str(child) for child, _parent in rows]
        usage_ids = run_ids + [str(child) for child, _parent in child_pairs]
        usages = LLMUsage.objects.filter(metadata__run_uuid__in=usage_ids)
        parent_by_child = {
            str(child): str(parent) for child, parent in child_pairs
        }
        usage_owner = {}
        for child, parent in child_pairs:
            owner = str(parent)
            while owner in parent_by_child:
                owner = parent_by_child[owner]
            usage_owner[str(child)] = owner
        usage_by_run = {}
        for usage in usages:
            usage_run_id = str((usage.metadata or {}).get("run_uuid") or "")
            run_id = usage_owner.get(usage_run_id, usage_run_id)
            if not run_id:
                continue
            usage_by_run.setdefault(run_id, []).append(usage)
            if run_id != usage_run_id:
                usage_by_run.setdefault(usage_run_id, []).append(usage)
        for run in page_runs:
            calls = usage_by_run.get(str(run.uuid), [])
            if calls:
                run._admin_metered_usage = {
                    "llm_calls": len(calls),
                    "subagent_model_calls": sum(
                        bool((item.metadata or {}).get("is_subagent"))
                        or str((item.metadata or {}).get("run_uuid") or "")
                        in usage_owner
                        for item in calls
                    ),
                    "total_tokens": sum(
                        item.total_tokens or 0 for item in calls
                    ),
                    "prompt_tokens": sum(
                        item.prompt_tokens or 0 for item in calls
                    ),
                    "completion_tokens": sum(
                        item.completion_tokens or 0 for item in calls
                    ),
                    "cached_tokens": sum(
                        item.cached_tokens or 0 for item in calls
                    ),
                    "reasoning_tokens": sum(
                        item.reasoning_tokens or 0 for item in calls
                    ),
                    "total_cost": (
                        round(
                            sum(
                                float(item.cost or 0)
                                for item in calls
                                if item.cost is not None
                            ),
                            6,
                        )
                        if any(item.cost is not None for item in calls)
                        else None
                    ),
                }
        rows = [_admin_run_row(run) for run in page_runs]
        return Response(
            {
                "results": rows,
                "total": total,
                "page": page,
                "page_size": page_size,
                "summary": summary,
            }
        )


class AdminRunDetailView(APIView):
    """Admin-only full trace of a single Q&A run."""

    permission_classes = [HasRequiredFeature]
    required_feature = "admin_console"

    def get(self, request, uuid):
        """Return the full trace for one run."""

        try:
            run = (
                Run.objects.select_related(
                    "session__user",
                    "session__assistant",
                    "input_message",
                    "output_message",
                    "lensnode",
                    "execution",
                    "retry_of_run",
                )
                .prefetch_related("output_files", "steps")
                .get(uuid=uuid)
            )
        except Run.DoesNotExist:
            return Response(
                {"detail": "Run not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(_admin_run_detail(run))


def _admin_run_for_action(run_uuid, *, lock=False):
    """Return one fully-related Run for a server-authorized admin action."""

    queryset = Run.objects.select_related(
        "session__user",
        "session__assistant",
        "input_message",
        "lensnode",
        "execution",
        "retry_of_run",
    )
    if lock:
        queryset = queryset.select_for_update(of=("self",))
    return queryset.filter(uuid=run_uuid).first()


class AdminRunCancelView(APIView):
    """Cancel one active Run across users from the operations console."""

    permission_classes = [HasRequiredFeature]
    required_feature = "admin_console"

    def post(self, request, uuid):
        """Cancel a queued or active Run idempotently."""

        descendants = []
        with transaction.atomic():
            run = _admin_run_for_action(uuid, lock=True)
            if run is None:
                return Response(
                    {"detail": "Run not found."},
                    status=status.HTTP_404_NOT_FOUND,
                )
            if run.status not in [
                Run.Status.QUEUED,
                Run.Status.RUNNING,
                Run.Status.STREAMING,
            ]:
                return Response(_admin_run_row(run))
            now = timezone.now()
            run.status = Run.Status.CANCELLED
            run.resume_by = None
            run.finished_at = now
            run.save(
                update_fields=[
                    "status",
                    "resume_by",
                    "finished_at",
                    "updated_at",
                ]
            )
            if hasattr(run, "execution"):
                run.execution.status = RunExecution.Status.CANCELLED
                run.execution.finished_at = now
                run.execution.save(update_fields=["status", "finished_at"])
            descendants = cancel_descendant_runs(run)
        cancel_run_on_lensnode(run)
        for descendant in descendants:
            cancel_run_on_lensnode(descendant)
        return Response(_admin_run_row(run))


class AdminRunRetryView(APIView):
    """Create an audited retry for a failed or cancelled Run."""

    permission_classes = [HasRequiredFeature]
    required_feature = "admin_console"

    def post(self, request, uuid):
        """Retry one terminal Run with a caller-provided idempotency key."""

        request_key = str(request.data.get("idempotency_key") or "").strip()
        if not request_key or len(request_key) > 64:
            return Response(
                {"detail": "A valid idempotency_key is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        idempotency_key = f"admin-retry:{uuid}:{request_key}"[:128]
        with transaction.atomic():
            run = _admin_run_for_action(uuid, lock=True)
            if run is None:
                return Response(
                    {"detail": "Run not found."},
                    status=status.HTTP_404_NOT_FOUND,
                )
            if run.status not in [Run.Status.FAILED, Run.Status.CANCELLED]:
                return Response(
                    {"detail": "RUN_NOT_RETRYABLE"},
                    status=status.HTTP_409_CONFLICT,
                )
            existing = Run.objects.filter(
                retry_of_run=run,
                idempotency_key=idempotency_key,
            ).first()
            if existing is not None:
                existing = _admin_run_for_action(existing.uuid)
                return Response(_admin_run_row(existing))
            attachment_uuids = list(
                run.input_message.attachments.values_list("uuid", flat=True)
            )
            attachment_uuids.extend(
                item["uuid"] for item in get_run_document_attachments(run.uuid)
            )
            retry = create_execution_run(
                session=run.session,
                question=run.input_message.content,
                idempotency_key=idempotency_key,
                retry_of_run=run,
                enqueue=True,
                attachment_uuids=attachment_uuids,
                user=run.session.user,
            )
            snapshot = dict(retry.execution.runtime_snapshot or {})
            snapshot["admin_action"] = {
                "action": "retry",
                "actor_user_id": request.user.pk,
                "source_run_uuid": str(run.uuid),
            }
            retry.execution.runtime_snapshot = snapshot
            retry.execution.save(update_fields=["runtime_snapshot"])
        retry = _admin_run_for_action(retry.uuid)
        return Response(_admin_run_row(retry), status=status.HTTP_201_CREATED)


class AdminRunResumeView(APIView):
    """Resume one checkpoint-ready Run parked after a node disconnect."""

    permission_classes = [HasRequiredFeature]
    required_feature = "admin_console"

    def post(self, request, uuid):
        """Request an immediate, state-validated resume attempt."""

        run = _admin_run_for_action(uuid)
        if run is None:
            return Response(
                {"detail": "Run not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        if not run.resume_by or run.status not in [
            Run.Status.RUNNING,
            Run.Status.STREAMING,
        ]:
            return Response(
                {"detail": "RUN_NOT_AWAITING_RESUME"},
                status=status.HTTP_409_CONFLICT,
            )
        if not resume_awaiting_run(run.pk):
            return Response(
                {"detail": "RUN_RESUME_UNAVAILABLE"},
                status=status.HTTP_409_CONFLICT,
            )
        refreshed = _admin_run_for_action(uuid)
        return Response(_admin_run_row(refreshed))


def _trace_event_response(
    event,
    *,
    trace_run=None,
    root_run=None,
    display_sequence=None,
):
    """Serialize one immutable trajectory row."""

    return {
        "uuid": str(event.uuid),
        "event_id": str(event.event_id),
        "sequence": (
            display_sequence if display_sequence is not None else event.sequence
        ),
        "attempt": event.attempt,
        "event_type": event.event_type,
        "timestamp": event.timestamp.isoformat(),
        "checkpoint_id": event.checkpoint_id or None,
        "turn": event.turn,
        "step": event.step,
        "call_id": event.call_id or None,
        "parent_call_id": event.parent_call_id or None,
        "payload": event.payload,
        "created_at": event.created_at.isoformat(),
        "trace_run_uuid": str(trace_run.uuid) if trace_run else None,
        "trace_run_role": (
            "child"
            if trace_run and root_run and trace_run.id != root_run.id
            else "parent"
        ),
        "assistant_name": (
            trace_run.session.assistant.name
            if trace_run and trace_run.session and trace_run.session.assistant
            else None
        ),
    }


def _run_trace_progress(root_run, trace_runs, events):
    """Return the parent and logical delegated tasks with their attempts."""

    event_counts = {
        row["run_id"]: row["count"]
        for row in events.values("run_id").annotate(count=Count("uuid"))
    }
    runs_by_id = {trace_run.id: trace_run for trace_run in trace_runs}

    def attempt_payload(trace_run, attempt):
        assistant = trace_run.session.assistant if trace_run.session_id else None
        input_message = trace_run.input_message
        duration_end = trace_run.finished_at
        if trace_run.started_at and duration_end is None:
            duration_end = timezone.now()
        retry_run = runs_by_id.get(trace_run.retry_of_run_id)
        return {
            "run_uuid": str(trace_run.uuid),
            "attempt": attempt,
            "retry_of_run_uuid": str(retry_run.uuid) if retry_run else None,
            "assistant_name": assistant.name if assistant else None,
            "status": trace_run.status,
            "outcome": trace_run.outcome,
            "started_at": (
                trace_run.started_at.isoformat() if trace_run.started_at else None
            ),
            "finished_at": (
                trace_run.finished_at.isoformat() if trace_run.finished_at else None
            ),
            "duration_ms": _duration_ms(trace_run.started_at, duration_end),
            "event_count": event_counts.get(trace_run.id, 0),
            "task": ((input_message.content or "")[:500] if input_message else ""),
        }

    parent_attempt = attempt_payload(root_run, 1)
    progress = [
        {
            key: value
            for key, value in parent_attempt.items()
            if key not in {"attempt", "retry_of_run_uuid"}
        }
    ]
    progress[0]["role"] = "parent"

    snapshot = root_run.execution.runtime_snapshot or {}
    explicit_assistant_uuids = (
        {
            str(value)
            for value in (
                snapshot.get("routing_assistant_uuids")
                or [snapshot.get("routing_assistant_uuid")]
            )
            if value
        }
        if "routing_question" in snapshot
        else set()
    )
    groups = {}
    child_runs = sorted(
        (trace_run for trace_run in trace_runs if trace_run.id != root_run.id),
        key=lambda item: (item.created_at, item.pk),
    )
    for trace_run in child_runs:
        assistant_uuid = str(trace_run.session.assistant.uuid)
        if assistant_uuid in explicit_assistant_uuids:
            group_key = ("explicit", assistant_uuid)
        else:
            chain_root = trace_run
            seen = set()
            while (
                chain_root.retry_of_run_id in runs_by_id and chain_root.id not in seen
            ):
                seen.add(chain_root.id)
                chain_root = runs_by_id[chain_root.retry_of_run_id]
            group_key = ("run", chain_root.id)
        groups.setdefault(group_key, []).append(trace_run)

    for attempts in groups.values():
        attempt_rows = [
            attempt_payload(trace_run, index)
            for index, trace_run in enumerate(attempts, start=1)
        ]
        first = attempt_rows[0]
        latest = attempt_rows[-1]
        durations = [
            item["duration_ms"]
            for item in attempt_rows
            if item["duration_ms"] is not None
        ]
        progress.append(
            {
                "run_uuid": first["run_uuid"],
                "role": "child",
                "assistant_name": latest["assistant_name"],
                "status": latest["status"],
                "outcome": latest["outcome"],
                "started_at": first["started_at"],
                "finished_at": latest["finished_at"],
                "duration_ms": sum(durations) if durations else None,
                "event_count": sum(item["event_count"] for item in attempt_rows),
                "task": first["task"],
                "attempt_count": len(attempt_rows),
                "attempts": attempt_rows,
            }
        )
    return progress


def _run_trace_summary(
    run,
    trace_runs=None,
    *,
    progress_root=None,
    progress_runs=None,
):
    """Return unfiltered timing, category, call, and usage aggregates."""

    events = RunTraceEvent.objects.filter(run__in=trace_runs or [run])
    progress_root = progress_root or run
    progress_runs = progress_runs or trace_runs or [run]
    progress_events = (
        events
        if progress_runs is trace_runs
        else RunTraceEvent.objects.filter(run__in=progress_runs)
    )
    aggregate = events.aggregate(
        event_count=Count("uuid"),
        first_timestamp=Min("timestamp"),
        last_timestamp=Max("timestamp"),
        model_calls=Count(
            "call_id",
            filter=(Q(event_type__startswith="model.") & ~Q(call_id="")),
            distinct=True,
        ),
        tool_calls=Count(
            "call_id",
            filter=(
                Q(event_type__startswith="tool.") | Q(event_type__startswith="subtool.")
            )
            & ~Q(call_id=""),
            distinct=True,
        ),
        error_count=Count(
            "uuid",
            filter=Q(event_type__endswith=".failed"),
        ),
    )
    metered = list(
        LLMUsage.objects.filter(
            metadata__run_uuid__in=[str(item.uuid) for item in progress_runs]
        )
    )
    if not aggregate["event_count"]:
        summary = {
            "event_count": 0,
            "first_timestamp": None,
            "last_timestamp": None,
            "duration_ms": None,
            "model_calls": len(metered),
            "tool_calls": 0,
            "total_tokens": sum(item.total_tokens or 0 for item in metered),
            "subagent_model_calls": sum(
                bool((item.metadata or {}).get("is_subagent"))
                or str((item.metadata or {}).get("run_uuid") or "")
                != str(run.uuid)
                for item in metered
            ),
            "error_count": 0,
            "categories": {},
        }
        summary["run_progress"] = _run_trace_progress(
            progress_root,
            progress_runs,
            progress_events,
        )
        return summary
    categories = {}
    for row in events.values("event_type").annotate(count=Count("uuid")):
        category = row["event_type"].split(".", 1)[0]
        categories[category] = categories.get(category, 0) + row["count"]
    total_tokens = 0
    token_values = events.filter(event_type="model.completed").values_list(
        "payload__usage__total_tokens",
        flat=True,
    )
    for value in token_values:
        try:
            total_tokens += max(int(value or 0), 0)
        except (TypeError, ValueError):
            pass
    if metered:
        total_tokens = sum(item.total_tokens or 0 for item in metered)
        aggregate["model_calls"] = len(metered)
    first_timestamp = aggregate["first_timestamp"]
    last_timestamp = aggregate["last_timestamp"]
    summary = {
        "event_count": aggregate["event_count"],
        "first_timestamp": first_timestamp.isoformat(),
        "last_timestamp": last_timestamp.isoformat(),
        "duration_ms": max(
            int((last_timestamp - first_timestamp).total_seconds() * 1000),
            0,
        ),
        "model_calls": aggregate["model_calls"],
        "tool_calls": aggregate["tool_calls"],
        "total_tokens": total_tokens,
        "subagent_model_calls": sum(
            bool((item.metadata or {}).get("is_subagent"))
            or str((item.metadata or {}).get("run_uuid") or "")
            != str(run.uuid)
            for item in metered
        ),
        "error_count": aggregate["error_count"],
        "categories": categories,
    }
    summary["run_progress"] = _run_trace_progress(
        progress_root,
        progress_runs,
        progress_events,
    )
    return summary


def _trajectory_context(run_uuid):
    """Load one run and the trace/progress runs visible from it."""

    try:
        run = Run.objects.select_related(
            "execution",
            "session__assistant",
            "input_message",
            "parent_run__execution",
            "parent_run__session__assistant",
            "parent_run__input_message",
        ).get(uuid=run_uuid)
    except Run.DoesNotExist:
        return None
    trace_runs = [run]
    frontier = [run.pk]
    while frontier:
        children = list(
            Run.objects.filter(parent_run_id__in=frontier).select_related(
                "execution",
                "session__assistant",
                "input_message",
            )
        )
        trace_runs.extend(children)
        frontier = [child.pk for child in children]
    progress_root = run.parent_run or run
    if progress_root.id == run.id:
        progress_runs = trace_runs
    else:
        progress_runs = [progress_root]
        frontier = [progress_root.pk]
        while frontier:
            children = list(
                Run.objects.filter(parent_run_id__in=frontier).select_related(
                    "execution",
                    "session__assistant",
                    "input_message",
                )
            )
            progress_runs.extend(children)
            frontier = [child.pk for child in children]
    return run, trace_runs, progress_root, progress_runs


def _encode_trajectory_cursor(event):
    """Return an opaque cursor for one durably persisted event."""

    if event is None:
        return ""
    value = json.dumps(
        {
            "created_at": event.created_at.isoformat(),
            "uuid": str(event.uuid),
        },
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode_trajectory_cursor(cursor):
    """Decode one opaque trajectory cursor or raise ValueError."""

    if not cursor:
        return None
    try:
        padding = "=" * (-len(cursor) % 4)
        value = json.loads(
            base64.urlsafe_b64decode(cursor + padding).decode("utf-8")
        )
        created_at = parse_datetime(value["created_at"])
        event_uuid = uuid_lib.UUID(value["uuid"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid trajectory cursor") from exc
    if created_at is None or timezone.is_naive(created_at):
        raise ValueError("Invalid trajectory cursor")
    return created_at, event_uuid


def _trajectory_cursor_belongs_to_run(run_uuid, cursor):
    """Return whether a cursor names an event in the requested trace."""

    if not cursor:
        return True
    context = _trajectory_context(run_uuid)
    if context is None:
        return False
    _run, trace_runs, _progress_root, _progress_runs = context
    created_at, event_uuid = _decode_trajectory_cursor(cursor)
    return RunTraceEvent.objects.filter(
        run__in=trace_runs,
        created_at=created_at,
        uuid=event_uuid,
    ).exists()


def _trajectory_revision(run, trace_runs, latest_cursor):
    """Return a deterministic revision for current trace and run state."""

    run_states = []
    for trace_run in trace_runs:
        execution = (
            trace_run.execution if hasattr(trace_run, "execution") else None
        )
        run_states.append(
            {
                "uuid": str(trace_run.uuid),
                "status": trace_run.status,
                "outcome": trace_run.outcome,
                "updated_at": trace_run.updated_at.isoformat(),
                "executor_status": execution.status if execution else "",
                "executor_finished_at": (
                    execution.finished_at.isoformat()
                    if execution and execution.finished_at
                    else ""
                ),
            }
        )
    body = json.dumps(
        {
            "root": str(run.uuid),
            "cursor": latest_cursor,
            "runs": run_states,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(body).hexdigest()[:24]


def _trajectory_run_state(run):
    """Return the live run fields needed by the trajectory client."""

    execution = run.execution if hasattr(run, "execution") else None
    return {
        "uuid": str(run.uuid),
        "status": run.status,
        "executor_status": execution.status if execution else run.status,
        "outcome": run.outcome,
        "updated_at": run.updated_at.isoformat(),
    }


def _trajectory_event_matches(event, filters):
    """Return whether an event matches the bounded trajectory filters."""

    event_type = filters.get("event_type", "")
    if event_type and event.event_type != event_type:
        return False
    category = filters.get("category", "")
    if category and not event.event_type.startswith(f"{category}."):
        return False
    call_id = filters.get("call_id", "")
    if (
        call_id
        and event.call_id != call_id
        and event.parent_call_id != call_id
    ):
        return False
    keyword = filters.get("q", "")
    if keyword:
        haystack = " ".join(
            [
                event.event_type or "",
                event.call_id or "",
                event.parent_call_id or "",
                str(event.payload or ""),
            ]
        ).lower()
        if keyword not in haystack:
            return False
    return True


def _trajectory_stream_snapshot(
    run_uuid,
    cursor,
    filters,
    *,
    display_sequence=0,
    known_revision="",
    include_summary=False,
):
    """Build one resumable trajectory stream snapshot from durable state."""

    context = _trajectory_context(run_uuid)
    if context is None:
        return None
    run, trace_runs, progress_root, progress_runs = context
    trace_runs_by_id = {item.id: item for item in trace_runs}
    all_events = RunTraceEvent.objects.filter(run__in=trace_runs)
    events = all_events
    decoded_cursor = _decode_trajectory_cursor(cursor)
    if decoded_cursor is not None:
        cursor_created_at, cursor_uuid = decoded_cursor
        if display_sequence == 0:
            display_sequence = all_events.filter(
                Q(created_at__lt=cursor_created_at)
                | Q(created_at=cursor_created_at, uuid__lte=cursor_uuid)
            ).count()
        events = events.filter(
            Q(created_at__gt=cursor_created_at)
            | Q(created_at=cursor_created_at, uuid__gt=cursor_uuid)
        )
    pending = list(
        events.order_by("created_at", "uuid")[
            : TRAJECTORY_STREAM_BATCH_SIZE + 1
        ]
    )
    raw_batch = pending[:TRAJECTORY_STREAM_BATCH_SIZE]
    rows = [
        _trace_event_response(
            event,
            trace_run=trace_runs_by_id[event.run_id],
            root_run=run,
            display_sequence=sequence,
        )
        for sequence, event in enumerate(
            raw_batch,
            start=display_sequence + 1,
        )
        if _trajectory_event_matches(event, filters)
    ]
    latest_event = all_events.order_by("created_at", "uuid").last()
    next_event = raw_batch[-1] if raw_batch else None
    latest_cursor = _encode_trajectory_cursor(latest_event)
    next_cursor = _encode_trajectory_cursor(next_event) or cursor
    state_runs = {
        trace_run.id: trace_run for trace_run in [*trace_runs, *progress_runs]
    }
    revision = _trajectory_revision(
        run,
        sorted(state_runs.values(), key=lambda item: str(item.uuid)),
        latest_cursor,
    )
    return {
        "events": rows,
        "cursor": next_cursor,
        "latest_cursor": latest_cursor,
        "display_sequence": display_sequence + len(raw_batch),
        "has_more": len(pending) > TRAJECTORY_STREAM_BATCH_SIZE,
        "revision": revision,
        "run": _trajectory_run_state(run),
        "summary": (
            _run_trace_summary(
                run,
                trace_runs,
                progress_root=progress_root,
                progress_runs=progress_runs,
            )
            if include_summary or revision != known_revision
            else None
        ),
        "terminal": run.status in TERMINAL_RUN_STATUSES,
    }


async def stream_admin_run_trajectory_events_async(
    run_uuid,
    *,
    cursor="",
    revision="",
    filters=None,
    display_sequence=0,
    poll_interval=TRAJECTORY_STREAM_POLL_INTERVAL_SECONDS,
    terminal_quiet_polls=TRAJECTORY_STREAM_TERMINAL_QUIET_POLLS,
):
    """Yield resumable admin trajectory updates from the database."""

    current_cursor = cursor
    current_revision = revision
    current_sequence = display_sequence
    current_summary = None
    filters = filters or {}
    last_ping_at = timezone.now()
    quiet_polls = 0
    event_type = "sync"

    while True:
        snapshot = await sync_to_async(_trajectory_stream_snapshot)(
            run_uuid,
            current_cursor,
            filters,
            display_sequence=current_sequence,
            known_revision=current_revision,
            include_summary=event_type == "sync",
        )
        if snapshot is None:
            return
        changed = (
            bool(snapshot["events"])
            or snapshot["cursor"] != current_cursor
            or snapshot["revision"] != current_revision
        )
        if event_type == "sync" or changed:
            current_summary = snapshot["summary"] or current_summary
            payload = {
                "type": event_type,
                "previous_revision": current_revision,
                "revision": snapshot["revision"],
                "cursor": snapshot["cursor"],
                "sequence": snapshot["display_sequence"],
                "events": snapshot["events"],
                "summary": current_summary,
                "run": snapshot["run"],
            }
            yield payload
            current_revision = snapshot["revision"]
            current_cursor = snapshot["cursor"]
            current_sequence = snapshot["display_sequence"]
            event_type = "append"

        if snapshot["has_more"]:
            quiet_polls = 0
            continue

        if snapshot["terminal"]:
            quiet_polls = 0 if changed else quiet_polls + 1
            if quiet_polls >= terminal_quiet_polls:
                yield {
                    "type": "done",
                    "previous_revision": current_revision,
                    "revision": snapshot["revision"],
                    "cursor": snapshot["cursor"],
                    "sequence": snapshot["display_sequence"],
                    "events": [],
                    "summary": current_summary,
                    "run": snapshot["run"],
                }
                return
        else:
            quiet_polls = 0

        now = timezone.now()
        if (
            now - last_ping_at
        ).total_seconds() >= TRAJECTORY_STREAM_PING_INTERVAL_SECONDS:
            last_ping_at = now
            yield {"type": "ping", "ts": now.isoformat()}
        await asyncio.sleep(poll_interval)


class AdminRunTrajectoryView(APIView):
    """Admin-only paginated trajectory for one Q&A run."""

    permission_classes = [HasRequiredFeature]
    required_feature = "admin_console"

    def get(self, request, run_uuid):
        """Return filtered events and unfiltered run summary."""

        context = _trajectory_context(run_uuid)
        if context is None:
            return Response(
                {"detail": "Run not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        run, trace_runs, progress_root, progress_runs = context
        params = request.query_params
        page_size = _admin_safe_int(
            params.get("page_size"),
            200,
            maximum=500,
        )
        after_sequence = _admin_safe_int(
            params.get("after_sequence"),
            0,
            minimum=0,
        )
        trace_runs_by_id = {
            trace_run.id: trace_run for trace_run in trace_runs
        }
        ordered_events = list(
            RunTraceEvent.objects.filter(
                run__in=trace_runs,
            ).order_by("created_at", "uuid")
        )
        trace_events = [
            (sequence, event, trace_runs_by_id[event.run_id])
            for sequence, event in enumerate(ordered_events, start=1)
            if sequence > after_sequence
        ]
        event_type = (params.get("event_type") or "").strip()[:128]
        if event_type:
            trace_events = [
                item
                for item in trace_events
                if item[1].event_type == event_type
            ]
        category = (params.get("category") or "").strip().lower()[:128]
        if category:
            trace_events = [
                item
                for item in trace_events
                if item[1].event_type.startswith(f"{category}.")
            ]
        call_id = (params.get("call_id") or "").strip()[:128]
        if call_id:
            trace_events = [
                item
                for item in trace_events
                if item[1].call_id == call_id
                or item[1].parent_call_id == call_id
            ]
        keyword = (params.get("q") or "").strip()[:256]
        if keyword:
            trace_events = [
                item
                for item in trace_events
                if keyword.lower()
                in " ".join(
                    [
                        item[1].event_type or "",
                        item[1].call_id or "",
                        item[1].parent_call_id or "",
                        str(item[1].payload or ""),
                    ]
                ).lower()
            ]
        total = len(trace_events)
        rows = trace_events[:page_size]
        next_after_sequence = rows[-1][0] if rows else None
        latest_event = ordered_events[-1] if ordered_events else None
        stream_cursor = _encode_trajectory_cursor(latest_event)
        return Response(
            {
                "results": [
                    _trace_event_response(
                        event,
                        trace_run=trace_run,
                        root_run=run,
                        display_sequence=sequence,
                    )
                    for sequence, event, trace_run in rows
                ],
                "total": total,
                "page_size": page_size,
                "after_sequence": after_sequence,
                "next_after_sequence": next_after_sequence,
                "has_more": total > len(rows),
                "stream_cursor": stream_cursor,
                "stream_sequence": len(ordered_events),
                "revision": _trajectory_revision(
                    run,
                    sorted(
                        {
                            item.id: item
                            for item in [*trace_runs, *progress_runs]
                        }.values(),
                        key=lambda item: str(item.uuid),
                    ),
                    stream_cursor,
                ),
                "summary": _run_trace_summary(
                    run,
                    trace_runs,
                    progress_root=progress_root,
                    progress_runs=progress_runs,
                ),
            }
        )


async def admin_run_trajectory_stream_view(request, run_uuid):
    """Stream durable trajectory updates to authorized administrators."""

    user = await sync_to_async(_authenticate_stream_request)(request)
    if user is None:
        return HttpResponse("Unauthorized", status=401)
    feature_keys = await sync_to_async(get_effective_feature_keys)(user)
    if "admin_console" not in feature_keys:
        return HttpResponse("Forbidden", status=403)

    cursor = (request.GET.get("cursor") or "").strip()[:1024]
    try:
        _decode_trajectory_cursor(cursor)
    except ValueError:
        return HttpResponse("Invalid trajectory cursor", status=400)
    context_exists = await sync_to_async(
        lambda: _trajectory_context(run_uuid) is not None
    )()
    if not context_exists:
        return HttpResponse("Not found", status=404)
    if not await sync_to_async(_trajectory_cursor_belongs_to_run)(
        run_uuid,
        cursor,
    ):
        return HttpResponse("Invalid trajectory cursor", status=400)

    revision = (request.GET.get("revision") or "").strip()[:128]
    display_sequence = _admin_safe_int(
        request.GET.get("sequence"),
        0,
        minimum=0,
    )
    filters = {
        "event_type": (request.GET.get("event_type") or "").strip()[:128],
        "category": (
            (request.GET.get("category") or "").strip().lower()[:128]
        ),
        "call_id": (request.GET.get("call_id") or "").strip()[:128],
        "q": (request.GET.get("q") or "").strip().lower()[:256],
    }

    async def event_stream():
        async for event in stream_admin_run_trajectory_events_async(
            run_uuid,
            cursor=cursor,
            revision=revision,
            filters=filters,
            display_sequence=display_sequence,
        ):
            payload = json.dumps(event, ensure_ascii=False)
            yield f"data: {payload}\n\n".encode("utf-8")

    response = StreamingHttpResponse(
        event_stream(),
        content_type="text/event-stream; charset=utf-8",
    )
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response
