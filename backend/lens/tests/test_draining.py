from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import TransactionTestCase

from core.asgi import application
from lens.lensnode_auth import issue_lensnode_token
from lens.models import (
    Assistant,
    LensNode,
    Run,
    Session,
)
from lens.services import (
    LensNodeDispatchError,
    create_execution_run,
    validate_run_dispatch,
)

User = get_user_model()


class LensNodeDrainingTests(TransactionTestCase):
    """A draining LensNode is excluded from new-run dispatch.

    Complements the disconnect grace-period: this is the node's own
    shutdown/upgrade path, where it announces draining so no new run is routed
    to it while it finishes in-flight work.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username="drain-user",
            email="drain-user@example.com",
            password="pass12345",
        )
        self.lensnode = LensNode.objects.create(
            name="Drain LensNode",
            status=LensNode.Status.ONLINE,
            enrollment_status=LensNode.EnrollmentStatus.APPROVED,
            workspace_path="/workspace",
            available_dirs=[{"path": "/workspace/repo"}],
            tasks=[{"name": "knowledge_qa"}],
        )
        self.assistant = Assistant.objects.create(
            name="Advisor",
            slug="advisor",
            lensnode=self.lensnode,
            selected_task="knowledge_qa",
            selected_dirs=[{"path": "/workspace/repo"}],
        )
        self.session = Session.objects.create(
            assistant=self.assistant,
            user=self.user,
            title="",
        )

    def test_dispatch_rejects_draining_node(self):
        run = create_execution_run(
            session=self.session, question="q", enqueue=False
        )
        run.lensnode = self.lensnode
        run.save(update_fields=["lensnode"])
        self.lensnode.status = LensNode.Status.DRAINING
        self.lensnode.save(update_fields=["status"])

        with self.assertRaises(LensNodeDispatchError) as ctx:
            validate_run_dispatch(run)
        self.assertEqual(str(ctx.exception), "LENSNODE_DRAINING")

    def test_node_draining_frame_sets_status_draining(self):
        token = issue_lensnode_token(self.lensnode)
        # Read the status while still connected — a later disconnect flips it
        # to OFFLINE, so the DRAINING state must be observed before that.
        status = async_to_sync(self._connect_send_draining)(token)
        self.assertEqual(status, LensNode.Status.DRAINING)

    def test_health_probe_ack_is_persisted_for_control_plane(self):
        token = issue_lensnode_token(self.lensnode)
        acknowledged = async_to_sync(self._connect_send_probe_ack)(token)
        self.assertTrue(acknowledged)

    def test_heartbeat_does_not_hide_an_unresponsive_node(self):
        token = issue_lensnode_token(self.lensnode)
        status = async_to_sync(
            self._connect_send_heartbeat_while_unresponsive
        )(token)
        self.assertEqual(status, LensNode.Status.UNRESPONSIVE)

    def test_revoked_token_closes_connection_on_heartbeat(self):
        token = issue_lensnode_token(self.lensnode)
        closed = async_to_sync(self._connect_revoke_then_heartbeat)(token)
        self.assertTrue(closed)

    async def _connect_send_draining(self, token):
        from channels.db import database_sync_to_async
        from channels.testing import WebsocketCommunicator

        communicator = WebsocketCommunicator(
            application,
            f"/ws/lens/lensnodes/?token={token}",
        )
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        await communicator.receive_json_from()
        await communicator.send_json_to({"type": "node_draining"})
        # No reply is expected; the wait lets the consumer process the frame.
        await communicator.receive_nothing(timeout=1)
        status = await database_sync_to_async(
            lambda: LensNode.objects.get(uuid=self.lensnode.uuid).status
        )()
        await communicator.disconnect()
        return status

    async def _connect_send_probe_ack(self, token):
        from channels.testing import WebsocketCommunicator
        from django.core.cache import cache

        communicator = WebsocketCommunicator(
            application,
            f"/ws/lens/lensnodes/?token={token}",
        )
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        await communicator.receive_json_from()
        await communicator.send_json_to(
            {"type": "health_probe_ack", "request_id": "probe-1"}
        )
        await communicator.receive_nothing(timeout=1)
        acknowledged = cache.get(
            f"lens:lensnode_health:{self.lensnode.uuid}:probe-1"
        )
        await communicator.disconnect()
        return acknowledged

    async def _connect_send_heartbeat_while_unresponsive(self, token):
        from channels.db import database_sync_to_async
        from channels.testing import WebsocketCommunicator

        communicator = WebsocketCommunicator(
            application,
            f"/ws/lens/lensnodes/?token={token}",
        )
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        await communicator.receive_json_from()
        await database_sync_to_async(LensNode.objects.filter(
            uuid=self.lensnode.uuid
        ).update)(status=LensNode.Status.UNRESPONSIVE)
        await communicator.send_json_to({"type": "heartbeat"})
        response = await communicator.receive_json_from()
        self.assertEqual(response["type"], "heartbeat_ack")
        status = await database_sync_to_async(
            lambda: LensNode.objects.get(uuid=self.lensnode.uuid).status
        )()
        await communicator.disconnect()
        return status

    async def _connect_revoke_then_heartbeat(self, token):
        from channels.db import database_sync_to_async
        from channels.testing import WebsocketCommunicator

        communicator = WebsocketCommunicator(
            application,
            f"/ws/lens/lensnodes/?token={token}",
        )
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        await communicator.receive_json_from()
        await database_sync_to_async(LensNode.objects.filter(
            uuid=self.lensnode.uuid
        ).update)(token_revoked=True)
        await communicator.send_json_to({"type": "heartbeat"})
        output = await communicator.receive_output(timeout=1)
        await communicator.disconnect()
        return output["type"] == "websocket.close" and output["code"] == 4401
