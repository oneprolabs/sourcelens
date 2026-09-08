import asyncio
import json
import logging
from pathlib import Path
import re
import threading
import time
import uuid
from types import SimpleNamespace

from deepagents import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    create_deep_agent,
    register_harness_profile,
)
from deepagents.backends.filesystem import FilesystemBackend
from deepagents.backends.local_shell import LocalShellBackend

from langchain_core.messages import HumanMessage, SystemMessage

from ..agent_tools import (
    build_agent_tools,
    build_general_chat_tools,
)
from ..checkpoint import (
    checkpoint_enabled,
    get_checkpoint_saver,
    load_resume_state,
    save_initial_checkpoint,
    save_resume_metadata,
    save_runtime_state,
    thread_config,
)
from ..gateway_model import (
    LensGatewayChatModel,
    RunCancelledError,
)
from ..logging_utils import elapsed_since, task_log, utc_now
from ..mcp_tools import build_deferred_mcp_tools, load_mcp_tools
from ..plugins import collect_agent_runtime_contributions
from ..planned_evidence import (
    ALLOWED_CODEGRAPH_OPERATIONS,
    EvidenceExecutor,
    MAX_OBJECTIVE_CHARS,
    build_evidence_bundle,
    assess_code_analysis_capabilities,
    parse_retrieval_plan,
    validate_citations,
    validate_evidence_sufficiency,
)
from ..plugin_tools import (
    build_plugin_tools,
)
from ..runtime_modes import runtime_mode_for
from .assembly import _agent_middleware, _fast_subagent
from .capabilities import CapabilityBoundaryMiddleware
from .capability_protocol import CAPABILITY_FAMILIES
from .direct_answer import (
    _answer_general_chat_directly,
    _contains_unfulfilled_action_promise,
)
from .execution import (
    EmptyAgentResponseError,
    _emit_new_model_calls,
    _emit_new_tool_calls,
    _model_summary,
    _response_stop_reason,
    _run_agent_with_turn_limit,
    _strip_dangling_tool_call,
    _synthesize_wrapup_answer,
)
from .limits import (
    resolve_token_budget as _resolve_token_budget,
    resolve_tool_call_budget as _resolve_tool_call_budget,
)
from .messages import (
    activity_from_event as _activity_from_event,
    build_initial_messages as _build_initial_messages,
    detail_lines as _detail_lines,
    extract_final_message as _extract_final_message,
    normalize_plan_steps as _normalize_plan_steps,
    tool_call_summary as _tool_call_summary,
)
from .offload import apply_offload_thresholds as _apply_offload_thresholds
from .outcomes import (
    _capability_termination_detail,
    _evidence_termination_detail,
    _finalize_runtime_outcome,
    _unverified_execution_answer,
)
from .prompts import (
    answer_language_requirement as _answer_language_requirement,
    command_answer_language as _command_answer_language,
    detect_answer_language as _detect_answer_language,
    pick_text as _pick_text,
)
from .remote_subagent import RemoteSubagentRunnable
from .resume import (
    pending_checkpoint_tool_calls as _pending_checkpoint_tool_calls,
    reject_unsafe_resume_tool_replay as _reject_unsafe_resume_tool_replay,
)
from .restrictions import NoTaskMiddleware as _NoTaskMiddleware
from .routing import (
    _normalize_route_evidence_capabilities,
    _parse_route_decision,
    _select_general_chat_route,
)
from .scenarios import SCENARIOS
from .summarization import (
    CONTINUATION_SUMMARY_PROMPT,
    LensSummarizationMiddleware,
    build_summarization_middleware as _build_summarization_middleware_impl,
)
from .system_prompts import (
    _context_guidance,
    _general_chat_guidance,
    _general_chat_system_prompt,
    _activity_report_batch_tools,
    _is_activity_report,
    _is_general_chat,
    _knowledge_system_prompt,
    _subagent_guidance,
    _system_prompt,
)
from .tracing import (
    TraceObservationMiddleware,
    observation_timestamp as _observation_timestamp,
)
from ..runtime_resources import (
    cleanup_runtime_resources,
    prepare_runtime_resources,
)

LOGGER = logging.getLogger("lensnode")


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

    if (
        command.get("runtime_route") != "plan_execute"
        or not _is_activity_report(command)
    ):
        return list(tools)
    batch_tools = _activity_report_batch_tools(command)
    batch_by_plugin = {
        key.split("_", 1)[0]: key
        for key in batch_tools
    }
    return [
        tool
        for tool in tools
        if (
            (getattr(tool, "metadata", None) or {}).get("plugin_key")
            not in batch_by_plugin
            or getattr(tool, "name", "")
            == batch_by_plugin.get(
                (getattr(tool, "metadata", None) or {}).get("plugin_key")
            )
        )
    ]
MAX_CONFIGURED_SUBAGENTS = 8

_MAX_PRESENTED_CITATIONS = 5
_DEFAULT_CODE_ANALYSIS_MAX_TURNS = 10
_AGENT_TURNS_BY_ROUNDS = {
    "flash": 5,
    "fast": 13,
    "balanced": 26,
    "deep": 50,
    "max": 100,
}
_EXECUTION_BACKEND_TRUSTED_CONTAINER = "trusted_container"
_EXECUTION_BACKEND_FILESYSTEM = "filesystem"

register_harness_profile(
    "lensgatewaychatmodel",
    HarnessProfile(
        general_purpose_subagent=GeneralPurposeSubagentProfile(
            enabled=False,
        ),
    ),
)


def _resolve_agent_turn_limit(command):
    """Return the bounded model-turn budget for a run."""

    configured = (command or {}).get("max_agent_turns")
    try:
        configured = int(configured) if configured is not None else None
    except (TypeError, ValueError):
        configured = None
    if configured is not None and configured > 0:
        return configured
    agent_rounds = (command or {}).get("agent_rounds")
    if agent_rounds in _AGENT_TURNS_BY_ROUNDS:
        return _AGENT_TURNS_BY_ROUNDS[agent_rounds]
    if (command or {}).get("task") == "code_analysis":
        return _DEFAULT_CODE_ANALYSIS_MAX_TURNS
    return configured


def _vague_code_analysis_question(question):
    """Return whether a Code Analysis question has no analysis target."""

    text = re.sub(r"[\s，。！？、,.!?：:；;]+", "", str(question or ""))
    for prefix in ("请", "麻烦", "帮我", "能否", "可以"):
        text = text.removeprefix(prefix)
    generic = (
        "分析一下",
        "分析下",
        "帮我分析",
        "描述一下",
        "介绍一下",
        "解释一下",
        "看看怎么回事",
    )
    return not text or text in generic


def _build_execution_backend(config, root_dir):
    """Build the configured file and command execution backend.

    ``trusted_container`` deliberately delegates command execution to the
    current LensNode process. The deployment boundary is the LensNode
    container; this backend does not provide an additional sandbox.
    """

    backend_name = getattr(
        config,
        "execution_backend",
        _EXECUTION_BACKEND_TRUSTED_CONTAINER,
    )
    if backend_name == _EXECUTION_BACKEND_TRUSTED_CONTAINER:
        return LocalShellBackend(
            root_dir=str(root_dir),
            virtual_mode=True,
            inherit_env=True,
        )
    if backend_name == _EXECUTION_BACKEND_FILESYSTEM:
        return FilesystemBackend(
            root_dir=str(root_dir),
            virtual_mode=True,
        )
    raise ValueError(
        "Unsupported LENSNODE_EXECUTION_BACKEND: "
        f"{backend_name!r}. Expected "
        f"{_EXECUTION_BACKEND_TRUSTED_CONTAINER!r} or "
        f"{_EXECUTION_BACKEND_FILESYSTEM!r}."
    )


def _virtual_skill_path(resources, path):
    """Return a skill path relative to the Deep Agents virtual root."""

    candidate = Path(path)
    if candidate.is_absolute():
        candidate = candidate.relative_to(resources.root)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError("Skill path must remain inside the run scratch root")
    return candidate.as_posix()








def _build_summarization_middleware(
    config,
    model_ref,
    emit_event,
    cancel_event=None,
    run_uuid="",
    http_client=None,
    trace_context=None,
    emit_observation=None,
    trajectory=None,
):
    """Build summarization while preserving facade monkeypatches."""

    return _build_summarization_middleware_impl(
        config,
        model_ref,
        emit_event,
        cancel_event,
        run_uuid,
        http_client,
        trace_context,
        emit_observation,
        trajectory,
        model_class=LensGatewayChatModel,
        middleware_class=LensSummarizationMiddleware,
    )


