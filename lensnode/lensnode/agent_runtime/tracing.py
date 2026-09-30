"""Privacy-bounded tracing middleware for LensNode agent runs."""

import re
import time
import uuid
from contextlib import nullcontext

from langchain.agents.middleware import AgentMiddleware

from ..logging_utils import utc_now


class TraceObservationMiddleware(AgentMiddleware):
    """Emit privacy-bounded spans around synchronous and async tool calls."""

    def __init__(
        self,
        emit_observation,
        root_observation_id,
        trajectory=None,
    ):
        self.emit_observation = emit_observation
        self.root_observation_id = root_observation_id
        self.trajectory = trajectory
        self._started_at = {}

    def wrap_tool_call(self, request, handler):
        """Wrap one synchronous tool call in a trace observation."""

        observation_id, trajectory_id = self._start(request)
        try:
            with (
                self.trajectory.tool_call_scope(trajectory_id)
                if self.trajectory is not None
                else nullcontext()
            ):
                result = handler(request)
        except Exception as exc:
            self._finish(observation_id, trajectory_id, "failed", error=exc)
            raise
        status = (
            "failed"
            if getattr(result, "status", None) == "error"
            else "done"
        )
        self._finish(
            observation_id,
            trajectory_id,
            status,
            result=result,
        )
        return result

    async def awrap_tool_call(self, request, handler):
        """Wrap one asynchronous tool call in a trace observation."""

        observation_id, trajectory_id = self._start(request)
        try:
            with (
                self.trajectory.tool_call_scope(trajectory_id)
                if self.trajectory is not None
                else nullcontext()
            ):
                result = await handler(request)
        except Exception as exc:
            self._finish(observation_id, trajectory_id, "failed", error=exc)
            raise
        status = (
            "failed"
            if getattr(result, "status", None) == "error"
            else "done"
        )
        self._finish(
            observation_id,
            trajectory_id,
            status,
            result=result,
        )
        return result

    def _start(self, request):
        """Emit a start event without tool arguments."""

        observation_id = uuid.uuid4().hex
        tool = getattr(request, "tool", None)
        raw_name = getattr(tool, "name", None) or "unknown"
        tool_name = re.sub(r"[^A-Za-z0-9._-]", "_", str(raw_name))[:96]
        if self.emit_observation is not None and self.root_observation_id:
            self.emit_observation(
                {
                    "action": "start",
                    "id": observation_id,
                    "parent_observation_id": self.root_observation_id,
                    "name": f"tool.{tool_name or 'unknown'}",
                    "started_at": observation_timestamp(),
                }
            )
        tool_call = getattr(request, "tool_call", None) or {}
        trajectory_id = str(tool_call.get("id") or uuid.uuid4().hex)
        self._started_at[trajectory_id] = time.monotonic()
        if self.trajectory is not None:
            self.trajectory.start_call(
                "tool",
                tool_name or "unknown",
                {"arguments": tool_call.get("args") or {}},
                call_id=trajectory_id,
            )
        return observation_id, trajectory_id

    def _finish(
        self,
        observation_id,
        trajectory_id,
        status,
        *,
        result=None,
        error=None,
    ):
        """Emit an end event without tool output or exception text."""

        event = {
            "action": "end",
            "id": observation_id,
            "status": status,
            "ended_at": observation_timestamp(),
        }
        if error is not None:
            event["error_type"] = type(error).__name__
        if self.emit_observation is not None and self.root_observation_id:
            self.emit_observation(event)
        if self.trajectory is not None:
            started_at = self._started_at.pop(
                trajectory_id,
                time.monotonic(),
            )
            payload = {
                "duration_ms": int((time.monotonic() - started_at) * 1000)
            }
            if result is not None:
                payload["result"] = {
                    "content": getattr(result, "content", result),
                    "status": getattr(result, "status", None),
                    "artifact": getattr(result, "artifact", None),
                    "additional_kwargs": getattr(
                        result,
                        "additional_kwargs",
                        None,
                    ),
                }
            if error is not None:
                payload.update(
                    {
                        "error_type": type(error).__name__,
                        "error": str(error),
                    }
                )
            trajectory_status = "completed" if status == "done" else status
            self.trajectory.finish_call(
                trajectory_id,
                trajectory_status,
                payload,
            )


def observation_timestamp():
    """Return an ingestion-compatible UTC timestamp."""

    return utc_now().isoformat().replace("+00:00", "Z")
