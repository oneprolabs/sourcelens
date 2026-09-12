import asyncio
import logging
import math
import threading
from datetime import datetime, timezone

from .agent_runtime import LensDeepAgentRuntime
from .checkpoint import cleanup_run_checkpoint
from .logging_utils import elapsed_since, format_duration, task_log, utc_now
from .runtime_resources import cleanup_run_runtime_resources
from .trajectory import RunTrajectory

LOGGER = logging.getLogger("lensnode")

# How often the inactivity watchdog checks for stalled output.
WATCHDOG_INTERVAL_S = 5

RUN_TIMEOUT_SECONDS = 3600


def _run_timeout_seconds(command):
    """Resolve the Run deadline independently of transport timeouts."""

    value = command.get("run_timeout_s")
    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    ):
        return value
    return RUN_TIMEOUT_SECONDS


def _remaining_run_timeout_seconds(command, now=None):
    """Return the unspent wall-clock budget from the original Run start."""

    timeout_s = _run_timeout_seconds(command)
    remaining = command.get("remaining_run_timeout_s")
    if (
        isinstance(remaining, (int, float))
        and not isinstance(remaining, bool)
        and math.isfinite(remaining)
        and remaining >= 0
    ):
        return min(remaining, timeout_s)
    raw_started_at = command.get("run_started_at")
    if not isinstance(raw_started_at, str) or not raw_started_at.strip():
        return timeout_s
    try:
        started_at = datetime.fromisoformat(
            raw_started_at.strip().replace("Z", "+00:00")
        )
    except ValueError:
        return timeout_s
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    elapsed_s = max((current - started_at).total_seconds(), 0)
    return max(timeout_s - elapsed_s, 0)


def _wrapup_grace_seconds(timeout_s):
    """Reserve bounded time for a tool-free answer before hard timeout."""

    return min(60.0, max(5.0, timeout_s * 0.2), timeout_s * 0.5)


class RunStalledError(TimeoutError):
    """No transport activity at all for the whole idle window.

    The gateway heartbeats every few seconds while a model call is in
    flight, so tripping this means the pipe to the gateway is dead or
    the agent is wedged between calls — distinct from MODEL_TIMEOUT,
    which the gateway reports when the provider itself times out.
    """

    code = "NO_ACTIVITY_TIMEOUT"


class RunDeadlineExceededError(TimeoutError):
    """The run exceeded its configured total wall-clock duration."""

    code = "RUN_TIMEOUT"


def _failure_error_code(exc):
    """Map a run failure to a stable, user-facing error code.

    Timeouts (the inactivity watchdog, the gateway httpx read timeout, or
    a litellm timeout after its retries) all collapse to MODEL_TIMEOUT so
    the UI can explain that the model was too slow rather than showing a
    raw exception. Other failures keep their message for debugging.
    """

    code = getattr(exc, "code", "")
    if code:
        return str(code)
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    if isinstance(exc, TimeoutError) or "timeout" in name or "timeout" in message:
        return "MODEL_TIMEOUT"
    stream_error_markers = [
        "chunked",
        "incomplete",
        "peer closed",
        "remote protocol",
        "connection reset",
        "connection closed",
    ]
    if any(marker in name or marker in message for marker in stream_error_markers):
        return "MODEL_STREAM_ERROR"
    return str(exc)


def _execution_step_type(command):
    """Return the run step type for progress events."""

    if command.get("task") == "general_chat":
        return "general_chat"
    return "retrieval"


async def _cleanup_after_cancelled_worker(
    answer_task,
    run_uuid,
    workspace_path,
    runtime_path=None,
):
    """Clean durable Run state after its synchronous worker has stopped."""

    while True:
        try:
            await asyncio.shield(answer_task)
            break
        except asyncio.CancelledError:
            if answer_task.done():
                break
            continue
        except Exception:
            break
    cleanup_run_checkpoint(run_uuid, workspace_path)
    cleanup_run_runtime_resources(runtime_path or workspace_path, run_uuid)