class LensDeepAgentRuntime:
    """Run a real LangChain Deep Agents execution for one LensNode command."""

    def __init__(self, config, http_client=None, plugin_http_pool=None):
        self.config = config
        self.http_client = http_client
        self.plugin_http_pool = plugin_http_pool

    async def answer(
        self,
        command,
        emit_progress=None,
        emit_output=None,
        on_activity=None,
        cancel_event=None,
        wrapup_event=None,
        on_checkpoint_ready=None,
    ):
        """Execute a run_start command with create_deep_agent."""

        return await asyncio.to_thread(
            self._answer_sync,
            command,
            emit_progress,
            emit_output,
            on_activity,
            cancel_event,
            wrapup_event,
            on_checkpoint_ready,
        )

    def _answer_sync(
        self,
        command,
        emit_progress=None,
        emit_output=None,
        on_activity=None,
        cancel_event=None,
        wrapup_event=None,
        on_checkpoint_ready=None,
    ):
        """Synchronous Deep Agents invocation run in a worker thread."""

        state = self._prepare_runtime(
            command,
            emit_progress,
            emit_output,
            on_activity,
            cancel_event,
            wrapup_event,
            on_checkpoint_ready,
        )
        try:
            if (
                getattr(getattr(state, "runtime_mode", None), "name", None)
                == "code_analysis"
                and _vague_code_analysis_question(
                    getattr(state, "question", command.get("question", ""))
                )
            ):
                answer = (
                    "请补充具体的分析对象，例如文件路径、类或函数名、"
                    "错误堆栈，或希望追踪的调用链。"
                )
                emit_user_event = getattr(state, "emit_user_event", None)
                if emit_user_event is not None:
                    emit_user_event(
                        "phase.changed",
                        {"phase": "completed"},
                    )
                return {
                    "answer": answer,
                    "samples": [],
                    "stop_reason": state.model.stop_reason,
                    "token_usage": state.model.token_usage,
                    "outcome": "awaiting_user_input",
                    "termination_detail": {
                        "reason": "clarification_required",
                    },
                }
            route_result = self._route_runtime(state)
            if route_result is not None:
                return route_result
            self._build_agent(state)
            return self._execute_agent(state)
        finally:
            if cancel_event is None or not cancel_event.is_set():
                cleanup_runtime_resources(state.resources)
                for resources in getattr(state, "subagent_resources", []):
                    cleanup_runtime_resources(resources)

    def _subagents_enabled(self, state):
        """Return whether this run may delegate work to a subagent.

        General Chat exposes the task tool only on the plan_execute route.
        Smart Collaboration uses the configured local subagents.
        """

        if self._is_smart_collaboration(state.command):
            return state.command.get("task") != "code_analysis"
        if not state.runtime_mode.general_chat:
            return state.command.get("task") != "code_analysis"
        if _is_activity_report(state.command):
            return False
        route_decision = getattr(state, "route_decision", None) or {}
        return route_decision.get("route") == "plan_execute"

    @staticmethod
    def _is_smart_collaboration(command):
        """Return whether a command belongs to Smart Collaboration."""

        return command.get("routing_mode") == "smart"

    def _seed_run_checkpoint(
        self,
        state,
        route_decision=None,
        call_saver=True,
    ):
        """Seed durable checkpoint state for a run before its first model
        call.

        Shared by the planned, route-classified, and deep-agent paths so the
        saver, resume metadata, and initial checkpoint are always seeded the
        same way. Callers guard on resume/checkpoint availability and own any
        error handling on top of the returned success flag. ``call_saver`` is
        False when the caller already resolved the saver (e.g. to store it as
        the graph checkpointer) to avoid a duplicate saver lookup.
        """

        if call_saver:
            get_checkpoint_saver(self.config.workspace_path)
        save_resume_metadata(
            state.run_uuid,
            self.config.workspace_path,
            route_decision=route_decision,
            history_assistant_turns=state.history_assistant_turns,
        )
        saved_checkpoint = save_initial_checkpoint(
            state.run_uuid,
            self.config.workspace_path,
            state.initial_messages,
        )
        state.checkpoint_ready = True
        state.initial_checkpoint_seeded = True
        trajectory = getattr(state, "trajectory", None)
        if trajectory is not None:
            checkpoint_id = (
                (saved_checkpoint or {})
                .get("configurable", {})
                .get("checkpoint_id")
            )
            trajectory.record(
                "checkpoint.saved",
                {"checkpoint_step": -1},
                checkpoint_id=checkpoint_id,
            )
        state.notify_checkpoint_ready()

    def _prepare_planned_checkpoint(self, state):
        """Seed durable state for a legacy planned run."""

        if state.resume_state is not None or not checkpoint_enabled():
            return
        try:
            self._seed_run_checkpoint(
                state,
                route_decision=_PLANNED_CODE_ANALYSIS_ROUTE,
            )
        except Exception:
            LOGGER.exception(
                "Failed to enable planned code analysis checkpoint"
            )

    def _prepare_runtime(
        self,
        command,
        emit_progress=None,
        emit_output=None,
        on_activity=None,
        cancel_event=None,
        wrapup_event=None,
        on_checkpoint_ready=None,
    ):
        """Prepare callbacks, resources, model, and tools for one run."""

        started_at = utc_now()
        question = command.get("question", "")
        run_uuid = str(command.get("run_uuid") or "")
        resume_state = None
        if command.get("resume"):
            resume_state = load_resume_state(
                run_uuid,
                self.config.workspace_path,
            )
        trajectory = command.get("_trajectory")
        if resume_state is not None and trajectory is not None:
            trajectory.merge_resume_state(
                {
                    "trace_schema_version": 1,
                    "last_trace_seq": resume_state.last_trace_seq,
                    "current_attempt": resume_state.current_attempt,
                    "open_call_ids": list(resume_state.open_call_ids),
                    "open_span_ids": list(resume_state.open_span_ids),
                    "parent_call_map": resume_state.parent_call_map,
                }
            )
            trajectory.record(
                "checkpoint.restored",
                {"checkpoint_step": resume_state.checkpoint_step},
                checkpoint_id=resume_state.checkpoint_id,
            )
            trajectory.interrupt_open_calls("checkpoint_resume")
        scenario = _scenario_for_task(command.get("task"))
        runtime_mode = runtime_mode_for(command)
        model_ref = command.get("agent_model_ref")
        if not model_ref:
            raise ValueError("agent_model_ref is required for Deep Agents")
        trace_context = command.get("trace_context")
        if not isinstance(trace_context, dict):
            trace_context = {}
        root_observation_id = trace_context.get("root_observation_id")
        if not isinstance(root_observation_id, str) or not re.fullmatch(
            r"[0-9a-f]{32}",
            root_observation_id,
        ):
            trace_context = {}
            root_observation_id = None

        state = SimpleNamespace(
            started_at=started_at,
            question=question,
            run_uuid=run_uuid,
            resume_state=resume_state,
            scenario=scenario,
            runtime_mode=runtime_mode,
            model_ref=model_ref,
            trace_context=trace_context,
            root_observation_id=root_observation_id,
            command=command,
            emit_progress=emit_progress,
            emit_output=emit_output,
            on_activity=on_activity,
            cancel_event=cancel_event,
            wrapup_event=wrapup_event,
            on_checkpoint_ready=on_checkpoint_ready,
            config=self.config,
            http_client=self.http_client,
            trajectory=trajectory,
        )

        def emit_agent_event(event, detail=None):
            detail = runtime_mode.decorate_event(detail)
            message = task_log(event, details=_detail_lines(detail))
            LOGGER.info(message)
            if emit_progress is not None:
                emit_progress(
                    message,
                    {
                        "agent_event": event,
                        "activity": _activity_from_event(event),
                        **detail,
                    },
                )

        def emit_user_event(event_type, payload):
            emit_agent_event(
                f"workflow.{event_type}",
                {
                    "event_type": event_type,
                    "visibility": "user",
                    "payload": payload,
                },
            )

        def emit_trace_observation(observation):
            if emit_progress is not None:
                emit_progress(
                    task_log("trace.observation"),
                    {"observation": observation},
                )

        state.emit_agent_event = emit_agent_event
        state.emit_user_event = emit_user_event
        state.emit_trace_observation = emit_trace_observation
        emit_agent_event(
            "deepagents.runtime.start",
            {
                "scenario": scenario["title"],
                "question_chars": len(question),
                "target_dirs": len(command.get("target_dirs") or []),
                "history_turns": len(command.get("history") or []),
            },
        )
        _apply_offload_thresholds(self.config)
        emit_agent_event(
            "deepagents.offload.configured",
            {
                "tool_tokens": self.config.offload_tool_tokens,
                "human_tokens": self.config.offload_human_tokens,
            },
        )
        if runtime_mode.general_chat:
            emit_user_event("phase.changed", {"phase": "analyzing"})
        resources_started_at = time.monotonic()
        emit_agent_event(
            "deepagents.runtime.stage.start",
            {"stage": "resources"},
        )
        state.resources = prepare_runtime_resources(
            self.config,
            command,
            emit_event=emit_agent_event,
            cancel_event=cancel_event,
            on_activity=on_activity,
        )
        state.subagent_resources = []
        emit_agent_event(
            "deepagents.runtime.stage.done",
            {
                "stage": "resources",
                "duration_ms": int(
                    (time.monotonic() - resources_started_at) * 1000
                ),
            },
        )
        model_tools_started_at = time.monotonic()
        emit_agent_event(
            "deepagents.runtime.stage.start",
            {"stage": "model_tools"},
        )
        try:
            self._prepare_model_and_tools(state)
        except BaseException:
            if cancel_event is None or not cancel_event.is_set():
                cleanup_runtime_resources(state.resources)
            raise
        emit_agent_event(
            "deepagents.runtime.stage.done",
            {
                "stage": "model_tools",
                "duration_ms": int(
                    (time.monotonic() - model_tools_started_at) * 1000
                ),
            },
        )
        return state

    def _prepare_model_and_tools(self, state):
        """Build the model, tools, middleware contributions, and MCP tools."""

        state.initial_messages = _build_initial_messages(
            state.command.get("history"),
            state.question,
            state.command.get("image_data_urls"),
        )
        state.history_assistant_turns = sum(
            1
            for item in state.command.get("history") or []
            if item.get("role") == "assistant"
        )
        state.runtime_evidence = dict(
            state.resume_state.runtime_evidence if state.resume_state else {}
        )
        state.capability_middleware = None
        state.checkpoint_ready = state.resume_state is not None
        state.initial_checkpoint_seeded = False
        state.resume_from_graph_checkpoint = state.resume_state is not None
        state.checkpoint_ready_notified = False

        def notify_checkpoint_ready():
            if (
                state.checkpoint_ready_notified
                or state.on_checkpoint_ready is None
            ):
                return
            state.on_checkpoint_ready()
            state.checkpoint_ready_notified = True

        state.notify_checkpoint_ready = notify_checkpoint_ready
        if state.checkpoint_ready:
            notify_checkpoint_ready()
        if (
            state.runtime_mode.execution_gates
            and state.resume_state is not None
            and state.resume_state.checkpoint_step < 0
        ):
            state.initial_checkpoint_seeded = True
            state.resume_from_graph_checkpoint = False

        def persist_execution_state(
            capability_state=None,
            guardrail_state=None,
        ):
            if not state.checkpoint_ready:
                return
            if capability_state is None and state.capability_middleware:
                capability_state = state.capability_middleware.export_state()
            save_runtime_state(
                state.run_uuid,
                self.config.workspace_path,
                capability_state=capability_state or {},
                runtime_evidence=state.runtime_evidence,
                guardrail_state=(
                    guardrail_state
                    if guardrail_state is not None
                    else state.model.export_runtime_state()
                ),
                trace_state=(
                    state.trajectory.snapshot()
                    if state.trajectory is not None
                    else None
                ),
            )

        state.persist_execution_state = persist_execution_state
        if state.trajectory is not None:
            state.trajectory.persist_state = lambda trace_state: (
                save_runtime_state(
                    state.run_uuid,
                    self.config.workspace_path,
                    trace_state=trace_state,
                )
                if state.checkpoint_ready
                else None
            )
        state.token_budget = _resolve_token_budget(
            self.config,
            state.command,
        )
        state.tool_call_budget = _resolve_tool_call_budget(
            self.config,
            state.command,
        )
        budget_gates_enabled = (
            state.runtime_mode.execution_gates
            or bool(state.command.get("parent_run_uuid"))
        )
        state.token_budget_wrapup_event = (
            threading.Event()
            if budget_gates_enabled
            else None
        )

        raw_emit_output = state.emit_output

        def emit_user_output(content, reset=False):
            """Apply the user-facing disclosure backstop to live output."""

            if raw_emit_output is None:
                return
            if reset or not content:
                raw_emit_output(content, reset=reset)
                return
            raw_emit_output(
                _sanitize_user_facing_answer(content),
                reset=reset,
            )

        state.emit_output = emit_user_output if raw_emit_output else None
        state.model = LensGatewayChatModel(
            model_ref=str(state.model_ref),
            ai_gateway_url=self.config.ai_gateway_url,
            token=self.config.token,
            request_timeout_s=self.config.request_timeout_s,
            tls_skip_verify=getattr(
                self.config,
                "tls_skip_verify",
                False,
            ),
            tls_ca_file=getattr(self.config, "tls_ca_file", None),
            http_client=self.http_client,
            emit_output=state.emit_output,
            on_activity=state.on_activity,
            cancel_event=state.cancel_event,
            run_uuid=state.run_uuid,
            trace_context=state.trace_context,
            emit_observation=state.emit_trace_observation,
            observation_name="agent",
            general_chat_execution_gates=budget_gates_enabled,
            token_budget_max_tokens=state.token_budget["max_tokens"],
            token_budget_final_reserve_tokens=state.token_budget[
                "final_reserve_tokens"
            ],
            token_budget_warn_ratio=getattr(
                self.config,
                "token_budget_warn_ratio",
                0.8,
            ),
            token_budget_wrapup_event=state.token_budget_wrapup_event,
            reasoning_effort=getattr(self.config, "reasoning_effort", None),
            on_runtime_state_change=lambda runtime_state: (
                state.persist_execution_state(
                    guardrail_state=runtime_state
                )
            ),
            trajectory=state.trajectory,
        )
        if state.resume_state is not None:
            state.model.restore_runtime_state(
                state.resume_state.messages,
                state.resume_state.guardrail_state,
            )
        if state.runtime_mode.general_chat:
            state.tools = build_general_chat_tools(
                state.command,
                state.resources,
                self.config,
                emit_event=state.emit_agent_event,
                runtime_evidence=state.runtime_evidence,
                on_runtime_evidence=lambda _state: (
                    state.persist_execution_state()
                ),
                http_client=self.http_client,
            )
        else:
            state.tools = build_agent_tools(
                state.command,
                state.resources,
                self.config,
                emit_event=state.emit_agent_event,
            )
        state.plugin_tools = build_plugin_tools(
            state.command,
            self.config,
            self.http_client,
            emit_event=state.emit_agent_event,
            plugin_http_pool=self.plugin_http_pool,
        )
        state.tools.extend(state.plugin_tools)
        state.mcp_tools = load_mcp_tools(
            state.resources.mcp_configs,
            discovery_timeout_s=getattr(
                self.config,
                "mcp_discovery_timeout_s",
                30,
            ),
            tool_timeout_s=getattr(
                self.config,
                "mcp_tool_timeout_s",
                60,
            ),
            emit_event=state.emit_agent_event,
            stdio_allowlist=getattr(
                self.config,
                "mcp_stdio_allowlist",
                (),
            ),
        )
        runtime_contributions = collect_agent_runtime_contributions(
            self.config,
            state.command,
            state.mcp_tools,
        )
        state.runtime_middleware = tuple(
            item
            for contribution in runtime_contributions
            for item in contribution.middleware
        )
        state.subagent_middleware = tuple(
            item
            for contribution in runtime_contributions
            for item in contribution.subagent_middleware
        )
        state.runtime_guidance = tuple(
            contribution.prompt_guidance
            for contribution in runtime_contributions
            if contribution.prompt_guidance
        )
        always_visible_tool_prefixes = tuple(
            prefix
            for contribution in runtime_contributions
            for prefix in contribution.always_visible_tool_prefixes
        )
        state.registered_mcp_tools, state.mcp_middleware = (
            build_deferred_mcp_tools(
                state.mcp_tools,
                threshold=getattr(
                    self.config,
                    "mcp_defer_threshold",
                    12,
                ),
                always_visible_prefixes=always_visible_tool_prefixes,
            )
        )
        state.tools.extend(state.registered_mcp_tools)
        if state.resume_state is not None:
            _reject_unsafe_resume_tool_replay(
                state.resume_state.messages,
                [*state.tools, *state.mcp_tools],
                state.resume_state.pending_write_tool_call_ids,
            )
        state.evidence_requirement = "none"
        state.required_capabilities = []
        state.route_decision = None

    def _route_runtime(self, state):
        """Select and handle the general-chat execution route."""

        if self._is_smart_collaboration(state.command):
            state.route_decision = {"route": "plan_execute"}
            return None
        if not state.runtime_mode.execution_gates:
            return None

        routing_started_at = time.monotonic()
        state.emit_agent_event(
            "deepagents.runtime.stage.start",
            {"stage": "routing"},
        )
        route_was_resumed = bool(
            state.resume_state is not None
            and state.resume_state.route_decision.get("route")
        )
        if route_was_resumed:
            state.route_decision = state.resume_state.route_decision
        else:
            if state.resume_state is None and checkpoint_enabled():
                try:
                    self._seed_run_checkpoint(state)
                except Exception:
                    LOGGER.exception("Failed to enable route checkpoint")
            state.route_decision = _high_confidence_report_route(
                state.command
            ) or _select_general_chat_route(
                state.model,
                state.question,
                history=state.command.get("history"),
                history_artifacts=state.command.get(
                    "history_artifact_paths"
                ),
                context_skill_contents=(
                    state.resources.context_skill_contents
                ),
                available_tools=[*state.tools, *state.mcp_tools],
                has_bound_skills=bool(state.resources.skill_paths),
                image_data_urls=state.command.get("image_data_urls"),
            )
            if state.checkpoint_ready:
                save_resume_metadata(
                    state.run_uuid,
                    self.config.workspace_path,
                    route_decision=state.route_decision,
                    history_assistant_turns=state.history_assistant_turns,
                )
                state.persist_execution_state()
        state.emit_agent_event(
            "deepagents.runtime.stage.done",
            {
                "stage": "routing",
                "duration_ms": int(
                    (time.monotonic() - routing_started_at) * 1000
                ),
            },
        )
        state.command = {
            **state.command,
            "runtime_route": state.route_decision["route"],
        }
        state_tools = getattr(state, "tools", [])
        original_tool_count = len(state_tools)
        state.tools = _report_scoped_tools(state.command, state_tools)
        state.plugin_tools = _report_scoped_tools(
            state.command,
            getattr(state, "plugin_tools", []),
        )
        removed_tool_count = original_tool_count - len(state.tools)
        if removed_tool_count:
            state.emit_agent_event(
                "deepagents.report.tools_scoped",
                {
                    "removed_tool_count": removed_tool_count,
                    "remaining_tool_count": len(state.tools),
                },
            )
        state.emit_user_event(
            "route.resumed" if route_was_resumed else "route.selected",
            state.route_decision,
        )
        state.evidence_requirement = state.route_decision[
            "evidence_requirement"
        ]
        state.required_capabilities = state.route_decision[
            "required_capabilities"
        ]
        if state.route_decision["route"] == "capability_unavailable":
            required = state.route_decision["required_capabilities"]
            capability = next(
                (
                    item
                    for item in required
                    if item in CAPABILITY_FAMILIES or item == "tool"
                ),
                "tool",
            )
            termination_detail = _capability_termination_detail(capability)
            state.emit_user_event(
                "capability.blocked",
                termination_detail,
            )
            state.emit_user_event(
                "phase.changed",
                {"phase": "completed"},
            )
            return {
                "answer": _unverified_execution_answer(
                    state.question,
                    termination_detail,
                    answer_language=_command_answer_language(
                        state.command
                    ),
                ),
                "samples": [],
                "stop_reason": state.model.stop_reason,
                "token_usage": state.model.token_usage,
                "outcome": "blocked",
                "termination_detail": termination_detail,
            }
        if state.route_decision["route"] == "direct_answer":
            state.emit_user_event(
                "phase.changed",
                {"phase": "answering"},
            )
            if (
                state.resume_state is not None
                and state.emit_output is not None
            ):
                state.emit_output("", reset=True)
            state.runtime_mode.emit_model_round(
                state.emit_agent_event,
                "start",
                1,
            )
            original_emit_output = getattr(state.model, "emit_output", None)
            state.model.emit_output = None
            try:
                answer = _answer_general_chat_directly(
                    state.model,
                    state.command,
                    _system_prompt(
                        state.scenario,
                        state.command,
                        state.resources.context_skill_contents,
                        workspace_guide=state.command.get(
                            "workspace_guide", ""
                        ),
                    ),
                    messages=(
                        state.resume_state.messages
                        if state.resume_state is not None
                        else None
                    ),
                    emit_event=state.emit_agent_event,
                    emit_output=None,
                )
            except Exception:
                state.runtime_mode.emit_model_round(
                    state.emit_agent_event,
                    "failed",
                    1,
                )
                raise
            finally:
                state.model.emit_output = original_emit_output
            state.runtime_mode.emit_model_round(
                state.emit_agent_event,
                "done",
                1,
            )
            if not answer.strip():
                raise EmptyAgentResponseError(
                    "Direct route returned no answer."
                )
            state.emit_user_event(
                "phase.changed",
                {"phase": "completed"},
            )
            answer = _normalize_code_analysis_paths(answer, state.command)
            if state.emit_output is not None:
                state.emit_output(answer)
            return {
                "answer": answer,
                "samples": [],
                "stop_reason": state.model.stop_reason,
                "token_usage": state.model.token_usage,
                "outcome": "completed",
                "termination_detail": {},
            }
        state.capability_middleware = CapabilityBoundaryMiddleware(
            emit_event=state.emit_agent_event,
            required_capabilities=state.required_capabilities,
            require_initial_plan=(
                state.route_decision["route"] == "plan_execute"
            ),
            planning_reasoning_effort=getattr(
                self.config,
                "planning_reasoning_effort",
                "none",
            ),
            on_state_change=state.persist_execution_state,
            max_tool_calls=getattr(
                state,
                "tool_call_budget",
                _resolve_tool_call_budget(self.config, state.command),
            ),
        )
        if state.resume_state is not None:
            state.capability_middleware.restore_state(
                state.resume_state.capability_state
            )
        phase = (
            "planning"
            if state.route_decision["route"] == "plan_execute"
            else "executing"
        )
        state.emit_user_event("phase.changed", {"phase": phase})
        return None

    def _build_agent(self, state):
        """Build the Deep Agents graph and configure its checkpoint."""

        state.trace_middleware = (
            TraceObservationMiddleware(
                state.emit_trace_observation,
                state.root_observation_id,
                state.trajectory,
            )
            if state.root_observation_id or state.trajectory is not None
            else None
        )
        use_subagents = self._subagents_enabled(state)
        dynamic_subagents = self._build_configured_subagents(state)
        backend = _build_execution_backend(
            self.config,
            state.resources.root,
        )
        state.kwargs = {
            "model": state.model,
            "tools": state.tools,
            "system_prompt": _system_prompt(
                state.scenario,
                state.command,
                state.resources.context_skill_contents,
                workspace_guide=state.command.get(
                    "workspace_guide", ""
                ),
                mcp_deferred=state.mcp_middleware is not None,
                runtime_guidance=state.runtime_guidance,
            ),
            "backend": backend,
            "subagents": (
                dynamic_subagents
                if dynamic_subagents
                else
                [
                    _fast_subagent(
                        state.mcp_middleware,
                        state.trace_middleware,
                        state.subagent_middleware,
                    )
                ]
                if use_subagents
                and not self._is_smart_collaboration(state.command)
                else []
            ),
            "name": f"lensnode-{state.command.get('task') or 'agent'}",
        }
        if state.resources.skill_paths and use_subagents:
            state.kwargs["skills"] = state.resources.skill_paths

        state.summarizer = _build_summarization_middleware(
            self.config,
            state.model_ref,
            state.emit_agent_event,
            state.cancel_event,
            run_uuid=state.run_uuid,
            http_client=self.http_client,
            trace_context=state.trace_context,
            emit_observation=state.emit_trace_observation,
            trajectory=state.trajectory,
        )
        middleware = _agent_middleware(
            state.command,
            state.summarizer,
            state.emit_agent_event,
            capability_middleware=state.capability_middleware,
            mcp_middleware=state.mcp_middleware,
            trace_middleware=state.trace_middleware,
            runtime_middleware=state.runtime_middleware,
            allow_task_tool=use_subagents,
        )
        if middleware:
            state.kwargs["middleware"] = middleware
        if state.summarizer is not None:
            state.emit_agent_event(
                "deepagents.summarization.enabled",
                {
                    "trigger_tokens": self.config.summary_trigger_tokens,
                    "keep_tokens": self.config.summary_keep_tokens,
                },
            )

        state.emit_agent_event(
            "deepagents.agent.create",
            {
                "tool_count": len(state.tools),
                "skill_count": len(state.resources.skill_paths),
                "mcp_tool_count": len(state.mcp_tools),
                "plugin_tool_count": len(state.plugin_tools),
                "mcp_deferred": state.mcp_middleware is not None,
                "task_tool_enabled": use_subagents,
                "mcp_config_path": str(state.resources.mcp_config_path),
            },
        )
        state.checkpoint_thread = thread_config(state.run_uuid)
        if checkpoint_enabled():
            if state.resume_state is not None:
                state.kwargs["checkpointer"] = get_checkpoint_saver(
                    self.config.workspace_path
                )
                state.checkpoint_ready = True
            elif state.checkpoint_ready:
                state.kwargs["checkpointer"] = get_checkpoint_saver(
                    self.config.workspace_path
                )
            else:
                try:
                    state.kwargs["checkpointer"] = get_checkpoint_saver(
                        self.config.workspace_path
                    )
                    self._seed_run_checkpoint(
                        state,
                        route_decision=(
                            state.route_decision
                            if state.runtime_mode.execution_gates
                            else {}
                        ),
                        call_saver=False,
                    )
                except Exception:
                    state.kwargs.pop("checkpointer", None)
                    LOGGER.exception("Failed to enable agent run checkpoints")
        state.agent = create_deep_agent(**state.kwargs)
        state.max_turns = _resolve_agent_turn_limit(state.command)

    def _build_configured_subagents(self, state):
        """Build control-plane subagents routed to their bound LensNodes."""

        subagents = []
        seen_names = set()
        state.subagent_display_names = {}
        explicit_assistant_uuids = {
            str(value)
            for value in (
                state.command.get("routing_assistant_uuids")
                or [state.command.get("routing_assistant_uuid")]
            )
            if value
        }
        for index, snapshot in enumerate(
            (state.command.get("subagents") or [])[:MAX_CONFIGURED_SUBAGENTS]
        ):
            if not isinstance(snapshot, dict):
                continue
            assistant_uuid = str(snapshot.get("uuid") or "")
            if not assistant_uuid:
                state.emit_agent_event(
                    "deepagents.subagent.skipped",
                    {"reason": "assistant_uuid_missing"},
                )
                continue
            raw_name = str(snapshot.get("name") or snapshot.get("uuid") or "")
            name = re.sub(r"[^a-zA-Z0-9_-]+", "-", raw_name).strip("-")
            name = (name or f"assistant-{index + 1}")[:80]
            if name in seen_names:
                name = f"{name[:70]}-{index + 1}"
            seen_names.add(name)
            state.subagent_display_names[name] = raw_name[:160]
            description = (
                str(snapshot.get("routing_description") or "")
                or str(snapshot.get("description") or "")
                or f"Delegate work to the {raw_name} assistant."
            )[:500]
            runnable = RemoteSubagentRunnable(
                assistant_uuid=assistant_uuid,
                parent_run_uuid=state.run_uuid,
                delegation_group_key=(
                    f"explicit:{assistant_uuid}"
                    if state.command.get("routing_assistant_explicit")
                    and assistant_uuid in explicit_assistant_uuids
                    else ""
                ),
                delegation_base_url=getattr(
                    self.config,
                    "delegation_base_url",
                    "",
                ),
                token=self.config.token,
                http_client=self.http_client,
                cancel_event=state.cancel_event,
                on_activity=state.on_activity,
                timeout_s=float(
                    state.command.get("remaining_run_timeout_s")
                    or state.command.get("run_timeout_s")
                    or 3600
                ),
            )
            subagents.append(
                {
                    "name": name,
                    "description": description,
                    "runnable": runnable,
                }
            )
        return subagents

    def _execute_agent(self, state):
        """Run the prepared Deep Agents graph and finalize its outcome."""

        invoke_detail = {}
        if state.max_turns and state.max_turns > 0:
            invoke_detail["max_agent_turns"] = state.max_turns
        if state.runtime_mode.execution_gates:
            invoke_detail.update(
                {
                    "token_budget_profile": state.token_budget["profile"],
                    "token_budget_max_tokens": state.token_budget[
                        "max_tokens"
                    ],
                    "token_budget_final_reserve_tokens": state.token_budget[
                        "final_reserve_tokens"
                    ],
                    "tool_budget_max_calls": state.tool_call_budget,
                }
            )
        state.emit_agent_event(
            "deepagents.agent.invoke",
            invoke_detail,
        )
        messages = state.initial_messages
        turn_baseline_ai = None
        event_baseline_ai = None
        if state.resume_state is not None:
            if state.emit_output is not None:
                state.emit_output("", reset=True)
            messages = list(state.resume_state.messages)
            turn_baseline_ai = state.resume_state.history_assistant_turns
            event_baseline_ai = sum(
                1
                for message in state.resume_state.messages
                if getattr(message, "type", "") == "ai"
            )
            state.emit_agent_event(
                "deepagents.runtime.resume",
                {
                    "checkpoint_ai_turns": event_baseline_ai,
                    "history_ai_turns": turn_baseline_ai,
                },
            )
        (
            answer,
            truncated,
            termination_reason,
        ) = _run_agent_with_turn_limit(
            state.agent,
            messages,
            state.max_turns,
            model=state.model,
            thread=state.checkpoint_thread,
            turn_baseline_ai=turn_baseline_ai,
            event_baseline_ai=event_baseline_ai,
            resume_from_checkpoint=state.resume_from_graph_checkpoint,
            emit_event=state.emit_agent_event,
            answer_language=_command_answer_language(state.command),
            cancel_event=state.cancel_event,
            wrapup_event=(
                state.wrapup_event
                if state.runtime_mode.execution_gates
                else None
            ),
            token_budget_wrapup_event=state.token_budget_wrapup_event,
            on_checkpoint_state=(
                state.persist_execution_state
                if state.checkpoint_ready
                else None
            ),
            input_checkpoint_seeded=state.initial_checkpoint_seeded,
            stream_recovery_attempts=(
                int(
                    getattr(
                        self.config,
                        "stream_recovery_attempts",
                        1,
                    )
                )
                if state.checkpoint_ready
                else 0
            ),
            stream_recovery_backoff_s=float(
                getattr(self.config, "stream_recovery_backoff_s", 1.0)
            ),
            stream_recovery_backoff_max_s=float(
                getattr(
                    self.config,
                    "stream_recovery_backoff_max_s",
                    8.0,
                )
            ),
            on_stream_recovery=(
                (lambda: state.emit_output("", reset=True))
                if state.emit_output is not None
                else None
            ),
            subagent_display_names=getattr(
                state,
                "subagent_display_names",
                None,
            ),
        )
        if truncated:
            detail = {}
            if state.max_turns and state.max_turns > 0:
                detail["max_agent_turns"] = state.max_turns
            state.emit_agent_event("deepagents.agent.truncated", detail)
        state.emit_agent_event(
            "deepagents.runtime.done",
            {
                "actual_duration": elapsed_since(state.started_at),
                "answer_chars": len(answer),
                "stop_reason": state.model.stop_reason,
                "termination_reason": termination_reason,
                "token_usage": state.model.token_usage,
            },
        )
        outcome, termination_detail = _finalize_runtime_outcome(
            capability_middleware=state.capability_middleware,
            evidence_requirement=state.evidence_requirement,
            required_capabilities=state.required_capabilities,
            truncated=truncated or bool(termination_reason),
            stop_reason=termination_reason or state.model.stop_reason,
            execution_gate_enabled=state.runtime_mode.execution_gates,
            runtime_evidence=state.runtime_evidence,
        )
        if state.capability_middleware is not None:
            state.emit_agent_event(
                "deepagents.runtime.outcome",
                {
                    "outcome": outcome,
                    **state.capability_middleware.failure_diagnostics(
                        state.required_capabilities,
                        outcome,
                    ),
                },
            )
        if (
            outcome == "blocked"
            and state.capability_middleware is not None
        ):
            reason = termination_detail.get("reason")
            if reason == "execution_failed":
                state.emit_user_event(
                    "execution.failed",
                    termination_detail,
                )
            elif reason == "evidence_unavailable":
                state.emit_user_event(
                    "verification.failed",
                    termination_detail,
                )
            answer = _unverified_execution_answer(
                state.question,
                termination_detail,
                answer_language=_command_answer_language(
                    state.command
                ),
            )
        if state.runtime_mode.general_chat:
            state.emit_user_event(
                "phase.changed",
                {"phase": "completed"},
            )
        return {
            "answer": _normalize_code_analysis_paths(answer, state.command),
            "samples": [],
            "stop_reason": state.model.stop_reason,
            "token_usage": state.model.token_usage,
            "outcome": outcome,
            "termination_detail": termination_detail,
        }


