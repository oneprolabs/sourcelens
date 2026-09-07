from datetime import timedelta
from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import TransactionTestCase
from django.utils import timezone

from core.asgi import application
from lens.lensnode_auth import issue_lensnode_token
from lens.models import (
    Assistant,
    DataSource,
    GlobalSetting,
    LensNode,
    Run,
    RunExecution,
    Session,
)
from lens.services import (
    LENSNODE_DISCONNECT_GRACE_SECONDS_DEFAULT,
    RECONCILE_CONFIRM_GRACE_SECONDS_DEFAULT,
    create_execution_run,
    get_lensnode_disconnect_grace_seconds,
    get_awaiting_resume_ttl_hours,
    get_reconcile_confirm_grace_seconds,
)
from lens.tasks import (
    check_lensnode_disconnect_grace_period,
    register_datasource_conversion_task,
)

User = get_user_model()


class LensNodeDisconnectGraceTests(TransactionTestCase):
    """Grace-period handling for LensNode WebSocket disconnects.

    A disconnect (e.g. a blue/green API recycle) must not fail the node's
    in-flight runs immediately; only a node still gone after the grace window
    does.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username="grace-user",
            email="grace-user@example.com",
            password="pass12345",
        )
        self.lensnode = LensNode.objects.create(
            name="Grace LensNode",
            status=LensNode.Status.ONLINE,
            enrollment_status=LensNode.EnrollmentStatus.APPROVED,
            workspace_path="/workspace",
            available_dirs=[{"path": "/workspace/repo"}],
            tasks=[{"name": "knowledge_qa"}],
            labels={
                "run_checkpoint_resume": True,
                "run_checkpoint_ttl_hours": 24,
            },
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

    def _make_running_run(self):
        run = create_execution_run(session=self.session, question="q", enqueue=False)
        run.status = Run.Status.RUNNING
        run.started_at = timezone.now()
        run.save(update_fields=["status", "started_at"])
        return run

    def test_grace_seconds_default_and_override(self):
        self.assertEqual(
            get_lensnode_disconnect_grace_seconds(),
            LENSNODE_DISCONNECT_GRACE_SECONDS_DEFAULT,
        )
        GlobalSetting.objects.create(key="lensnode.disconnect_grace_s", value=42)
        self.assertEqual(get_lensnode_disconnect_grace_seconds(), 42)

    def test_reconcile_confirm_grace_seconds_default_and_override(self):
        self.assertEqual(RECONCILE_CONFIRM_GRACE_SECONDS_DEFAULT, 10)
        self.assertEqual(
            get_reconcile_confirm_grace_seconds(),
            RECONCILE_CONFIRM_GRACE_SECONDS_DEFAULT,
        )
        GlobalSetting.objects.create(key="lensnode.reconcile_confirm_grace_s", value=7)
        self.assertEqual(get_reconcile_confirm_grace_seconds(), 7)

    def test_resume_ttl_is_capped_by_node_retention(self):
        GlobalSetting.objects.create(key="lensnode.resume_ttl_h", value=48)
        self.lensnode.labels["run_checkpoint_ttl_hours"] = 12
        self.lensnode.save(update_fields=["labels"])

        self.assertEqual(
            get_awaiting_resume_ttl_hours(self.lensnode),
            12,
        )

    def test_check_parks_runs_when_still_offline(self):
        run = self._make_running_run()
        stamp = timezone.now()
        self.lensnode.status = LensNode.Status.OFFLINE
        self.lensnode.disconnected_at = stamp
        self.lensnode.save(update_fields=["status", "disconnected_at"])

        check_lensnode_disconnect_grace_period(
            str(self.lensnode.uuid), stamp.isoformat()
        )

        run.refresh_from_db()
        self.assertEqual(run.status, Run.Status.RUNNING)
        self.assertIsNotNone(run.resume_by)

    def test_check_confirms_active_conversion_when_node_stays_offline(self):
        datasource = DataSource.objects.create(
            name="Managed Snapshot",
            source_type=DataSource.SourceType.MANAGED_WORKSPACE,
            lensnode=self.lensnode,
            target_path="/workspace/restores/finance",
        )
        task = register_datasource_conversion_task(
            datasource,
            "offline-conversion",
            {"document": True},
        )
        task.status = "STARTED"
        task.metadata["lensnode_connection_id"] = "old-connection"
        task.save(update_fields=["status", "metadata"])
        stamp = timezone.now()
        self.lensnode.status = LensNode.Status.OFFLINE
        self.lensnode.connection_id = ""
        self.lensnode.disconnected_at = stamp
        self.lensnode.save(update_fields=["status", "connection_id", "disconnected_at"])

        with patch(
            "lens.tasks.confirm_orphaned_datasource_conversion.apply_async"
        ) as apply_async:
            check_lensnode_disconnect_grace_period(
                str(self.lensnode.uuid),
                stamp.isoformat(),
            )

        task.refresh_from_db()
        self.assertEqual(
            task.metadata["datasource_orphan_confirmation_connection_id"],
            "",
        )
        apply_async.assert_called_once()

    def test_check_expires_run_past_original_wall_clock_budget(self):
        run = self._make_running_run()
        run.started_at = timezone.now() - timedelta(seconds=61)
        run.execution.run_timeout_s = 60
        run.execution.status = RunExecution.Status.RUNNING
        run.execution.save(update_fields=["run_timeout_s", "status"])
        run.save(update_fields=["started_at"])
        stamp = timezone.now()
        self.lensnode.status = LensNode.Status.OFFLINE
        self.lensnode.disconnected_at = stamp
        self.lensnode.save(update_fields=["status", "disconnected_at"])

        check_lensnode_disconnect_grace_period(
            str(self.lensnode.uuid), stamp.isoformat()
        )

        run.refresh_from_db()
        run.execution.refresh_from_db()
        self.assertEqual(run.status, Run.Status.FAILED)
        self.assertEqual(run.error, "LENSNODE_RESUME_EXPIRED")
        self.assertIsNone(run.resume_by)
        self.assertEqual(run.execution.status, RunExecution.Status.FAILED)

    def test_check_fails_run_when_node_cannot_resume_checkpoint(self):
        run = self._make_running_run()
        stamp = timezone.now()
        self.lensnode.status = LensNode.Status.OFFLINE
        self.lensnode.disconnected_at = stamp
        self.lensnode.labels = {}
        self.lensnode.save(update_fields=["status", "disconnected_at", "labels"])

        check_lensnode_disconnect_grace_period(
            str(self.lensnode.uuid), stamp.isoformat()
        )

        run.refresh_from_db()
        self.assertEqual(run.status, Run.Status.FAILED)
        self.assertEqual(run.error, "LENSNODE_DISCONNECTED")
        self.assertIsNone(run.resume_by)

    def test_check_noops_when_node_reconnected(self):
        run = self._make_running_run()
        stamp = timezone.now()
        # Still ONLINE (reconnected within the window) with disconnected_at
        # cleared — the check must leave the run running.
        self.lensnode.status = LensNode.Status.ONLINE
        self.lensnode.disconnected_at = None
        self.lensnode.save(update_fields=["status", "disconnected_at"])

        check_lensnode_disconnect_grace_period(
            str(self.lensnode.uuid), stamp.isoformat()
        )

        run.refresh_from_db()
        self.assertEqual(run.status, Run.Status.RUNNING)

    def test_check_noops_on_stale_episode(self):
        run = self._make_running_run()
        scheduled_stamp = timezone.now()
        # A newer disconnect moved disconnected_at on; this stale check, pinned
        # to the earlier episode, must defer to the newer one and no-op.
        self.lensnode.status = LensNode.Status.OFFLINE
        self.lensnode.disconnected_at = scheduled_stamp + timezone.timedelta(seconds=5)
        self.lensnode.save(update_fields=["status", "disconnected_at"])

        check_lensnode_disconnect_grace_period(
            str(self.lensnode.uuid), scheduled_stamp.isoformat()
        )

        run.refresh_from_db()
        self.assertEqual(run.status, Run.Status.RUNNING)

    def test_check_noops_for_unknown_node(self):
        import uuid

        # Must not raise for a node that no longer exists.
        check_lensnode_disconnect_grace_period(
            str(uuid.uuid4()), timezone.now().isoformat()
        )

    def test_disconnect_defers_instead_of_failing_runs(self):
        token = issue_lensnode_token(self.lensnode)
        run = self._make_running_run()

        with patch(
            "lens.tasks.check_lensnode_disconnect_grace_period.apply_async"
        ) as apply_async:
            async_to_sync(self._connect_then_disconnect)(token)

        # Run is NOT failed on disconnect; instead a grace check was scheduled.
        run.refresh_from_db()
        self.assertEqual(run.status, Run.Status.RUNNING)
        self.lensnode.refresh_from_db()
        self.assertEqual(self.lensnode.status, LensNode.Status.OFFLINE)
        self.assertIsNotNone(self.lensnode.disconnected_at)
        self.assertTrue(apply_async.called)
        kwargs = apply_async.call_args.kwargs
        self.assertEqual(kwargs["args"][0], str(self.lensnode.uuid))
        self.assertIn("countdown", kwargs)

    def test_disconnect_schedules_check_when_health_task_cleared_owner(self):
        # If lensnode_health_task marked the node OFFLINE and cleared
        # connection_id before the socket finally closes, disconnect()'s CAS on
        # connection_id misses — but it must still schedule the grace check so
        # a genuinely-dead node's runs are failed, not left RUNNING.
        token = issue_lensnode_token(self.lensnode)
        run = self._make_running_run()

        with patch(
            "lens.tasks.check_lensnode_disconnect_grace_period.apply_async"
        ) as apply_async:
            async_to_sync(self._connect_clear_owner_then_disconnect)(token)

        run.refresh_from_db()
        self.assertEqual(run.status, Run.Status.RUNNING)
        self.lensnode.refresh_from_db()
        self.assertIsNotNone(self.lensnode.disconnected_at)
        self.assertTrue(apply_async.called)

    async def _connect_then_disconnect(self, token):
        from channels.testing import WebsocketCommunicator

        communicator = WebsocketCommunicator(
            application,
            f"/ws/lens/lensnodes/?token={token}",
        )
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        await communicator.receive_json_from()
        await communicator.disconnect()

    async def _connect_clear_owner_then_disconnect(self, token):
        from channels.db import database_sync_to_async
        from channels.testing import WebsocketCommunicator

        communicator = WebsocketCommunicator(
            application,
            f"/ws/lens/lensnodes/?token={token}",
        )
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        await communicator.receive_json_from()
        # Simulate lensnode_health_task getting there first.
        await database_sync_to_async(
            LensNode.objects.filter(uuid=self.lensnode.uuid).update
        )(status=LensNode.Status.OFFLINE, connection_id="")
        await communicator.disconnect()