TASKS = [
    {
        "name": "knowledge_qa",
        "title": "Knowledge Q&A",
        "description": (
            "Answer questions over selected documents and code workspaces "
            "using read-only search and evidence reading."
        ),
        "recommended_questions": [
            "What does this project do?",
            "Where is this feature documented or implemented?",
            "What does this configuration mean?",
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "target_dirs": {"type": "array"},
            },
            "required": ["question", "target_dirs"],
        },
    },
    {
        "name": "code_analysis",
        "title": "Code Analysis",
        "description": (
            "Analyze implementation logic, module responsibilities, "
            "important files, and code flows in selected workspaces."
        ),
        "recommended_questions": [
            "How is the login flow implemented?",
            "Which files implement the billing feature?",
            "What is the call path for this API?",
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "target_dirs": {"type": "array"},
            },
            "required": ["question", "target_dirs"],
        },
    },
    {
        "name": "general_chat",
        "title": "General Chat",
        "description": (
            "Chat and complete tasks with bound Skills and bundled scripts "
            "without searching workspace directories."
        ),
        "recommended_questions": [
            "Use the available capabilities to complete this task.",
            "Follow the configured workflow for this request.",
            "Help me complete this request without workspace retrieval.",
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
            },
            "required": ["question"],
        },
    },
]