def _scenario_for_task(task):
    """Return scenario metadata for a LensNode task name."""

    return SCENARIOS.get(task or "", SCENARIOS["knowledge_qa"])


def _normalize_code_analysis_paths(answer, command):
    """Present code paths relative to the selected resource directories."""

    normalized = str(answer or "")
    if command.get("task") != "general_chat":
        roots = sorted(
            {
                Path(item.get("path", "")).resolve().as_posix().rstrip("/")
                for item in command.get("target_dirs") or []
                if item.get("path")
            },
            key=len,
            reverse=True,
        )
        workspace_path = command.get("workspace_path")
        if workspace_path:
            roots.append(
                Path(workspace_path).resolve().as_posix().rstrip("/")
            )
        roots = sorted(set(roots or ["/workspace"]), key=len, reverse=True)
        for root in roots:
            normalized = re.sub(
                rf"(?<![A-Za-z0-9_.-]){re.escape(root)}/",
                "",
                normalized,
            )
            normalized = re.sub(
                rf"(?<![A-Za-z0-9_.-]){re.escape(root)}"
                r"(?=$|[\s\"'`.,:;!?])",
                ".",
                normalized,
            )
    return _sanitize_user_facing_answer(normalized)


def _sanitize_user_facing_answer(answer):
    """Remove runtime implementation details from every user-facing answer."""

    def replace_subject_path(match):
        filename = match.group("filename").removesuffix(".sourcelens")
        return re.sub(
            r"^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}-",
            "",
            filename,
            flags=re.IGNORECASE,
        )

    sanitized = re.sub(
        r"/subject-documents/(?P<filename>[^/\s`]+?)"
        r"\.sourcelens/content\.md",
        replace_subject_path,
        answer,
    )
    sanitized = re.sub(
        r"/subject-documents/(?P<filename>[^/\s`]+)",
        replace_subject_path,
        sanitized,
    )
    sanitized = sanitized.replace(
        "/subject-documents",
        "the uploaded documents",
    )
    sanitized = re.sub(
        r"/(?:mcp|skills|large_tool_results|conversation-artifacts)"
        r"(?:/[^\s`'\")\]}>,;:]*)?",
        "internal runtime material",
        sanitized,
    )
    return re.sub(
        r"\b(?:ls|read_file|write_file|search_workspace|"
        r"find_files|read_workspace_file)\b",
        "document tools",
        sanitized,
    )


