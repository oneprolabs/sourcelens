"""Regression coverage for executor stop evidence after platform cancellation."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from threading import Event
from unittest.mock import call, patch
from uuid import uuid4

from agentcore_task.adapters.django.models import TaskExecution
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone
from rest_framework.test import APIClient

from lens.consumers import LensNodeConsumer
from lens.models import DataSource, LensNode
from lens.tasks import (
    acquire_datasource_lock,
    complete_datasource_conversion_task,
    datasource_conversion_task,
    reconcile_cancelled_conversion_stops,
    reconcile_orphaned_datasource_conversions,
    register_datasource_conversion_task,
)


class ConversionStopTests(TestCase):
    """Keep platform task outcomes independent of executor stop confirmation."""

    def setUp(self):
        """Create a real conversion task owner and authenticated admin client."""
        self.client = APIClient()
        self.user = get_user_model().objects.create_user(username="stop-admin", is_staff=True)
        self.client.force_authenticate(self.user)
        self.lensnode = LensNode.objects.create(
            name="Stop test node",
            status=LensNode.Status.ONLINE,
            enrollment_status=LensNode.EnrollmentStatus.APPROVED,
            workspace_path="/workspace",
            connection_id="original-connection",
        )
        self.datasource = DataSource.objects.create(
            name="Stop test snapshot",
            source_type=DataSource.SourceType.MANAGED_WORKSPACE,
            lensnode=self.lensnode,
            target_path="/workspace/stop-test",
            last_conversion_status="STARTED",
        )
        self.base_url = f"/api/lens/admin/datasources/{self.datasource.uuid}/"
        self.lock_key = f"lens:datasource-sync:{self.datasource.uuid}"
        self.addCleanup(cache.delete, self.lock_key)

    def task(self, task_id="stop-conversion", status="STARTED", metadata=None):
        """Register a conversion with persisted progress worth preserving."""
        task = register_datasource_conversion_task(
            self.datasource,
            task_id,
            {"document": True},
            metadata={
                "lock_token": task_id,
                "conversion_summary": {"converted": 3, "failed": 1},
                "conversion_checkpoint": {"processed": 4, "total": 8},
                "steps": [{"step": "conversion_progress", "status": "running"}],
                "logs": [{"message": "Converted report.docx"}],
                **(metadata or {}),
            },
        )
        task.status = status
        if status in {"REVOKED", "FAILURE", "SUCCESS"}:
            task.finished_at = timezone.now()
            task.result = {"overall_status": "cancelled", "conversion_summary": {"converted": 3}}
            task.error = "DATASOURCE_CONVERSION_CANCELLED"
        task.save(update_fields=["status", "finished_at", "result", "error"])
        return task

    def evidence(self, **overrides):
        """Return explicit evidence from the connected executor instance."""
        return {
            "status": "cancelled",
            "datasource_uuid": str(self.datasource.uuid),
            "executor_stopped": True,
            "executor_instance_id": "executor-boot-1",
            "stop_confirmation_source": "lensnode_callback",
            "stop_confirmed_at": timezone.now().isoformat(),
            **overrides,
        }

    def callback(self, task, result, **kwargs):
        """Deliver a frame with the socket-authenticated node identity."""
        return complete_datasource_conversion_task(
            task.task_id,
            result,
            connection_id=kwargs.pop("connection_id", "original-connection"),
            lensnode_uuid=kwargs.pop("lensnode_uuid", str(self.lensnode.uuid)),
            **kwargs,
        )

    def cancel(self, task_id=None):
        """Stub the transport boundaries while exercising the real API and DB."""
        with (
            patch("core.celery.app.control.revoke"),
            patch("lens.views.datasources.cancel_datasource_conversion_on_lensnode") as send_cancel,
        ):
            response = self.client.post(
                self.base_url + "cancel-conversion/",
                {"task_id": task_id} if task_id is not None else {},
                format="json",
            )
        return response, send_cancel

    def test_platform_cancellation_is_immediate_but_executor_stop_is_unconfirmed(self):
        """A successful cancel request cannot prove the remote executor stopped."""
        task = self.task()
        response, send_cancel = self.cancel()

        self.assertEqual(response.status_code, 200, response.data)
        send_cancel.assert_called_once_with(self.lensnode, task.task_id)
        task.refresh_from_db()
        self.datasource.refresh_from_db()
        self.assertEqual(task.status, "REVOKED")
        self.assertEqual(self.datasource.last_conversion_status, "REVOKED")
        self.assertEqual(task.metadata["stop_confirmation_state"], "unconfirmed")
        self.assertFalse(task.metadata.get("stop_confirmed_at"))
        self.assertEqual(response.data["stop_confirmation"]["state"], "unconfirmed")

    def test_offline_cancellation_remains_unconfirmed(self):
        """An unavailable executor cannot supply stop evidence synchronously."""
        task = self.task()
        self.lensnode.status = LensNode.Status.OFFLINE
        self.lensnode.save(update_fields=["status"])
        response, _ = self.cancel(task.task_id)
        self.assertEqual(response.status_code, 200, response.data)
        task.refresh_from_db()
        self.assertEqual(task.status, "REVOKED")
        self.assertEqual(task.metadata["stop_confirmation_state"], "unconfirmed")

    def test_cancel_retry_targets_old_revoked_task_without_touching_new_conversion(self):
        """A retry must resend the old task's cancel and preserve newer work."""
        old = self.task("old-conversion", "REVOKED", {"stop_confirmation_state": "unconfirmed"})
        newer = self.task("new-conversion")
        newer_before = deepcopy(newer.metadata)
        original_finished_at = old.finished_at
        acquire_datasource_lock(self.datasource.uuid, token=newer.task_id, ttl_s=60)
        response, send_cancel = self.cancel(old.task_id)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["task_id"], old.task_id)
        send_cancel.assert_called_once_with(self.lensnode, old.task_id)
        old.refresh_from_db()
        newer.refresh_from_db()
        self.datasource.refresh_from_db()
        self.assertEqual(old.finished_at, original_finished_at)
        self.assertEqual(newer.status, "STARTED")
        self.assertEqual(newer.metadata, newer_before)
        self.assertEqual(self.datasource.last_conversion_status, "STARTED")
        self.assertIsNone(self.datasource.last_conversion_at)
        self.assertEqual(cache.get(self.lock_key), newer.task_id)

    def test_cancel_with_unknown_task_id_does_not_fall_back_to_latest(self):
        """Exact task selection must not accidentally cancel unrelated work."""
        task = self.task()
        response, send_cancel = self.cancel("unknown-task")
        self.assertEqual(response.status_code, 404, response.data)
        send_cancel.assert_not_called()
        task.refresh_from_db()
        self.assertEqual(task.status, "STARTED")

    def test_stop_status_is_scoped_to_datasource_and_reports_legacy_as_unconfirmed(self):
        """Old revoked records have no implicit stop proof and cannot leak across sources."""
        task = self.task(status="REVOKED")
        response = self.client.get(self.base_url + f"conversion-tasks/{task.task_id}/stop-status/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "REVOKED")
        self.assertEqual(response.data["stop_confirmation"]["state"], "unconfirmed")
        self.assertEqual(response.data["stop_confirmation"]["task_id"], task.task_id)

        task.metadata["datasource_uuid"] = str(uuid4())
        task.save(update_fields=["metadata"])
        response = self.client.get(self.base_url + f"conversion-tasks/{task.task_id}/stop-status/")
        self.assertEqual(response.status_code, 404, response.data)

    def test_valid_late_stop_evidence_preserves_terminal_result_progress_and_history(self):
        """A valid late frame adds evidence without rewriting the platform outcome."""
        task = self.task(status="REVOKED", metadata={"stop_confirmation_state": "unconfirmed"})
        metadata_before = deepcopy(task.metadata)
        result_before = deepcopy(task.result)
        finished_at = task.finished_at
        frame = self.evidence(conversion_summary={"converted": 99})
        self.callback(task, frame)

        task.refresh_from_db()
        self.assertEqual(task.status, "REVOKED")
        self.assertEqual(task.finished_at, finished_at)
        self.assertEqual(task.result, result_before)
        self.assertEqual(task.error, "DATASOURCE_CONVERSION_CANCELLED")
        for key in ("conversion_summary", "conversion_checkpoint", "steps", "logs"):
            self.assertEqual(task.metadata[key], metadata_before[key], key)
        self.assertEqual(task.metadata["stop_confirmation_state"], "confirmed")
        self.assertEqual(task.metadata["stop_confirmation_task_id"], task.task_id)
        self.assertEqual(task.metadata["stop_confirmation_lensnode_uuid"], str(self.lensnode.uuid))
        self.assertEqual(task.metadata["stop_confirmation_connection_id"], "original-connection")
        self.assertEqual(task.metadata["stop_confirmation_source"], "lensnode_callback")
        self.assertTrue(task.metadata["stop_confirmed_at"])

        response = self.client.get(self.base_url + f"conversion-tasks/{task.task_id}/stop-status/")
        self.assertEqual(response.status_code, 200, response.data)
        confirmation = response.data["stop_confirmation"]
        self.assertEqual(confirmation["state"], "confirmed")
        self.assertEqual(confirmation["lensnode_uuid"], str(self.lensnode.uuid))
        self.assertEqual(confirmation["connection_id"], "original-connection")
        self.assertEqual(confirmation["source"], "lensnode_callback")
        self.assertTrue(confirmation["confirmed_at"])
        self.assertEqual(confirmation["executor_instance_id"], "executor-boot-1")

    def test_stop_duplicates_preserve_empty_summary_and_first_provenance(self):
        """Empty summaries are existing data and duplicate proofs are idempotent."""

        task = self.task(status="REVOKED", metadata={"conversion_summary": {}})
        self.callback(task, self.evidence(conversion_summary={"converted": 99}))
        task.refresh_from_db()
        self.assertEqual(task.metadata["conversion_summary"], {})
        first_evidence = deepcopy(task.metadata)
        self.callback(task, self.evidence())
        task.refresh_from_db()
        self.assertEqual(task.metadata, first_evidence)
        response, send_cancel = self.cancel(task.task_id)
        self.assertEqual(response.data["stop_confirmation"]["state"], "confirmed")
        send_cancel.assert_not_called()

    def test_terminal_callback_without_explicit_stop_evidence_stays_unconfirmed(self):
        """Legacy terminal callbacks cannot confirm executor quiescence."""
        task = self.task(status="REVOKED", metadata={"stop_confirmation_state": "unconfirmed"})
        before = deepcopy(task.metadata)
        self.callback(task, {"status": "cancelled", "conversion_summary": {}})
        task.refresh_from_db()
        self.assertEqual(task.metadata, before)

    def test_failed_or_successful_platform_outcome_is_preserved_by_late_stop_evidence(self):
        """Executor stop evidence must not reclassify other terminal outcomes."""
        for status in ("FAILURE", "SUCCESS"):
            with self.subTest(status=status):
                task = self.task(f"terminal-{status.lower()}", status)
                finished_at = task.finished_at
                result = deepcopy(task.result)
                self.callback(task, self.evidence(), stop_only=True)
                task.refresh_from_db()
                self.assertEqual(task.status, status)
                self.assertEqual(task.result, result)
                self.assertEqual(task.finished_at, finished_at)
                self.assertEqual(task.metadata["stop_confirmation_state"], "confirmed")

    def test_explicit_cancelled_completion_confirms_stop_while_legacy_does_not(self):
        """Active completion uses the executor flag rather than terminal status as proof."""
        for stopped in (True, False):
            with self.subTest(executor_stopped=stopped):
                task = self.task(f"active-{stopped}")
                frame = self.evidence()
                if not stopped:
                    frame.pop("executor_stopped")
                self.callback(task, frame)
                task.refresh_from_db()
                self.assertEqual(task.status, "REVOKED")
                self.assertEqual(task.metadata["stop_confirmation_state"], "confirmed" if stopped else "unconfirmed")

    def test_terminal_callback_rejects_wrong_connection_node_or_datasource(self):
        """Terminal state must not bypass the ownership checks for stop evidence."""
        task = self.task(status="REVOKED", metadata={"stop_confirmation_state": "unconfirmed"})
        before = deepcopy(task.metadata)
        invalid_frames = [
            (self.evidence(), {"connection_id": "stale-connection"}),
            (self.evidence(), {"lensnode_uuid": str(uuid4())}),
            (self.evidence(datasource_uuid=str(uuid4())), {}),
        ]
        for frame, kwargs in invalid_frames:
            with self.subTest(frame=frame, kwargs=kwargs):
                self.callback(task, frame, **kwargs)
                task.refresh_from_db()
                self.assertEqual(task.metadata, before)
                self.assertEqual(task.status, "REVOKED")

    def test_active_callback_rejects_wrong_datasource_before_completing(self):
        """Matching socket ownership alone cannot complete the wrong datasource."""
        task = self.task()
        before = deepcopy(task.metadata)
        self.callback(task, self.evidence(status="success", datasource_uuid=str(uuid4())))
        task.refresh_from_db()
        self.datasource.refresh_from_db()
        self.assertEqual(task.status, "STARTED")
        self.assertEqual(task.metadata, before)
        self.assertEqual(self.datasource.last_conversion_status, "STARTED")

    def test_stop_only_frame_does_not_change_active_task_or_datasource_outcome(self):
        """Dedicated stopped acknowledgements merge evidence only."""
        task = self.task()
        before = deepcopy(task.metadata)
        self.callback(task, self.evidence(), stop_only=True)
        task.refresh_from_db()
        self.datasource.refresh_from_db()
        self.assertEqual(task.status, "STARTED")
        self.assertIsNone(task.finished_at)
        self.assertEqual(self.datasource.last_conversion_status, "STARTED")
        self.assertEqual(task.metadata["conversion_summary"], before["conversion_summary"])
        self.assertEqual(task.metadata["stop_confirmation_state"], "confirmed")

    def test_reconnect_retries_revoked_unconfirmed_work_and_empty_snapshot_is_not_proof(self):
        """Reconnect must fence old work even if the new snapshot omits it."""
        task = self.task(status="REVOKED", metadata={"stop_confirmation_state": "unconfirmed"})
        for operations in (
            [{"task_id": task.task_id, "datasource_uuid": str(self.datasource.uuid), "operation": "conversion"}],
            [],
        ):
            with self.subTest(operations=operations):
                task.metadata["lensnode_connection_id"] = "original-connection"
                task.save(update_fields=["metadata"])
                with (
                    patch("lens.services.cancel_datasource_conversion_on_lensnode") as send_cancel,
                    patch("lens.tasks.confirm_orphaned_datasource_conversion.apply_async") as schedule,
                ):
                    reconcile_orphaned_datasource_conversions(self.lensnode.uuid, "new-connection", operations)
                task.refresh_from_db()
                self.assertEqual(task.status, "REVOKED")
                self.assertEqual(task.metadata["lensnode_connection_id"], "new-connection")
                self.assertEqual(task.metadata["stop_confirmation_state"], "unconfirmed")
                self.assertFalse(task.metadata.get("stop_confirmed_at"))
                send_cancel.assert_called_once_with(self.lensnode, task.task_id)
                schedule.assert_not_called()

    def test_confirmed_revoked_task_is_not_retried_on_reconnect(self):
        """Confirmed work no longer needs a reconnect cancellation command."""
        task = self.task(status="REVOKED", metadata={"stop_confirmation_state": "confirmed"})
        with patch("lens.services.cancel_datasource_conversion_on_lensnode") as send_cancel:
            reconcile_orphaned_datasource_conversions(self.lensnode.uuid, "new-connection", [])
        task.refresh_from_db()
        send_cancel.assert_not_called()
        self.assertEqual(task.metadata["lensnode_connection_id"], "original-connection")

    def test_periodic_stop_retry_skips_offline_nodes_without_mutating_evidence(self):
        """Offline work stays pending until its original node can accept cancellation."""
        task = self.task(status="REVOKED", metadata={"stop_confirmation_state": "unconfirmed"})
        before = deepcopy(task.metadata)
        self.lensnode.status = LensNode.Status.OFFLINE
        self.lensnode.save(update_fields=["status"])
        with patch("lens.services.cancel_datasource_conversion_on_lensnode") as send_cancel:
            count = reconcile_cancelled_conversion_stops()
        self.assertEqual(count, 0)
        send_cancel.assert_not_called()
        task.refresh_from_db()
        self.assertEqual(task.metadata, before)

    def test_periodic_stop_retry_rebinds_legacy_and_unconfirmed_tasks_to_original_node(self):
        """Reassigning a datasource cannot redirect stop requests for old executors."""
        legacy = self.task("legacy-revoked", "REVOKED")
        pending = self.task("pending-stop", "REVOKED", {"stop_confirmation_state": "unconfirmed"})
        new_owner = LensNode.objects.create(
            name="Reassigned node", status=LensNode.Status.ONLINE, connection_id="different-node-connection"
        )
        self.datasource.lensnode = new_owner
        self.datasource.save(update_fields=["lensnode"])
        self.lensnode.connection_id = "reconnected-original-node"
        self.lensnode.save(update_fields=["connection_id"])
        with patch("lens.services.cancel_datasource_conversion_on_lensnode") as send_cancel:
            count = reconcile_cancelled_conversion_stops()
        self.assertEqual(count, 2)
        self.assertCountEqual(
            send_cancel.call_args_list,
            [call(self.lensnode, legacy.task_id), call(self.lensnode, pending.task_id)],
        )
        for task in (legacy, pending):
            task.refresh_from_db()
            self.assertEqual(task.metadata["lensnode_connection_id"], "reconnected-original-node")
            self.assertEqual(task.metadata["stop_confirmation_state"], "unconfirmed")
            self.assertTrue(task.metadata["stop_confirmation_requested_at"])

    def test_periodic_stop_retry_is_throttled_and_stops_after_evidence_arrives(self):
        """Lost cancellations retry after 30 seconds until a valid acknowledgement arrives."""
        task = self.task(status="REVOKED", metadata={"stop_confirmation_state": "unconfirmed"})
        now = timezone.now()
        with patch("lens.services.cancel_datasource_conversion_on_lensnode") as send_cancel:
            self.assertEqual(reconcile_cancelled_conversion_stops(now), 1)
            self.assertEqual(reconcile_cancelled_conversion_stops(now + timedelta(seconds=29)), 0)
            self.assertEqual(reconcile_cancelled_conversion_stops(now + timedelta(seconds=31)), 1)
        self.assertEqual(send_cancel.call_count, 2)
        self.callback(task, self.evidence(), stop_only=True)
        with patch("lens.services.cancel_datasource_conversion_on_lensnode") as send_cancel:
            self.assertEqual(reconcile_cancelled_conversion_stops(now + timedelta(minutes=2)), 0)
        send_cancel.assert_not_called()

    def test_consumer_rejects_explicit_task_id_that_disagrees_with_request_mapping(self):
        """A request-id and task-id disagreement cannot confirm either task."""
        expected = self.task("request-owner", "REVOKED", {"datasource_conversion_request_id": "request-1"})
        other = self.task("unrelated-task", "REVOKED")
        before = {task.task_id: deepcopy(task.metadata) for task in (expected, other)}
        cache_key = "lens:datasource_conversion_request:request-1"
        cache.set(cache_key, expected.task_id)
        self.addCleanup(cache.delete, cache_key)
        resolved = LensNodeConsumer._complete_datasource_conversion_done(
            "request-1",
            self.evidence(task_id=other.task_id),
            "original-connection",
            str(self.lensnode.uuid),
        )
        self.assertFalse(resolved)
        for task in (expected, other):
            task.refresh_from_db()
            self.assertEqual(task.metadata, before[task.task_id])

    def test_consumer_handles_stop_ack_and_completion_with_authenticated_identity(self):
        """Both frame paths persist proof, while stopped acknowledgements preserve outcomes."""
        for stop_only in (True, False):
            with self.subTest(stop_only=stop_only):
                task = self.task(f"consumer-{stop_only}", "REVOKED" if stop_only else "STARTED")
                before = deepcopy(task.metadata["conversion_summary"])
                resolved = LensNodeConsumer._complete_datasource_conversion_done(
                    "",
                    self.evidence(task_id=task.task_id),
                    "original-connection",
                    str(self.lensnode.uuid),
                    stop_only,
                )
                self.assertEqual(resolved, task.task_id)
                task.refresh_from_db()
                self.assertEqual(task.status, "REVOKED")
                self.assertEqual(task.metadata["stop_confirmation_state"], "confirmed")
                self.assertEqual(task.metadata["stop_confirmation_lensnode_uuid"], str(self.lensnode.uuid))
                if stop_only:
                    self.assertEqual(task.metadata["conversion_summary"], before)


class ConversionDispatchCancellationTests(TransactionTestCase):
    """Exercise cancellation racing with a real locked dispatch transaction."""

    @skipUnlessDBFeature("has_select_for_update")
    def test_cancellation_during_dispatch_cannot_resurrect_revoked_task(self):
        """Cancellation waits for dispatch persistence and then keeps the task revoked."""
        from lens.views.datasources import _finalize_datasource_cancellation

        node = LensNode.objects.create(
            name="Concurrent node",
            status=LensNode.Status.ONLINE,
            enrollment_status=LensNode.EnrollmentStatus.APPROVED,
            connection_id="concurrent-connection",
        )
        datasource = DataSource.objects.create(
            name="Concurrent snapshot",
            source_type=DataSource.SourceType.MANAGED_WORKSPACE,
            lensnode=node,
            target_path="/workspace/concurrent",
        )
        task = register_datasource_conversion_task(datasource, "concurrent-conversion", {"document": True})
        self.addCleanup(cache.delete, f"lens:datasource-sync:{datasource.uuid}")
        dispatch_entered = Event()
        release_dispatch = Event()
        cancel_entered = Event()
        cancel_finished = Event()

        def paused_transport(*args, **kwargs):
            """Hold the dispatch while a second DB connection requests cancellation."""
            dispatch_entered.set()
            if not release_dispatch.wait(5):
                raise RuntimeError("Timed out waiting for test dispatch release")
            return "concurrent-request"

        def dispatch():
            """Run the real Celery task using its own database connection."""
            close_old_connections()
            try:
                return datasource_conversion_task(str(datasource.uuid), {"document": True}, task_id=task.task_id)
            finally:
                close_old_connections()

        def cancel():
            """Run the real cancellation finalizer on a separate database connection."""
            close_old_connections()
            try:
                current_task = TaskExecution.objects.get(pk=task.pk)
                current_datasource = DataSource.objects.select_related("lensnode").get(pk=datasource.pk)
                cancel_entered.set()
                result = _finalize_datasource_cancellation(
                    current_task, current_datasource, "DATASOURCE_CONVERSION_CANCELLED"
                )
                cancel_finished.set()
                return result.status
            finally:
                close_old_connections()

        with (
            patch("lens.tasks.dispatch_datasource_conversion_async", side_effect=paused_transport),
            patch("core.celery.app.control.revoke"),
            patch("lens.views.datasources.cancel_datasource_conversion_on_lensnode") as send_cancel,
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            dispatch_future = executor.submit(dispatch)
            try:
                self.assertTrue(dispatch_entered.wait(5), "Dispatch did not reach the transport")
                cancel_future = executor.submit(cancel)
                self.assertTrue(cancel_entered.wait(5), "Cancellation did not reach the row lock")
                self.assertFalse(cancel_finished.wait(0.2), "Cancellation bypassed the dispatch transaction")
            finally:
                release_dispatch.set()
            dispatch_future.result(timeout=5)
            self.assertEqual(cancel_future.result(timeout=5), "REVOKED")
        task.refresh_from_db()
        self.assertEqual(task.status, "REVOKED")
        self.assertEqual(task.metadata["stop_confirmation_state"], "unconfirmed")
        self.assertEqual(task.metadata["datasource_conversion_request_id"], "concurrent-request")
        send_cancel.assert_called_once_with(node, task.task_id)
