"""Complete, ordered trajectory events for one LensNode run."""

import json
import logging
import threading
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from copy import copy

from .logging_utils import utc_now

LOGGER = logging.getLogger("lensnode")
TRACE_SCHEMA_VERSION = 1


def _json_value(value):
    """Return a JSON-safe copy without removing observable content."""

    try:
        return json.loads(
            json.dumps(
                value,
                allow_nan=False,
                default=str,
                ensure_ascii=False,
            )
        )
    except Exception:
        LOGGER.warning("Trajectory payload required fallback serialization")
        try:
            serialized = str(value)
        except Exception:
            serialized = f"<unserializable {type(value).__name__}>"
        return {
            "serialization_error": type(value).__name__,
            "value": serialized,
        }


class RunTrajectory:
    """Allocate run-local sequences and publish append-only event frames."""

    def __init__(
        self,
        run_uuid,
        emit_frame,
        *,
        start_sequence=0,
        attempt=1,
        trace_state=None,
        persist_state=None,
    ):
        state = trace_state if isinstance(trace_state, dict) else {}
        schema_version = state.get("trace_schema_version")
        if state and schema_version != TRACE_SCHEMA_VERSION:
            raise ValueError("Unsupported trajectory checkpoint schema")
        self.run_uuid = str(run_uuid)
        self.emit_frame = emit_frame
        self.persist_state = persist_state
        self._lock = threading.RLock()
        self._sequence = max(
            int(state.get("last_trace_seq") or start_sequence or 0),
            0,
        )
        self._attempt = max(
            int(state.get("current_attempt") or attempt or 1),
            1,
        )
        self._open_call_ids = {
            str(value) for value in state.get("open_call_ids") or []
        }
        self._open_span_ids = {
            str(value) for value in state.get("open_span_ids") or []
        }
        self._parent_call_map = {
            str(key): str(value)
            for key, value in (state.get("parent_call_map") or {}).items()
        }
        self._span_context = ContextVar("trajectory_spans", default=())
        self._span_context.set(tuple(str(value) for value in state.get("span_stack") or []))
        self._closed_call_ids = set()
        self._call_names = {}
        self._finished = False
        self._degraded = False
        self._call_categories = dict(state.get("call_categories") or {})
        self._call_names.update(state.get("call_names") or {})
        self._active_tool_call = ContextVar("trajectory_tool_call", default=None)

    @contextmanager
    def tool_call_scope(self, call_id):
        """Isolate the active tool invocation across threads and async tasks."""

        token = self._active_tool_call.set(str(call_id))
        span_token = self._span_context.set((*self._span_context.get(), str(call_id)))
        try:
            yield
        finally:
            self._span_context.reset(span_token)
            self._active_tool_call.reset(token)

    def tool_event_detail(self, detail):
        """Attach tool progress to its invocation without guessing a pairing."""

        detail = dict(detail or {})
        call_id = self._active_tool_call.get()
        if call_id is None:
            return detail
        explicit_id = detail.get("call_id") or detail.get("invocation_id")
        if explicit_id and str(explicit_id) != call_id:
            detail.setdefault("parent_call_id", call_id)
            return detail
        detail["call_id"] = call_id
        with self._lock:
            parent_id = self._parent_call_map.get(call_id)
        if parent_id:
            detail.setdefault("parent_call_id", parent_id)
        return detail

    def push_span(self, call_id):
        """Enter a span in the current thread/task context only."""

        if call_id:
            stack = self._span_context.get()
            value = str(call_id)
            if not stack or stack[-1] != value:
                self._span_context.set((*stack, value))

    def pop_span(self, call_id=None):
        """Leave a span without modifying concurrent execution contexts."""

        stack = self._span_context.get()
        if call_id is None:
            self._span_context.set(stack[:-1])
        elif str(call_id) in stack:
            self._span_context.set(stack[:stack.index(str(call_id))])

    def _current_span(self):
        stack = self._span_context.get()
        return stack[-1] if stack else f"run:{self.run_uuid}"

    @property
    def finished(self):
        """Return whether the terminal run event has been published."""

        with self._lock:
            return self._finished

    @property
    def health(self):
        """Report degradation independently from the final business outcome."""

        with self._lock:
            return "degraded" if self._degraded else "healthy"

    def record(
        self,
        event_type,
        payload=None,
        *,
        checkpoint_id=None,
        turn=None,
        step=None,
        call_id=None,
        parent_call_id=None,
    ):
        """Publish one event and persist the resulting resume cursor."""

        with self._lock:
            payload = copy(payload or {})
            diagnostics = list(payload.get("diagnostics") or [])
            bound_parent = self._parent_call_map.get(str(call_id)) if call_id else None
            if bound_parent is not None:
                if parent_call_id is not None and str(parent_call_id) != bound_parent:
                    diagnostics.append("parent_context_mismatch")
                parent_call_id = bound_parent
            elif parent_call_id is None:
                current = self._current_span()
                if current is not None and str(current) != str(call_id):
                    parent_call_id = current
            if call_id and parent_call_id:
                self._parent_call_map[str(call_id)] = str(parent_call_id)
            category = str(event_type).split(".", 1)[0]
            name = str(payload.get("name") or "")
            suffix = (name if category == "step" else str(event_type)).rsplit(".", 1)[-1]
            boundary = category in {"model", "tool", "subtool", "run", "step"}
            registered_category = self._call_categories.get(str(call_id))
            if registered_category and registered_category != category:
                boundary = False
            lifecycle_suffixes = {"start", "started", "done", "completed", "failed", "cancelled", "interrupted"}
            if category == "step" and str(call_id) in self._call_names:
                base = name.rsplit(".", 1)[0] if suffix in lifecycle_suffixes else name
                if base != self._call_names[str(call_id)]:
                    boundary = False
            # Runtime annotations sharing the run ID cannot close the run itself.
            if str(call_id) == f"run:{self.run_uuid}" and category != "run":
                boundary = False
            starts = suffix in {"start", "started"} or name in {
                "deepagents.agent.create", "deepagents.agent.invoke"
            }
            ends = suffix in {"done", "completed", "failed", "cancelled", "interrupted"}
            if self._finished:
                diagnostics.append("after_run_finished")
            if call_id and str(call_id) in self._closed_call_ids and boundary:
                diagnostics.append("late_event")
            elif call_id and boundary:
                key = str(call_id)
                if starts:
                    self._open_call_ids.add(key)
                    self._call_categories[key] = category
                    self._call_names[key] = name.rsplit(".", 1)[0] if suffix in {"start", "started"} else name
                elif ends:
                    if key not in self._open_call_ids:
                        diagnostics.append("missing_start")
                    self._open_call_ids.discard(key)
                    self._open_span_ids.discard(key)
                    self._closed_call_ids.add(key)
            if payload.get("fallback_reason") or payload.get("ok") is False or suffix in {"failed", "interrupted"}:
                self._degraded = True
            if diagnostics:
                payload["diagnostics"] = sorted(set(diagnostics))
                self._degraded = True
            if category == "run" and ends:
                self._finished = True
            self._sequence += 1
            event = {
                "event_id": str(uuid.uuid4()),
                "sequence": self._sequence,
                "attempt": self._attempt,
                "event_type": str(event_type),
                "timestamp": utc_now().isoformat().replace("+00:00", "Z"),
                "payload": _json_value(payload or {}),
            }
            for key, value in (
                ("checkpoint_id", checkpoint_id),
                ("turn", turn),
                ("step", step),
                ("call_id", call_id),
                ("parent_call_id", parent_call_id),
            ):
                if key in {"turn", "step"} and (
                    not isinstance(value, int)
                    or isinstance(value, bool)
                    or value < 1
                ):
                    continue
                if value is not None and value != "":
                    event[key] = value
            frame = {
                "type": "run_trace_events",
                "run_uuid": self.run_uuid,
                "events": [event],
            }
            snapshot = self._snapshot_unlocked()
            self.emit_frame(frame)
            if self.persist_state is not None and "after_run_finished" not in diagnostics:
                try:
                    self.persist_state(snapshot)
                except Exception:
                    LOGGER.exception("Failed to persist trajectory cursor")
            return event

    def start_call(
        self,
        category,
        name,
        payload=None,
        *,
        call_id=None,
        parent_call_id=None,
        turn=None,
        step=None,
    ):
        """Open a model, tool, or subtool call and return its stable ID."""

        call_id = str(call_id or uuid.uuid4().hex)
        category = str(category)
        with self._lock:
            if parent_call_id is None:
                parent_call_id = self._parent_call_map.get(call_id)
            if parent_call_id is None:
                parent_call_id = self._current_span()
            self._open_call_ids.add(call_id)
            if category in {"tool", "subtool"}:
                self._open_span_ids.add(call_id)
            if parent_call_id:
                self._parent_call_map[call_id] = str(parent_call_id)
            self._call_categories[call_id] = category
            detail = dict(payload or {})
            detail.setdefault("name", str(name))
            self.record(
                f"{category}.started",
                detail,
                turn=turn,
                step=step,
                call_id=call_id,
                parent_call_id=parent_call_id,
            )
            return call_id

    def bind_parent(self, call_id, parent_call_id):
        """Associate a future tool call with the model that requested it."""

        if not call_id or not parent_call_id:
            return
        with self._lock:
            self._parent_call_map[str(call_id)] = str(parent_call_id)

    def finish_call(
        self,
        call_id,
        status,
        payload=None,
        *,
        turn=None,
        step=None,
    ):
        """Close a call with completed, failed, or interrupted status."""

        call_id = str(call_id)
        with self._lock:
            category = self._call_categories.get(call_id, "tool")
            parent_call_id = self._parent_call_map.get(call_id)
            return self.record(
                f"{category}.{status}",
                payload,
                turn=turn,
                step=step,
                call_id=call_id,
                parent_call_id=parent_call_id,
            )

    def snapshot(self):
        """Return checkpoint-safe trajectory continuation metadata."""

        with self._lock:
            return self._snapshot_unlocked()

    def merge_resume_state(self, trace_state):
        """Merge checkpoint-only open-call state without moving backwards."""

        if not isinstance(trace_state, dict):
            return
        if trace_state.get("trace_schema_version") != TRACE_SCHEMA_VERSION:
            raise ValueError("Unsupported trajectory checkpoint schema")
        with self._lock:
            self._sequence = max(
                self._sequence,
                int(trace_state.get("last_trace_seq") or 0),
            )
            self._attempt = max(
                self._attempt,
                int(trace_state.get("current_attempt") or 1),
            )
            self._open_call_ids.update(
                str(value) for value in trace_state.get("open_call_ids") or []
            )
            self._open_span_ids.update(
                str(value) for value in trace_state.get("open_span_ids") or []
            )
            self._call_categories.update(trace_state.get("call_categories") or {})
            self._call_names.update(trace_state.get("call_names") or {})
            self._parent_call_map.update(
                {
                    str(key): str(value)
                    for key, value in (
                        trace_state.get("parent_call_map") or {}
                    ).items()
                }
            )

    def interrupt_open_calls(self, reason):
        """Close unfinished children before a run ends, preserving their parent IDs."""

        with self._lock:
            for call_id in sorted(self._open_call_ids):
                if call_id == f"run:{self.run_uuid}":
                    continue
                category = self._call_categories.get(
                    call_id, "tool" if call_id in self._open_span_ids else "model"
                )
                payload = {"reason": str(reason), "category": category}
                if category == "step":
                    payload["name"] = f"{self._call_names.get(call_id, call_id)}.interrupted"
                self.record(
                    "step.event" if category == "step" else f"{category}.interrupted",
                    payload,
                    call_id=call_id,
                    parent_call_id=self._parent_call_map.get(call_id),
                )

    def _snapshot_unlocked(self):
        return {
            "trace_schema_version": TRACE_SCHEMA_VERSION,
            "last_trace_seq": self._sequence,
            "current_attempt": self._attempt,
            "open_call_ids": sorted(self._open_call_ids),
            "open_span_ids": sorted(self._open_span_ids),
            "parent_call_map": dict(sorted(self._parent_call_map.items())),
            "span_stack": list(self._span_context.get()),
            "call_categories": dict(self._call_categories),
            "call_names": dict(self._call_names),
        }