def _planned_reasoning_pulse(
    emit_agent_event,
    *,
    throttle_s=3.0,
):
    """Return a reasoning callback that throttles lightweight phase pulses.

    Reasoning tokens prove the planner is still working without leaking
    the chain of thought. The callback re-emits `phase.changed: planning`
    at most once every throttle_s so the frontend sees continued liveness
    (elapsed timer keeps advancing) instead of a frozen "planning" state.
    """

    last_pulse = [0.0]

    def on_reasoning_delta(_text):
        now = time.monotonic()
        if now - last_pulse[0] < throttle_s:
            return
        last_pulse[0] = now
        emit_agent_event(
            "workflow.phase.changed",
            {
                "event_type": "phase.changed",
                "visibility": "user",
                "payload": {"phase": "planning"},
            },
        )

    return on_reasoning_delta


def _run_planned_code_analysis(
    *,
    model,
    command,
    tools,
    mcp_tools,
    emit_agent_event,
    workspace_root,
    context_skill_contents=None,
    trajectory=None,
    planner_repair_enabled=False,
):
    """Run Code Analysis through one plan and one compact evidence bundle."""

    question = str(command.get("question") or "")
    workspace_adapters = _planned_workspace_adapters(tools)
    codegraph_adapters = _planned_codegraph_adapters(mcp_tools)
    capability_assessment = assess_code_analysis_capabilities(
        workspace_adapters,
        codegraph_adapters,
    )
    if not capability_assessment["ready"]:
        termination_detail = {
            "reason": "capability_unavailable",
            "capability": "code_analysis_retrieval",
            "error_type": "capability",
            "missing_capabilities": list(capability_assessment["missing"]),
            "recovery": (
                "Bind workspace search and file reading or enable CodeGraph, "
                "then retry the analysis."
            ),
        }
        emit_agent_event(
            "planned_evidence.capability.blocked",
            termination_detail,
        )
        return {
            "answer": _pick_text(
                "当前助手没有可用的代码检索能力，无法安全分析该问题。请绑定"
                "工作区搜索/文件读取能力或启用 CodeGraph 后重试。",
                "This assistant has no available code-retrieval capability, "
                "so the question cannot be analyzed safely. Bind workspace "
                "search/file reading or enable CodeGraph, then retry.",
                _command_answer_language(command),
            ),
            "samples": [],
            "stop_reason": model.stop_reason,
            "token_usage": model.token_usage,
            "outcome": "blocked",
            "termination_detail": termination_detail,
            "planned_evidence": {
                "capability_status": "unavailable",
                "available_capabilities": list(
                    capability_assessment["available"]
                ),
                "missing_capabilities": list(
                    capability_assessment["missing"]
                ),
            },
            "citations": [],
        }
    planner_prompt = _planned_planner_prompt(
        command,
        context_skill_contents=context_skill_contents,
        codegraph_operations=sorted(codegraph_adapters),
    )
    emit_agent_event(
        "workflow.phase.changed",
        {
            "event_type": "phase.changed",
            "visibility": "user",
            "payload": {"phase": "planning"},
        },
    )
    pulse_callback = _planned_reasoning_pulse(emit_agent_event)
    planner_response = model.invoke(
        [
            SystemMessage(content=planner_prompt),
            HumanMessage(content=question),
        ],
        runtime_structured_output=True,
        on_reasoning_delta=pulse_callback,
    )
    planner_status = "valid"
    planner_retry_count = 0
    planner_rejection_reason = ""
    try:
        plan = parse_retrieval_plan(_message_content(planner_response))
    except Exception as exc:
        planner_rejection_reason = str(exc)
        if planner_repair_enabled:
            planner_retry_count = 1
            repair_response = model.invoke(
                [
                    SystemMessage(
                        content=_planned_planner_repair_prompt(
                            validation_error=str(exc),
                            codegraph_operations=sorted(codegraph_adapters),
                        )
                    ),
                    HumanMessage(
                        content=json.dumps(
                            {
                                "question": question,
                                "invalid_plan": _message_content(
                                    planner_response
                                )[:4000],
                            },
                            ensure_ascii=False,
                        )
                    ),
                ],
                runtime_structured_output=True,
                on_reasoning_delta=pulse_callback,
            )
            try:
                plan = parse_retrieval_plan(
                    _message_content(repair_response)
                )
                planner_status = "repaired"
            except Exception as repair_exc:
                planner_rejection_reason = (
                    f"{planner_rejection_reason} | {repair_exc}"
                )
                plan = _parse_planner_response(
                    repair_response,
                    question,
                    command,
                )
                planner_status = "fallback"
        else:
            plan = _parse_planner_response(
                planner_response,
                question,
                command,
            )
            planner_status = "fallback"
    emit_agent_event(
        "planned_evidence.plan.ready",
        {
            "question_type": plan.question_type,
            "codegraph_queries": len(plan.codegraph_queries),
            "literal_queries": len(plan.literal_queries),
            "source_windows": len(plan.source_windows),
            "max_files": plan.max_files,
        },
    )
    if plan.clarification is not None:
        request = {
            "request_id": f"clarification-{uuid.uuid4().hex}",
            **plan.clarification.as_dict(),
        }
        termination_detail = {
            "reason": "needs_user_input",
            "request": request,
        }
        emit_agent_event(
            "planned_evidence.user_input.required",
            {"reason": plan.clarification.reason},
        )
        return {
            "answer": request["question"],
            "samples": [],
            "stop_reason": model.stop_reason,
            "token_usage": model.token_usage,
            "status": "awaiting_user_input",
            "outcome": "blocked",
            "termination_detail": termination_detail,
            "planned_evidence": {
                "clarification_status": "awaiting_user_input",
                "model_call_count": 1 + planner_retry_count,
                "planner_status": planner_status,
                "planner_retry_count": planner_retry_count,
                "planner_rejection_reason": planner_rejection_reason,
            },
            "citations": [],
        }

    executor = EvidenceExecutor(
        workspace_tools=workspace_adapters,
        codegraph_tools=codegraph_adapters,
        trajectory=trajectory,
    )
    bundle = executor.execute(plan)
    sufficiency = validate_evidence_sufficiency(
        bundle,
        plan.evidence_requirements,
    )
    fallback_used = False
    if not sufficiency.sufficient and plan.max_fallback_rounds:
        fallback_response = model.invoke(
            [
                SystemMessage(content=_planned_fallback_prompt()),
                HumanMessage(
                    content=json.dumps(
                        {
                            "objective": plan.objective,
                            "gaps": list(sufficiency.gaps),
                        },
                        ensure_ascii=False,
                    )
                ),
            ],
            runtime_structured_output=True,
            on_reasoning_delta=pulse_callback,
        )
        fallback_plan = _parse_planner_response(
            fallback_response,
            question,
            command,
            fallback=True,
        )
        fallback_bundle = executor.execute(fallback_plan)
        bundle = build_evidence_bundle(
            [
                item.as_dict()
                for item in (*bundle.items, *fallback_bundle.items)
            ],
            max_tokens=plan.budgets.max_evidence_tokens,
        )
        fallback_used = True
        sufficiency = validate_evidence_sufficiency(
            bundle,
            plan.evidence_requirements,
        )
    bundle.metrics["fallback_rounds"] = int(fallback_used)
    bundle.metrics["model_call_count"] = (
        2 + planner_retry_count + int(fallback_used)
    )
    bundle.metrics["evidence_gap_count"] = len(sufficiency.gaps)
    bundle.metrics["plan_version"] = "planned-evidence-v1"
    bundle.metrics["capability_status"] = "ready"
    bundle.metrics["available_capabilities"] = list(
        capability_assessment["available"]
    )
    bundle.metrics["planner_status"] = planner_status
    bundle.metrics["planner_retry_count"] = planner_retry_count
    bundle.metrics["planner_rejection_reason"] = planner_rejection_reason
    bundle.metrics["planned_operation_count"] = (
        len(plan.codegraph_queries) + len(plan.literal_queries)
    )
    bundle.metrics["planned_codegraph_operations"] = sorted(
        {query.operation for query in plan.codegraph_queries}
    )
    citation_context = {
        "project": "workspace",
        "repository": "workspace",
        "revision": "working-tree",
    }
    final_input = json.dumps(
        {
            "question": question,
            **citation_context,
            "evidence": bundle.as_dict(),
            "evidence_gaps": list(sufficiency.gaps),
        },
        ensure_ascii=False,
    )
    final_response = _invoke_planned_synthesis(
        model,
        final_input,
        context_skill_contents=context_skill_contents,
    )
    final_retry_count = 0
    if _planned_response_needs_retry(final_response):
        final_response = _invoke_planned_synthesis(
            model,
            final_input,
            compact=True,
            context_skill_contents=context_skill_contents,
        )
        final_retry_count = 1
    bundle.metrics["final_retry_count"] = final_retry_count
    bundle.metrics["model_call_count"] += final_retry_count
    answer, citations, unsupported_claim_count = _validated_planned_answer(
        final_response,
        bundle,
        workspace_root,
        citation_context=citation_context,
    )
    bundle.metrics["citation_count"] = len(citations)
    bundle.metrics["unsupported_claim_count"] = unsupported_claim_count
    claim_count = len(citations) + unsupported_claim_count
    bundle.metrics["citation_coverage_ratio"] = (
        round(len(citations) / claim_count, 4) if claim_count else 0.0
    )
    bundle.metrics["sufficient"] = sufficiency.sufficient
    bundle.metrics["gap_categories"] = list(sufficiency.gaps)
    emit_agent_event(
        "planned_evidence.metrics",
        {
            **bundle.metrics,
            "sufficient": sufficiency.sufficient,
            "gap_categories": list(sufficiency.gaps),
        },
    )
    outcome = "completed"
    termination_detail = {}
    if not sufficiency.sufficient or unsupported_claim_count:
        if citations or (bundle.items and not unsupported_claim_count):
            outcome = "partial" if not sufficiency.sufficient else "completed"
        else:
            outcome = "blocked"
        termination_detail = {"reason": "evidence_insufficient"}
    if outcome == "blocked":
        answer = _pick_text(
            (
                "当前代码证据不足，暂时无法给出"
                "可靠结论。\n\n"
                "我已按当前范围检索，但还没有找到能"
                "将问题与具体代码路径关联起来的"
                "可靠证据。\n"
                "你可以补充任一线索："
                "文件路径、类或函数名、"
                "完整错误堆栈，或涉及的业务调用链。\n"
                "如果只想了解异常概念，请回复"
                "“只解释概念”。"
            ),
            (
                "The available code evidence is insufficient for a "
                "reliable conclusion.\n\n"
                "I searched the current scope but could not find reliable "
                "evidence linking the issue to a specific code path.\n"
                "Please provide any of these clues: a file path, class or "
                "function name, full traceback, or the relevant business "
                "call chain.\n"
                'If you only want a conceptual explanation, reply "explain '
                'the concept only".'
            ),
            _command_answer_language(command),
        )
    return {
        "answer": _normalize_code_analysis_paths(answer, command),
        "samples": [],
        "stop_reason": model.stop_reason,
        "token_usage": model.token_usage,
        "outcome": outcome,
        "termination_detail": termination_detail,
        "planned_evidence": bundle.metrics,
        "citations": list(citations),
    }