class LensNodeExecutor:
    """Translate LensNode protocol commands into Deep Agents execution."""

    def __init__(self, config, http_client=None, plugin_http_pool=None):
        self.agent = LensDeepAgentRuntime(
            config,
            http_client=http_client,
            plugin_http_pool=plugin_http_pool,
        )
        self._pending_workers = {}
        self._pending_cleanup_runs = set()

    def _track_pending_worker(self, run_uuid, worker_task):
        """Track a timed-out agent task until its worker has stopped."""

        pending_workers = getattr(self, "_pending_workers", None)
        if pending_workers is None:
            pending_workers = {}
            self._pending_workers = pending_workers
        pending_workers[run_uuid] = worker_task

        def discard_finished_worker(task):
            try:
                task.exception()
            except asyncio.CancelledError:
                pass
            if pending_workers.get(run_uuid) is task:
                pending_workers.pop(run_uuid, None)

        worker_task.add_done_callback(discard_finished_worker)

    def defer_cleanup_until_worker_stops(
        self, run_uuid, workspace_path, runtime_path=None
    ):
        """Defer acknowledged terminal cleanup for an active worker."""

        worker_task = getattr(self, "_pending_workers", {}).get(run_uuid)
        if worker_task is None or worker_task.done():
            return False
        cleanup_runs = getattr(self, "_pending_cleanup_runs", None)
        if cleanup_runs is None:
            cleanup_runs = set()
            self._pending_cleanup_runs = cleanup_runs
        if run_uuid in cleanup_runs:
            return True
        cleanup_runs.add(run_uuid)

        async def cleanup():
            try:
                await _cleanup_after_cancelled_worker(
                    worker_task,
                    run_uuid,
                    workspace_path,
                    runtime_path,
                )
            finally:
                cleanup_runs.discard(run_uuid)

        asyncio.create_task(cleanup())
        return True

    async def drain_pending_workers(self):
        """Wait until cancelled synchronous Run workers have unwound."""

        while True:
            pending_workers = getattr(self, "_pending_workers", {})
            tasks = [
                task for task in pending_workers.values() if not task.done()
            ]
            if not tasks:
                return
            await asyncio.gather(*tasks, return_exceptions=True)

    async def execute(self, command, emit):
        """Execute one run_start command and emit protocol frames."""

        started_at = utc_now()
        run_uuid = command["run_uuid"]
        trajectory = RunTrajectory(
            run_uuid,
            emit,
            start_sequence=command.get("trace_cursor") or 0,
            attempt=command.get("trace_attempt") or 1,
        )
        command["_trajectory"] = trajectory
        trajectory.record(
            "request.started",
            {
                "task": command.get("task"),
                "question": command.get("question") or "",
                "history": command.get("history") or [],
                "target_dirs": command.get("target_dirs") or [],
                "model_ref": command.get("agent_model_ref"),
                "settings": command.get("settings") or {},
                "resume": bool(command.get("resume")),
            },
        )
        for history_message in command.get("history") or []:
            trajectory.record(
                "context.message",
                {"message": history_message},
            )
        trajectory.record(
            "user.message",
            {
                "content": command.get("question") or "",
                "image_count": len(command.get("image_data_urls") or []),
            },
        )
        task = command.get("task") or "unknown"
        step_type = _execution_step_type(command)
        target_dirs = command.get("target_dirs") or []
        timeout_s = _run_timeout_seconds(command)
        cleanup_deferred = False
        remaining_timeout_s = _remaining_run_timeout_seconds(command)
        idle_timeout_s = getattr(
            self.agent.config, "run_idle_timeout_s", 180
        )
        target_dir_names = ", ".join(
            item.get("path", "") for item in target_dirs
        ) or "none"
        start_message = task_log(
            (
                f"Starting to run command: {task}({run_uuid}). "
                f"Run timeout {format_duration(timeout_s)}, "
                f"remaining {format_duration(remaining_timeout_s)}, "
                f"idle timeout {format_duration(idle_timeout_s)}."
            ),
            started_at,
            [
                f"Task: {task}",
                f"TargetDirs: {target_dir_names}",
                f"AgentModelRef: {command.get('agent_model_ref') or 'none'}",
            ],
        )
        LOGGER.info(start_message)
        emit(
            {
                "type": "run_event",
                "run_uuid": run_uuid,
                "step_type": step_type,
                "status": "running",
                "detail": {
                    "message": start_message,
                    "task": task,
                    "target_dirs": target_dirs,
                    "run_timeout_s": timeout_s,
                    "remaining_run_timeout_s": remaining_timeout_s,
                    "idle_timeout_s": idle_timeout_s,
                },
            }
        )

        try:
            progress_message = task_log(
                (
                    f"Running command {task}({run_uuid}). Current status is "
                    "running Deep Agents. Elapsed time so far: "
                    f"{elapsed_since(started_at)}. Remaining Timeout: "
                    f"{format_duration(remaining_timeout_s)}."
                )
            )
            LOGGER.info(progress_message)
            emit(
                {
                    "type": "run_event",
                    "run_uuid": run_uuid,
                    "step_type": step_type,
                    "status": "running",
                    "detail": {
                        "message": progress_message,
                    },
                }
            )

            loop = asyncio.get_running_loop()
            activity = {"at": loop.time()}
            deadline_at = loop.time() + remaining_timeout_s
            wrapup_at = deadline_at - _wrapup_grace_seconds(
                remaining_timeout_s
            )
            cancel_event = threading.Event()
            wrapup_event = threading.Event()

            def touch_activity():
                activity["at"] = loop.time()

            def emit_progress(message, extra_detail=None):
                if cancel_event.is_set():
                    return
                touch_activity()
                detail = {
                    "message": message,
                }
                if extra_detail:
                    detail.update(extra_detail)
                agent_event = detail.get("agent_event")
                if agent_event:
                    trajectory.record(
                        _trajectory_runtime_event_type(agent_event),
                        {
                            "name": agent_event,
                            **detail,
                        },
                        turn=detail.get("turn"),
                        step=detail.get("step"),
                        call_id=detail.get("call_id"),
                        parent_call_id=detail.get("parent_call_id"),
                    )
                emit(
                    {
                        "type": "run_event",
                        "run_uuid": run_uuid,
                        "step_type": step_type,
                        "status": "running",
                        "detail": detail,
                    }
                )

            def emit_output(content, reset=False):
                if cancel_event.is_set():
                    return
                touch_activity()
                emit(
                    {
                        "type": "run_output",
                        "run_uuid": run_uuid,
                        "content_delta": content,
                        "reset": reset,
                    }
                )

            def emit_checkpoint_ready():
                dispatch_id = command.get("dispatch_id")
                if not dispatch_id or cancel_event.is_set():
                    return
                emit(
                    {
                        "type": "run_checkpoint_ready",
                        "run_uuid": run_uuid,
                        "dispatch_id": str(dispatch_id),
                    }
                )

            # Inactivity watchdog on TRANSPORT activity, not user-visible
            # output: any gateway SSE event (heartbeats, reasoning and
            # tool-call tokens) refreshes the clock via on_activity, so a
            # silent long model call never trips it. Provider stalls are
            # the gateway's call to make (litellm read timeout -> error
            # event); this watchdog only fires when the pipe itself dies.
            # Cancelling the asyncio task cannot stop the agent worker
            # thread, so cancel_event tells the thread to unwind at its
            # next chunk or model-call boundary and mutes its late emits.
            answer_options = {
                "emit_progress": emit_progress,
                "emit_output": emit_output,
                "on_activity": touch_activity,
                "cancel_event": cancel_event,
                "wrapup_event": wrapup_event,
            }
            if command.get("dispatch_id"):
                answer_options["on_checkpoint_ready"] = (
                    emit_checkpoint_ready
                )
            answer_task = asyncio.create_task(
                self.agent.answer(command, **answer_options)
            )

            def stop_answer_task():
                """Signal a timed-out worker and track it until it exits."""

                cancel_event.set()
                self._track_pending_worker(run_uuid, answer_task)

            try:
                while True:
                    remaining_s = deadline_at - loop.time()
                    if remaining_s <= 0:
                        stop_answer_task()
                        raise RunDeadlineExceededError(
                            "Run exceeded total wall-clock timeout of "
                            f"{format_duration(timeout_s)}."
                        )
                    if not wrapup_event.is_set() and loop.time() >= wrapup_at:
                        wrapup_event.set()
                        emit_progress(
                            task_log(
                                "Soft deadline reached; requesting a "
                                "best-effort final answer."
                            ),
                            {
                                "agent_event": (
                                    "deepagents.agent.soft_deadline.requested"
                                ),
                                "remaining_s": max(0, int(remaining_s)),
                            },
                        )
                    wait_timeout_s = min(
                        WATCHDOG_INTERVAL_S,
                        remaining_s,
                    )
                    if not wrapup_event.is_set():
                        wait_timeout_s = min(
                            wait_timeout_s,
                            max(0, wrapup_at - loop.time()),
                        )
                    done, _ = await asyncio.wait(
                        {answer_task},
                        timeout=wait_timeout_s,
                    )
                    if answer_task in done:
                        result = answer_task.result()
                        break
                    if loop.time() >= deadline_at:
                        stop_answer_task()
                        raise RunDeadlineExceededError(
                            "Run exceeded total wall-clock timeout of "
                            f"{format_duration(timeout_s)}."
                        )
                    if loop.time() - activity["at"] > idle_timeout_s:
                        stop_answer_task()
                        raise RunStalledError(
                            "Run saw no gateway activity for "
                            f"{format_duration(idle_timeout_s)}; aborting."
                        )
            except asyncio.CancelledError:
                # run_cancel cancels this coroutine, which does not cancel
                # answer_task or its worker thread on its own. Signal the
                # worker and track it until it stops issuing model calls.
                cancel_event.set()
                self._track_pending_worker(run_uuid, answer_task)
                if command.get("_explicit_cancel"):
                    cleanup_deferred = True
                    workspace_path = getattr(
                        self.agent.config,
                        "workspace_path",
                        None,
                    )
                    runtime_path = getattr(
                        self.agent.config,
                        "runtime_path",
                        None,
                    )
                    asyncio.create_task(
                        _cleanup_after_cancelled_worker(
                            answer_task,
                            run_uuid,
                            workspace_path,
                            runtime_path,
                        )
                    )
                raise
            samples = result.get("samples") or []
            sample_paths = [item["path"] for item in samples]
            retrieval_done_message = task_log(
                (
                    "Finish running Deep Agents for "
                    f"{task}({run_uuid}). "
                    f"Actual duration: {elapsed_since(started_at)}."
                ),
                details=[
                    f"SampleCount: {len(samples)}",
                    f"SamplePaths: {', '.join(sample_paths) or 'none'}",
                ],
            )
            LOGGER.info(retrieval_done_message)
            emit(
                {
                    "type": "run_event",
                    "run_uuid": run_uuid,
                    "step_type": step_type,
                    "status": "done",
                    "detail": {
                        "message": retrieval_done_message,
                        "sample_count": len(samples),
                        "sample_paths": sample_paths,
                        "stop_reason": result.get("stop_reason"),
                        "token_usage": result.get("token_usage") or {},
                        "planned_evidence": (
                            result.get("planned_evidence") or {}
                        ),
                        "citations": [
                            {
                                key: value
                                for key, value in citation.items()
                                if key != "source"
                            }
                            for citation in result.get("citations") or []
                        ],
                    },
                }
            )
            trajectory.record(
                "assistant.message",
                {
                    "content": result["answer"],
                    "citations": result.get("citations") or [],
                },
            )
            emit(
                {
                    "type": "run_output",
                    "run_uuid": run_uuid,
                    "final_content": result["answer"],
                    "citations": result.get("citations") or [],
                    "planned_evidence": (
                        result.get("planned_evidence") or {}
                    ),
                }
            )
            done_message = task_log(
                (
                    f"Finish running command {task}({run_uuid}). Actual "
                    f"duration: {elapsed_since(started_at)}."
                )
            )
            LOGGER.info(done_message)
            trajectory.record(
                "request.completed",
                {
                    "status": result.get("status") or "done",
                    "outcome": result.get("outcome") or "completed",
                    "stop_reason": result.get("stop_reason"),
                    "token_usage": result.get("token_usage") or {},
                    "duration_ms": int(
                        (utc_now() - started_at).total_seconds() * 1000
                    ),
                },
            )
            trajectory.record(
                "run.completed",
                {
                    "status": result.get("status") or "done",
                    "outcome": result.get("outcome") or "completed",
                    "stop_reason": result.get("stop_reason"),
                },
            )
            emit(
                {
                    "type": "run_done",
                    "run_uuid": run_uuid,
                    "status": result.get("status") or "done",
                    "outcome": result.get("outcome") or "completed",
                    "final_content": result["answer"],
                    "citations": result.get("citations") or [],
                    "planned_evidence": (
                        result.get("planned_evidence") or {}
                    ),
                    "termination_detail": (
                        result.get("termination_detail") or {}
                    ),
                    "detail": {
                        "message": done_message,
                    },
                }
            )
        except asyncio.CancelledError:
            trajectory.record(
                "cancelled",
                {"reason": "control_plane_cancel"},
            )
            trajectory.record(
                "request.completed",
                {"status": "cancelled", "outcome": "blocked"},
            )
            trajectory.record(
                "run.completed",
                {"status": "cancelled", "outcome": "blocked"},
            )
            raise
        except Exception as exc:
            error_code = _failure_error_code(exc)
            failed_message = task_log(
                (
                    f"Failed running command {task}({run_uuid}). Actual "
                    f"duration: {elapsed_since(started_at)}."
                ),
                details=[
                    f"ErrorType: {type(exc).__name__}",
                    f"Error: {exc}",
                ],
            )
            LOGGER.error(failed_message)
            trajectory.record(
                "request.failed",
                {
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "code": error_code,
                },
            )
            trajectory.record(
                "run.completed",
                {
                    "status": "failed",
                    "outcome": "blocked",
                    "error": error_code,
                },
            )
            emit(
                {
                    "type": "run_event",
                    "run_uuid": run_uuid,
                    "step_type": step_type,
                    "status": "failed",
                    "detail": {
                        "message": failed_message,
                        "error": error_code,
                        "exception": str(exc),
                    },
                }
            )
            emit(
                {
                    "type": "run_done",
                    "run_uuid": run_uuid,
                    "status": "failed",
                    "error": error_code,
                    "outcome": "blocked",
                    "termination_detail": {
                        "reason": "runtime_failure",
                        "code": error_code,
                    },
                    "detail": {
                        "message": failed_message,
                    },
                }
            )
        finally:
            if command.get("_explicit_cancel") and not cleanup_deferred:
                workspace_path = getattr(
                    self.agent.config,
                    "workspace_path",
                    None,
                )
                runtime_path = getattr(
                    self.agent.config,
                    "runtime_path",
                    None,
                )
                cleanup_run_checkpoint(
                    run_uuid,
                    workspace_path,
                )
                cleanup_run_runtime_resources(runtime_path, run_uuid)


def _trajectory_runtime_event_type(agent_event):
    """Project internal runtime names onto the trajectory vocabulary."""

    name = str(agent_event)
    if "summarization.compacted" in name:
        return "compaction.completed"
    if "summarization" in name:
        return "compaction.event"
    if name.endswith(".resume"):
        return "checkpoint.restored"
    if "retry" in name or "recovery" in name:
        return "retry.event"
    if ".turn." in name:
        return "turn.event"
    return "step.event"
