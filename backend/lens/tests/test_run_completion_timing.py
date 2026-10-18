"""Keep terminal processing latency distinct from node execution duration."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.test import SimpleTestCase

from lens.consumers import LensNodeConsumer
from lens.models import Run


class RunCompletionTimingTests(SimpleTestCase):
    """Log bounded, validated transport timestamps without changing the Run."""

    async def test_terminal_timing_preserves_ack_and_does_not_trust_arbitrary_text(self):
        """Timestamp fields are optional and must not inject text into logs."""

        run_uuid = uuid.uuid4()
        consumer = LensNodeConsumer()
        consumer.send_json = AsyncMock()
        run = SimpleNamespace(pk=1, status=Run.Status.DONE)
        with (
            patch("lens.consumers.finish_lensnode_run", return_value=run),
            patch.object(consumer, "_delegation_done_payload", return_value=None),
            self.assertLogs("lens.consumers", level="INFO") as logs,
        ):
            await consumer._handle_run_done(
                {
                    "run_uuid": str(run_uuid),
                    "status": "done",
                    "node_enqueued_at": "2026-10-18T01:00:00+00:00",
                    "node_sent_at": "bad\nforged-log-line",
                }
            )
        output = "\n".join(logs.output)
        self.assertIn("node_enqueued_at=2026-10-18T01:00:00+00:00", output)
        self.assertIn("handler_ms=", output)
        self.assertNotIn("forged-log-line", output)
        consumer.send_json.assert_awaited_once_with({"type": "run_done_ack", "run_uuid": str(run_uuid)})