def _planned_workspace_adapters(tools):
    """Expose only deterministic workspace tools to the evidence executor."""

    return {
        tool.name: tool
        for tool in tools
        if getattr(tool, "name", "")
        in {"search_workspace", "read_workspace_file"}
    }


def _planned_codegraph_adapters(tools):
    """Map CodeGraph MCP tool names to bounded plan operations."""

    adapters = {}
    for tool in tools:
        name = str(getattr(tool, "name", ""))
        if not name.startswith("mcp__codegraph__codegraph_"):
            continue
        operation = name.rsplit("_", 1)[-1]
        if operation not in {
            "callers",
            "callees",
            "context",
            "explore",
            "impact",
            "node",
            "search",
            "trace",
        }:
            continue
        adapters[operation] = tool
    return adapters


def _parse_planner_response(response, question, command, fallback=False):
    """Parse a planner response or fail closed to one bounded safe plan."""

    content = _message_content(response)
    try:
        return parse_retrieval_plan(content)
    except Exception:
        query_terms = _fallback_query_terms(question)
        return parse_retrieval_plan(
            {
                "objective": question[:500] or "analyze the workspace",
                "project": command.get("project") or "workspace",
                "repository": command.get("repository") or "workspace",
                "revision": command.get("revision") or "workspace",
                "question_type": "mixed",
                "evidence_requirements": [
                    "source lines",
                ],
                "codegraph_queries": [
                    {"operation": "explore", "query": query_terms[0]}
                ],
                "literal_queries": list(query_terms[:2]),
                "max_files": 8,
                "max_fallback_rounds": 0,
            }
        )


