"""End-to-end ASGI coverage for cancellation stop-confirmation frames."""

from unittest.mock import patch

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from core.asgi import application
from lens.lensnode_auth import issue_lensnode_token
from lens.models import DataSource, LensNode
from lens.tasks import register_datasource_conversion_task


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
    CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
)
class ConversionStopTransportTests(TransactionTestCase):
    """Verify real socket delivery and acknowledgement against the database."""

    def test_reconnect_fences_revoked_work_and_acknowledges_duplicate_stop_evidence(self):
        """An empty hello snapshot prompts cancellation, never immediate confirmation."""
        node = LensNode.objects.create(
            name="Stop transport node",
            status=LensNode.Status.OFFLINE,
            enrollment_status=LensNode.EnrollmentStatus.APPROVED,
            connection_id="old-connection",
            workspace_path="/workspace",
        )
        datasource = DataSource.objects.create(
            name="Stop transport snapshot",
            source_type=DataSource.SourceType.MANAGED_WORKSPACE,
            lensnode=node,
            target_path="/workspace/transport",
            last_conversion_status="REVOKED",
            last_conversion_at=timezone.now(),
        )
        task = register_datasource_conversion_task(
            datasource,
            "transport-revoked-conversion",
            {"document": True},
            metadata={
                "stop_confirmation_state": "unconfirmed",
                "conversion_summary": {"converted": 2, "failed": 1},
                "conversion_checkpoint": {"processed": 3},
            },
        )
        task.status = "REVOKED"
        task.finished_at = timezone.now()
        task.result = {"overall_status": "cancelled", "conversion_summary": {"converted": 2}}
        task.save(update_fields=["status", "finished_at", "result"])
        finished_at = task.finished_at
        result = task.result
        token = issue_lensnode_token(node)

        with (
            patch("lens.consumers.session_workspace_cleanup_task.delay"),
            patch("lens.consumers.schedule_lensnode_disconnect_grace_check"),
        ):
            proof = async_to_sync(self._exercise_stop_protocol)(token, task, datasource)

        task.refresh_from_db()
        datasource.refresh_from_db()
        self.assertEqual(task.status, "REVOKED")
        self.assertEqual(task.finished_at, finished_at)
        self.assertEqual(task.result, result)
        self.assertEqual(datasource.last_conversion_status, "REVOKED")
        self.assertEqual(task.metadata, proof)
        self.assertEqual(task.metadata["conversion_summary"], {"converted": 2, "failed": 1})
        self.assertEqual(task.metadata["conversion_checkpoint"], {"processed": 3})
        self.assertEqual(task.metadata["stop_confirmation_state"], "confirmed")
        self.assertEqual(task.metadata["stop_confirmation_task_id"], task.task_id)
        self.assertEqual(task.metadata["stop_confirmation_lensnode_uuid"], str(node.uuid))
        self.assertEqual(task.metadata["stop_confirmation_source"], "lensnode_callback")
        self.assertTrue(task.metadata["stop_confirmed_at"])
        self.assertNotEqual(task.metadata["stop_confirmation_connection_id"], "old-connection")

    async def _exercise_stop_protocol(self, token, task, datasource):
        """Send hello and duplicate stop acknowledgements through the real consumer."""
        communicator = WebsocketCommunicator(application, f"/ws/lens/lensnodes/?token={token}")
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        try:
            self.assertEqual((await communicator.receive_json_from())["type"], "connected")
            await communicator.send_json_to(
                {
                    "type": "hello",
                    "protocol_version": "v1",
                    "agent_version": "1.0.0",
                    "workspace_path": "/workspace",
                    "available_dirs": [],
                    "tasks": [],
                    "active_datasource_operations": [],
                }
            )
            frames = [await communicator.receive_json_from(), await communicator.receive_json_from()]
            self.assertCountEqual([frame["type"] for frame in frames], ["hello_ack", "datasource_convert_cancel"])
            cancel_frame = next(frame for frame in frames if frame["type"] == "datasource_convert_cancel")
            self.assertEqual(
                cancel_frame,
                {
                    "type": "datasource_convert_cancel",
                    "task_id": task.task_id,
                    "datasource_uuid": str(datasource.uuid),
                },
            )
            before = await database_sync_to_async(self._task_metadata)(task)
            self.assertEqual(before["stop_confirmation_state"], "unconfirmed")
            self.assertFalse(before.get("stop_confirmed_at"))
            frame = {
                "type": "datasource_convert_stopped",
                "task_id": task.task_id,
                "datasource_uuid": str(datasource.uuid),
                "executor_stopped": True,
                "executor_instance_id": "transport-test-boot",
                "stop_confirmation_source": "lensnode_callback",
                "stop_confirmed_at": timezone.now().isoformat(),
            }
            await communicator.send_json_to(frame)
            self.assertEqual(
                await communicator.receive_json_from(),
                {"type": "datasource_terminal_ack", "task_id": task.task_id},
            )
            proof = await database_sync_to_async(self._task_metadata)(task)
            await communicator.send_json_to(frame)
            self.assertEqual(
                await communicator.receive_json_from(),
                {"type": "datasource_terminal_ack", "task_id": task.task_id},
            )
            self.assertEqual(await database_sync_to_async(self._task_metadata)(task), proof)
            return proof
        finally:
            await communicator.disconnect()

    @staticmethod
    def _task_metadata(task):
        """Read committed task state after a socket frame has been acknowledged."""
        task.refresh_from_db()
        return dict(task.metadata)
