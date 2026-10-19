"""Exercise streaming disconnects while the synchronous provider is blocked."""

import asyncio
import json
import threading
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from lens.views.gateway import LensNodeAIGatewayView


class GatewayStreamLifecycleTests(SimpleTestCase):
    """Keep ASGI cancellation independent from the provider thread's lifetime."""

    async def test_disconnect_returns_promptly_and_releases_lock_only_after_worker_exits(self):
        """Do not await a blocked provider or release its budget on disconnect."""

        blocked = threading.Event()
        resume = threading.Event()
        closed = threading.Event()
        released = threading.Event()

        def provider(**_kwargs):
            """Yield once, then simulate a provider blocked on a network read."""

            try:
                yield "content", "first"
                blocked.set()
                resume.wait(5)
                yield "content", "late"
            finally:
                closed.set()

        view = LensNodeAIGatewayView()
        view._release_parent_budget_lock = lambda _lock: released.set()
        with patch("lens.views.gateway.call_and_track_with_fallback", provider):
            response = view._stream_response(SimpleNamespace(uuid="test"), "model", [], {}, {}, budget_lock="lock")
            stream = response.streaming_content
            self.assertIn(b"first", await anext(stream))
            pending = asyncio.create_task(anext(stream))
            try:
                for _ in range(100):
                    if blocked.is_set():
                        break
                    await asyncio.sleep(0.001)
                self.assertTrue(blocked.is_set())
                pending.cancel()
                done, _ = await asyncio.wait({pending}, timeout=0.1)
                self.assertIn(pending, done, "disconnect waits for the blocked provider")
                self.assertFalse(released.is_set())
            finally:
                resume.set()
                await asyncio.gather(pending, return_exceptions=True)
                for _ in range(100):
                    if released.is_set():
                        break
                    await asyncio.sleep(0.001)
            self.assertTrue(closed.is_set())
            self.assertTrue(released.is_set())

    async def test_normal_completion_preserves_tokens_usage_and_lock_release(self):
        """Worker cleanup must also run on successful and failed streams."""

        for fails in (False, True):
            releases = []

            def provider(**_kwargs):
                """Return the same shape as the metering generator."""

                yield "content", "answer"
                if fails:
                    raise ValueError("synthetic provider failure")
                return {"total_tokens": 12}

            view = LensNodeAIGatewayView()
            view._release_parent_budget_lock = releases.append
            with patch("lens.views.gateway.call_and_track_with_fallback", provider):
                response = view._stream_response(SimpleNamespace(uuid="test"), "model", [], {}, {}, budget_lock="lock")
                events = [
                    json.loads(chunk.decode().removeprefix("data: ")) async for chunk in response.streaming_content
                ]
            self.assertEqual(events[0]["content"], "answer")
            self.assertEqual(events[-1]["type"], "error" if fails else "done")
            if not fails:
                self.assertEqual(events[-1]["usage"], {"total_tokens": 12})
            self.assertEqual(releases, ["lock"])