def _fallback_query_terms(question):
    """Extract a few bounded search terms from an unstructured question."""

    text = re.sub(r"\s+", " ", str(question or "")).strip()
    if not text:
        return ("workspace",)

    stop_phrases = (
        "描述一下",
        "分析一下",
        "解释一下",
        "介绍一下",
        "为什么",
        "是否",
        "需要",
        "请问",
        "如何",
        "怎么",
        "怎样",
        "哪些",
        "什么",
        "哪里",
        "这个",
        "那个",
        "当前",
        "先",
        "做",
        "会",
        "吗",
        "呢",
        "了",
        "的",
        "是",
        "在",
        "对",
    )
    stop_pattern = "|".join(map(re.escape, stop_phrases))
    stop_words = {
        "a",
        "an",
        "and",
        "are",
        "be",
        "can",
        "do",
        "does",
        "how",
        "is",
        "it",
        "of",
        "the",
        "this",
        "to",
        "what",
        "where",
        "which",
        "why",
    }
    terms = []
    chunks = re.findall(
        r"[\u4e00-\u9fff]+|[A-Za-z_][A-Za-z0-9_./:-]*",
        text,
    )
    for chunk in chunks:
        pieces = (
            re.split(stop_pattern, chunk)
            if re.search(r"[\u4e00-\u9fff]", chunk)
            else [chunk]
        )
        for piece in pieces:
            term = piece.strip(" _-./:")
            if len(term) < 2 or term.lower() in stop_words:
                continue
            if term not in terms:
                terms.append(term)
            if len(terms) >= 3:
                return tuple(terms)
    return tuple(terms) or ("workspace",)


