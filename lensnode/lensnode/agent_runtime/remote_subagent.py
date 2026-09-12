"""Control-plane backed subagents for cross-LensNode delegation."""

import time
import uuid

from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable

from ..delegation_events import delegation_events
from ..gateway_model import RunCancelledError


TERMINAL_STATUSES = {
    "awaiting_user_input",
    "done",
    "failed",
    "cancelled",
}


class RemoteSubagentRunnable(Runnable):
    """Run one configured assistant through its control-plane child Run."""

    def __init__(
        self,
        *,
        assistant_uuid,
        parent_run_uuid,
        delegation_base_url,
        token,
        http_client,
        delegation_group_key="",
        cancel_event=None,
        on_activity=None,
        poll_interval_s=0.3,
        push_wait_s=5,
        timeout_s=3600,
    ):
        self.assistant_uuid = str(assistant_uuid)
        self.parent_run_uuid = str(parent_run_uuid)
        self.delegation_group_key = str(delegation_group_key or "")
        self.delegation_base_url = str(delegation_base_url).rstrip("/")
        self.token = token
        self.http_client = http_client
        self.cancel_event = cancel_event
        self.on_activity = on_activity
        self.poll_interval_s = max(float(poll_interval_s), 0.05)
        self.push_wait_s = max(float(push_wait_s), self.poll_interval_s)
        self.timeout_s = max(float(timeout_s), 1)

    def invoke(self, input, config=None, **kwargs):
        """Create the child Run, poll it, and return its final message."""

        del config, kwargs
        question = self._question(input)
        delegation_key = str(uuid.uuid4())
        delegation_group_key = self.delegation_group_key or delegation_key
        url = (
            f"{self.delegation_base_url}/{self.parent_run_uuid}/"
            "delegations/"
        )
        response = self.http_client.post(
            url,
            headers={"Authorization": f"Bearer {self.token}"},
            json={
                "assistant_uuid": self.assistant_uuid,
                "question": question,
                "delegation_key": delegation_key,
                "delegation_group_key": delegation_group_key,
            },
        )
        response.raise_for_status()
        payload = self._response_data(response)
        child_uuid = str(payload.get("run_uuid") or "")
        if not child_uuid:
            keys = sorted(payload) if isinstance(payload, dict) else []
            raise RuntimeError(
                "Delegation response did not include run_uuid. "
                f"Response keys: {keys}"
            )

        deadline = time.monotonic() + self.timeout_s
        while payload.get("status") not in TERMINAL_STATUSES:
            self._check_cancelled()
            self._touch_activity()
            if time.monotonic() >= deadline:
                raise TimeoutError("Delegated assistant timed out.")
            payload = delegation_events.wait(
                child_uuid,
                min(self.push_wait_s, deadline - time.monotonic()),
            )
            if payload is not None:
                self._touch_activity()
                continue
            time.sleep(self.poll_interval_s)
            response = self.http_client.get(
                f"{url}{child_uuid}/",
                headers={"Authorization": f"Bearer {self.token}"},
            )
            response.raise_for_status()
            payload = self._response_data(response)
            self._touch_activity()

        self._check_cancelled()
        if payload.get("status") == "done":
            citations = payload.get("citations") or []
            return {
                "messages": [
                    AIMessage(
                        content=str(payload.get("answer") or ""),
                        additional_kwargs={"delegated_citations": citations},
                        response_metadata={"delegated_citations": citations},
                    )
                ]
            }
        error = str(payload.get("error") or payload.get("status") or "")
        raise RuntimeError(f"Delegated assistant failed: {error}")

    def _check_cancelled(self):
        """Stop waiting when the parent Run has been cancelled."""

        if self.cancel_event is not None and self.cancel_event.is_set():
            raise RunCancelledError("Parent Run cancelled delegated work.")

    def _touch_activity(self):
        """Keep the parent watchdog alive while a child Run is active."""

        if self.on_activity is not None:
            self.on_activity()

    @staticmethod
    def _question(input):
        """Extract the task description passed by Deep Agents."""

        messages = input.get("messages", []) if isinstance(input, dict) else []
        if not messages:
            raise ValueError("Delegated assistant question is required.")
        content = getattr(messages[-1], "content", "")
        question = str(content or "").strip()
        if not question:
            raise ValueError("Delegated assistant question is required.")
        return question

    @staticmethod
    def _response_data(response):
        """Unwrap the control plane's standard response envelope."""

        payload = response.json()
        if isinstance(payload, dict) and isinstance(
            payload.get("data"),
            dict,
        ):
            return payload["data"]
        return payload