def _validated_planned_answer(
    response,
    bundle,
    workspace_root,
    citation_context=None,
):
    """Return final text and only verified source citations."""

    content = _message_content(response)
    payload = _json_object(content)
    if payload is None or not _valid_planned_answer_payload(payload):
        if _looks_like_planned_answer_envelope(content):
            return (
                "The code analysis result could not be validated.",
                (),
                1,
            )
        return (content, (), 1)
    answer = str(payload.get("answer") or "").strip()
    if not answer:
        answer = "The code analysis result could not be validated."
    validated, invalid = validate_citations(
        payload.get("citations"),
        bundle,
        workspace_root,
        citation_context=citation_context,
    )
    valid = _select_presented_citations(validated)
    unsupported = payload.get("unsupported_claims") or []
    unsupported_count = len(unsupported) + len(invalid)
    if not valid and not unsupported_count and not bundle.items:
        unsupported_count = 1
    return answer, valid, unsupported_count


def _select_presented_citations(citations):
    """Keep a small, non-duplicated set of user-facing citations."""

    selected = []
    seen = set()
    for citation in citations:
        key = (
            citation.get("project"),
            citation.get("repository"),
            citation.get("revision"),
            citation.get("path"),
            citation.get("symbol"),
            citation.get("start_line"),
            citation.get("end_line"),
        )
        if key in seen:
            continue
        seen.add(key)
        selected.append(citation)
        if len(selected) >= _MAX_PRESENTED_CITATIONS:
            break
    return tuple(selected)


def _invoke_planned_synthesis(
    model,
    final_input,
    compact=False,
    context_skill_contents=None,
):
    """Generate one hidden structured response for final validation."""

    return model.invoke(
        [
            SystemMessage(
                content=_planned_final_prompt(
                    compact=compact,
                    context_skill_contents=context_skill_contents,
                )
            ),
            HumanMessage(content=final_input),
        ],
        runtime_final_synthesis=True,
        runtime_structured_output=True,
    )


def _planned_response_needs_retry(response):
    """Return whether one compact retry can recover a broken envelope."""

    metadata = getattr(response, "response_metadata", None) or {}
    if metadata.get("model_length_capped"):
        return True
    content = _message_content(response)
    payload = _json_object(content)
    if payload is not None:
        return not _valid_planned_answer_payload(payload)
    return _looks_like_planned_answer_envelope(content)


def _valid_planned_answer_payload(payload):
    """Return whether a final answer follows the complete JSON contract."""

    if not isinstance(payload, dict):
        return False
    required_fields = {"answer", "citations", "unsupported_claims"}
    if not required_fields.issubset(payload):
        return False
    if (
        not isinstance(payload["answer"], str)
        or not payload["answer"].strip()
    ):
        return False
    citations = payload["citations"]
    unsupported = payload["unsupported_claims"]
    return (
        isinstance(citations, list)
        and all(
            isinstance(item, dict)
            and str(item.get("evidence_id") or "").strip()
            and str(item.get("supports") or "").strip()
            for item in citations
        )
        and isinstance(unsupported, list)
        and all(isinstance(item, str) for item in unsupported)
    )


def _message_content(message):
    """Return model content as bounded plain text."""

    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            item.get("text", "")
            if isinstance(item, dict)
            else str(item)
            for item in content
        )
    return str(content or "")


def _json_object(content):
    """Extract one planned-answer JSON object from model text."""

    text = str(content or "").strip()
    decoder = json.JSONDecoder()
    starts = [0, *(match.start() for match in re.finditer(r"\{", text))]
    for start in dict.fromkeys(starts):
        try:
            value, _end = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        if not isinstance(value, dict):
            continue
        if {
            "answer",
            "citations",
            "unsupported_claims",
        }.intersection(value):
            return value
    return None


def _looks_like_planned_answer_envelope(content):
    """Detect a malformed internal final-answer protocol envelope."""

    text = str(content or "")
    keys = {
        key
        for key in ("answer", "citations", "unsupported_claims")
        if f'"{key}"' in text
    }
    return len(keys) >= 2


def _planned_planner_prompt(
    command,
    context_skill_contents=None,
    codegraph_operations=None,
):
    """Build the compact initial planner contract.

    Enumerates the allowed CodeGraph operations and shows concrete JSON so
    the model does not invent operation names that validation rejects. When
    only a subset is available (no dedicated adapter), the model is told
    which ones can actually run.
    """

    if codegraph_operations is None:
        operations = ALLOWED_CODEGRAPH_OPERATIONS
    else:
        operations = set(codegraph_operations)
    if operations:
        operation_text = ", ".join(
            f'"{name}"' for name in sorted(operations)
        )
        example_operation = (
            "search" if "search" in operations else sorted(operations)[0]
        )
        codegraph_contract = (
            "Each codegraph_queries item must be an object with an "
            f"operation from [{operation_text}] and a query or symbol; for "
            f'example {{"operation": "{example_operation}", "symbol": '
            '"VSSMode"}.'
        )
    else:
        codegraph_contract = (
            "CodeGraph is not available in this run; set codegraph_queries "
            "to []."
        )
    prompt = (
        "Return only one JSON retrieval plan. Do not inspect files or call "
        "tools. The backend will execute every bounded operation. Include "
        "objective, project, repository, revision, question_type, "
        "evidence_requirements, codegraph_queries, literal_queries, "
        "clarification, max_files, max_fallback_rounds, and budgets. Keep "
        f"objective under {MAX_OBJECTIVE_CHARS} characters. If a "
        "critical input is missing or the target is materially ambiguous, "
        "set clarification to an object with a plain-text question, reason, "
        "and answer_type=text; do not guess or retrieve until the answer "
        "arrives. Otherwise set clarification to null. Leave "
        "source_windows empty unless exact file paths and line ranges are "
        "already known; the executor derives source windows from retrieval "
        "results. If source_windows are provided, use path, start_line, and "
        "end_line as the field names. "
        f"{codegraph_contract} literal_queries must be an array of plain "
        'strings (never objects), for example ["153301", "Sysconfig.ini"]. '
        "Use CodeGraph for structural questions and exact search for logs "
        "or traceback text. Keep max_fallback_rounds at 1 or less. The "
        "workspace is "
        f"{command.get('workspace_path') or 'the selected workspace'}."
    )
    return prompt + _context_guidance(context_skill_contents)


def _planned_planner_repair_prompt(
    validation_error=None,
    codegraph_operations=None,
):
    """Build the repair prompt for an invalid retrieval plan.

    Passes the concrete rejection reason and the allowed operations back so
    the model can fix the specific violation instead of guessing again.
    """

    if codegraph_operations is None:
        operations = ALLOWED_CODEGRAPH_OPERATIONS
    else:
        operations = set(codegraph_operations)
    if operations:
        operation_text = ", ".join(
            f'"{name}"' for name in sorted(operations)
        )
        example_operation = (
            "search" if "search" in operations else sorted(operations)[0]
        )
        codegraph_contract = (
            "Each CodeGraph query must be an object with an operation from "
            f"[{operation_text}] and a query or symbol, for example "
            f'{{"operation": "{example_operation}", "symbol": "VSSMode"}}.'
        )
    else:
        codegraph_contract = (
            "CodeGraph is not available in this run; set codegraph_queries "
            "to []."
        )
    prompt = (
        "Repair the invalid retrieval plan and return only one valid JSON "
        "retrieval plan. Required fields are objective, question_type, "
        "evidence_requirements, codegraph_queries, literal_queries, "
        "source_windows, max_files, max_fallback_rounds, and budgets. "
        f"Keep objective under {MAX_OBJECTIVE_CHARS} characters. "
        "Use path, start_line, and end_line for source window fields. "
        f"{codegraph_contract} literal_queries must be an array of plain "
        "strings, never objects. Keep all searches bounded and set "
        "max_fallback_rounds to 1 or less. Do not answer the question."
    )
    if validation_error:
        prompt += (
            "\nThe previous plan was rejected with this reason: "
            f"{validation_error}"
        )
    return prompt


def _planned_fallback_prompt():
    """Build the one compact adaptive fallback planner contract."""

    return (
        "Return only one bounded JSON retrieval plan for the listed evidence "
        "gaps. Do not include tools or unbounded loops. Use at most one "
        "CodeGraph query and two literal queries. Do not guess source file "
        "paths or line numbers; the executor derives source windows from "
        "retrieval results. Set max_fallback_rounds to 0."
    )


def _planned_final_prompt(compact=False, context_skill_contents=None):
    """Build the final answer contract for compact evidence only."""

    prompt = (
        "Answer the user's code-analysis question only from the supplied "
        "evidence bundle. Return JSON with answer, citations, and "
        "unsupported_claims. In answer, state the direct conclusion in the "
        "first two sentences, explain at most three decisive findings, and "
        "end with a recommended next step or an explicit limitation. Keep "
        "answer under 800 words. Do not include an evidence inventory or "
        "citation appendix inside answer. Return at most five citations, "
        "using only the strongest non-duplicated source evidence when source "
        "evidence is available. Each citation must contain exactly two "
        "non-empty string fields: evidence_id and supports. Copy evidence_id "
        "exactly from a source evidence item, and summarize the supported "
        "claim in supports. Structural or literal "
        "evidence may support an answer without a source citation. The "
        "backend maps source citations to the trusted path, symbol, line "
        "range, and revision. If the evidence bundle does not support a "
        "claim, put it in unsupported_claims and say it is unverified. "
        "Keep runtime evidence separate from source evidence. Return the "
        "JSON object only, with answer as the first field."
    )
    if compact:
        prompt += (
            " The previous structured response was incomplete. Retry once "
            "with answer under 350 words, at most two findings, and at most "
            "three citations. Finish and close the JSON object."
        )
    return prompt + _context_guidance(context_skill_contents)
