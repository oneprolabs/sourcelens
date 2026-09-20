import uuid
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import TransactionTestCase
from django.utils import timezone

from lens.consumers import LensNodeConsumer
from lens.models import (
    Assistant,
    LensNode,
    Run,
    RunExecution,
    RunStep,
    Session,
)
from lens.services import (
    acknowledge_run_admitted,
    acknowledge_run_checkpoint_ready,
    create_execution_run,
    finish_lensnode_run,
    mark_active_runs_awaiting_resume,
    reconcile_lensnode_active_runs,
    record_lensnode_run_event,
    resume_awaiting_run,
    resume_awaiting_runs_for_lensnode,
    touch_run_activity,
)
from lens.tasks import (
    confirm_reconcile_orphan,
    expire_awaiting_run,
    lensnode_cleanup_task,
)

User = get_user_model()


class RunLifecycleTests(TransactionTestCase):
    def setUp(self):
        expiration_patcher = patch(
            "lens.tasks.expire_awaiting_run.apply_async"
        )
        self.expiration_apply_async = expiration_patcher.start()
        self.addCleanup(expiration_patcher.stop)
        self.user = User.objects.create_user(
            username="rl-user",
            email="rl-user@example.com",
            password="pass12345",
        )
        self.lensnode = LensNode.objects.create(
            name="Local LensNode",
            status=LensNode.Status.ONLINE,
            enrollment_status=LensNode.EnrollmentStatus.APPROVED,
            workspace_path="/workspace",
            labels={"run_checkpoint_resume": True},
        )
        self.assistant = Assistant.objects.create(
            name="Code Advisor",
            slug="code-advisor",
            lensnode=self.lensnode,
            selected_task="knowledge_qa",
        )
        self.session = Session.objects.create(
            assistant=self.assistant, user=self.user
        )

    def _run(self, status, started_delta, activity_delta):
        """Create a run with explicit status and timestamps."""

        run = create_execution_run(
            session=self.session, question="q", enqueue=False
        )
        now = timezone.now()
        Run.objects.filter(pk=run.pk).update(
            status=status,
            started_at=now - started_delta,
            last_activity_at=(
                None if activity_delta is None else now - activity_delta
            ),
        )
        run.refresh_from_db()
        return run

    def _mark_execution_running(self, run):
        """Mark a fixture as admitted by its LensNode."""

        run.execution.status = RunExecution.Status.RUNNING
        run.execution.admitted_at = timezone.now()
        run.execution.save(update_fields=["status", "admitted_at"])

    def test_touch_run_activity_throttles(self):
        run = self._run(
            Run.Status.STREAMING,
            timedelta(seconds=60),
            timedelta(seconds=3),
        )
        before = run.last_activity_at
        touch_run_activity(run.pk)
        run.refresh_from_db()
        self.assertEqual(run.last_activity_at, before)  # fresh -> no write

        Run.objects.filter(pk=run.pk).update(
            last_activity_at=timezone.now() - timedelta(seconds=30)
        )
        touch_run_activity(run.pk)
        run.refresh_from_db()
        self.assertLess(
            (timezone.now() - run.last_activity_at).total_seconds(), 5
        )

    def test_record_run_event_drops_late_event_for_terminal_run(self):
        run = self._run(
            Run.Status.FAILED,
            timedelta(minutes=5),
            timedelta(minutes=5),
        )

        step = record_lensnode_run_event(
            run.uuid, "retrieval", "running", {"message": "late"}
        )

        self.assertIsNone(step)
        self.assertEqual(run.steps.count(), 0)

    def test_record_run_event_persists_for_active_run(self):
        run = self._run(
            Run.Status.STREAMING,
            timedelta(seconds=10),
            timedelta(seconds=10),
        )

        step = record_lensnode_run_event(
            run.uuid, "retrieval", "running", {"message": "progress"}
        )

        self.assertIsNotNone(step)
        self.assertEqual(run.steps.count(), 1)

    def test_finish_lensnode_run_persists_awaiting_user_input(self):
        run = self._run(
            Run.Status.STREAMING,
            timedelta(seconds=10),
            timedelta(seconds=10),
        )
        request = {
            "request_id": "clarification-1",
            "question": "Which environment should I inspect?",
            "reason": "ambiguous_scope",
            "answer_type": "text",
        }

        finish_lensnode_run(
            run.uuid,
            Run.Status.AWAITING_USER_INPUT,
            outcome=Run.Outcome.BLOCKED,
            termination_detail={
                "reason": "needs_user_input",
                "request": request,
            },
        )

        run.refresh_from_db()
        self.assertEqual(run.status, Run.Status.AWAITING_USER_INPUT)
        self.assertEqual(run.termination_detail["reason"], "needs_user_input")
        self.assertEqual(run.execution.status, RunExecution.Status.COMPLETED)

    def test_first_running_event_marks_dispatched_execution_admitted(self):
        run = self._run(
            Run.Status.STREAMING,
            timedelta(seconds=10),
            timedelta(seconds=10),
        )
        run.execution.status = RunExecution.Status.DISPATCHED
        run.execution.save(update_fields=["status"])

        record_lensnode_run_event(
            run.uuid,
            "retrieval",
            "running",
            {"message": "accepted"},
        )

        run.execution.refresh_from_db()
        self.assertEqual(
            run.execution.status,
            RunExecution.Status.RUNNING,
        )
        self.assertIsNotNone(run.execution.admitted_at)

    def test_queued_running_event_does_not_mark_execution_admitted(self):
        run = self._run(
            Run.Status.STREAMING,
            timedelta(seconds=10),
            timedelta(seconds=10),
        )
        run.execution.status = RunExecution.Status.DISPATCHED
        run.execution.save(update_fields=["status"])

        record_lensnode_run_event(
            run.uuid,
            "retrieval",
            "running",
            {
                "queue_state": "QUEUED",
                "message": "Waiting for LensNode execution capacity.",
            },
        )

        run.execution.refresh_from_db()
        self.assertEqual(
            run.execution.status,
            RunExecution.Status.DISPATCHED,
        )
        self.assertIsNone(run.execution.admitted_at)

    def test_stale_dispatch_acknowledgements_are_ignored(self):
        run = self._run(
            Run.Status.STREAMING,
            timedelta(seconds=10),
            timedelta(seconds=10),
        )
        current_dispatch_id = uuid.uuid4()
        run.execution.status = RunExecution.Status.DISPATCHED
        run.execution.dispatch_id = current_dispatch_id
        run.execution.save(update_fields=["status", "dispatch_id"])

        admitted = acknowledge_run_admitted(
            run.uuid,
            uuid.uuid4(),
            self.lensnode.uuid,
        )
        checkpoint_ready = acknowledge_run_checkpoint_ready(
            run.uuid,
            uuid.uuid4(),
            self.lensnode.uuid,
        )

        run.execution.refresh_from_db()
        self.assertFalse(admitted)
        self.assertFalse(checkpoint_ready)
        self.assertEqual(
            run.execution.status,
            RunExecution.Status.DISPATCHED,
        )
        self.assertIsNone(run.execution.admitted_at)
        self.assertIsNone(run.execution.checkpoint_ready_at)

    def test_consumer_routes_admission_protocol_frames(self):
        consumer = LensNodeConsumer()
        consumer._handle_run_admitted = AsyncMock()
        consumer._handle_run_checkpoint_ready = AsyncMock()
        admitted_frame = {
            "type": "run_admitted",
            "run_uuid": str(uuid.uuid4()),
            "dispatch_id": str(uuid.uuid4()),
        }
        checkpoint_frame = {
            "type": "run_checkpoint_ready",
            "run_uuid": str(uuid.uuid4()),
            "dispatch_id": str(uuid.uuid4()),
        }

        async_to_sync(consumer.receive_json)(admitted_frame)
        async_to_sync(consumer.receive_json)(checkpoint_frame)

        consumer._handle_run_admitted.assert_awaited_once_with(
            admitted_frame
        )
        consumer._handle_run_checkpoint_ready.assert_awaited_once_with(
            checkpoint_frame
        )

    def test_current_dispatch_acknowledgements_are_idempotent(self):
        run = self._run(
            Run.Status.STREAMING,
            timedelta(seconds=10),
            timedelta(seconds=10),
        )
        dispatch_id = uuid.uuid4()
        run.execution.status = RunExecution.Status.DISPATCHED
        run.execution.dispatch_id = dispatch_id
        run.execution.save(update_fields=["status", "dispatch_id"])

        self.assertTrue(
            acknowledge_run_admitted(
                run.uuid,
                dispatch_id,
                self.lensnode.uuid,
            )
        )
        self.assertTrue(
            acknowledge_run_checkpoint_ready(
                run.uuid,
                dispatch_id,
                self.lensnode.uuid,
            )
        )
        run.execution.refresh_from_db()
        admitted_at = run.execution.admitted_at
        checkpoint_ready_at = run.execution.checkpoint_ready_at

        self.assertTrue(
            acknowledge_run_admitted(
                run.uuid,
                dispatch_id,
                self.lensnode.uuid,
            )
        )
        self.assertTrue(
            acknowledge_run_checkpoint_ready(
                run.uuid,
                dispatch_id,
                self.lensnode.uuid,
            )
        )

        run.execution.refresh_from_db()
        self.assertEqual(
            run.execution.status,
            RunExecution.Status.RUNNING,
        )
        self.assertEqual(run.execution.admitted_at, admitted_at)
        self.assertEqual(
            run.execution.checkpoint_ready_at,
            checkpoint_ready_at,
        )

    def test_checkpoint_ready_before_admission_records_both_states(self):
        run = self._run(
            Run.Status.STREAMING,
            timedelta(seconds=10),
            timedelta(seconds=10),
        )
        dispatch_id = uuid.uuid4()
        run.resume_by = timezone.now() + timedelta(hours=1)
        run.save(update_fields=["resume_by"])
        run.execution.status = RunExecution.Status.DISPATCHED
        run.execution.dispatch_id = dispatch_id
        run.execution.save(update_fields=["status", "dispatch_id"])

        acknowledged = acknowledge_run_checkpoint_ready(
            run.uuid,
            dispatch_id,
            self.lensnode.uuid,
        )

        run.refresh_from_db()
        run.execution.refresh_from_db()
        self.assertTrue(acknowledged)
        self.assertEqual(
            run.execution.status,
            RunExecution.Status.RUNNING,
        )
        self.assertIsNotNone(run.execution.admitted_at)
        self.assertIsNotNone(run.execution.checkpoint_ready_at)
        self.assertIsNone(run.resume_by)

    def test_dispatch_acknowledgement_from_wrong_lensnode_is_ignored(self):
        run = self._run(
            Run.Status.STREAMING,
            timedelta(seconds=10),
            timedelta(seconds=10),
        )
        dispatch_id = uuid.uuid4()
        run.execution.status = RunExecution.Status.DISPATCHED
        run.execution.dispatch_id = dispatch_id
        run.execution.save(update_fields=["status", "dispatch_id"])
        other_lensnode = LensNode.objects.create(
            name="Other LensNode",
            status=LensNode.Status.ONLINE,
            enrollment_status=LensNode.EnrollmentStatus.APPROVED,
            workspace_path="/other-workspace",
        )

        acknowledged = acknowledge_run_checkpoint_ready(
            run.uuid,
            dispatch_id,
            other_lensnode.uuid,
        )

        run.execution.refresh_from_db()
        self.assertFalse(acknowledged)
        self.assertEqual(
            run.execution.status,
            RunExecution.Status.DISPATCHED,
        )
        self.assertIsNone(run.execution.admitted_at)
        self.assertIsNone(run.execution.checkpoint_ready_at)

    def test_first_resume_event_acknowledges_node_admission(self):
        run = self._run(
            Run.Status.STREAMING,
            timedelta(seconds=10),
            timedelta(seconds=10),
        )
        run.resume_by = timezone.now() + timedelta(hours=1)
        run.save(update_fields=["resume_by"])

        record_lensnode_run_event(
            run.uuid,
            "retrieval",
            "running",
            {"message": "accepted"},
        )

        run.refresh_from_db()
        self.assertIsNone(run.resume_by)

    def test_idle_reaper_fails_silent_run(self):
        run = self._run(
            Run.Status.STREAMING,
            timedelta(minutes=10),
            timedelta(minutes=10),
        )

        lensnode_cleanup_task()

        run.refresh_from_db()
        self.assertEqual(run.status, Run.Status.FAILED)
        self.assertEqual(run.error, "LENS_RUN_TIMEOUT")

    def test_idle_reaper_fails_queued_execution_snapshot(self):
        run = self._run(
            Run.Status.RUNNING,
            timedelta(minutes=10),
            timedelta(minutes=10),
        )
        self.assertEqual(run.execution.status, RunExecution.Status.QUEUED)

        lensnode_cleanup_task()

        run.refresh_from_db()
        run.execution.refresh_from_db()
        self.assertEqual(run.status, Run.Status.FAILED)
        self.assertEqual(run.execution.status, RunExecution.Status.FAILED)
        self.assertIsNotNone(run.execution.finished_at)

    def test_idle_reaper_keeps_long_but_active_run(self):
        # Long-running (90 min, past the old 1h cap) but still streaming
        # output a few seconds ago: it must survive on activity alone.
        run = self._run(
            Run.Status.STREAMING,
            timedelta(minutes=90),
            timedelta(seconds=5),
        )

        lensnode_cleanup_task()

        run.refresh_from_db()
        self.assertEqual(run.status, Run.Status.STREAMING)

    def test_reconcile_schedules_confirmation_instead_of_failing_inline(self):
        # LensNode redelivers run_done at-least-once through a durable
        # outbox, so a run absent from a fresh hello's active list may just
        # be finishing, not dead — reconcile must not fail it on the spot.
        run = self._run(
            Run.Status.STREAMING,
            timedelta(minutes=5),
            timedelta(minutes=5),
        )
        run.execution.status = RunExecution.Status.RUNNING
        run.execution.save(update_fields=["status"])

        with patch(
            "lens.tasks.confirm_reconcile_orphan.apply_async"
        ) as apply_async:
            count = reconcile_lensnode_active_runs(self.lensnode.uuid, [])

        run.refresh_from_db()
        self.assertEqual(count, 1)
        self.assertEqual(run.status, Run.Status.STREAMING)
        self.assertTrue(apply_async.called)
        kwargs = apply_async.call_args.kwargs
        self.assertEqual(kwargs["args"][0], str(run.uuid))
        self.assertIn("countdown", kwargs)

    def test_reconcile_skips_run_already_awaiting_resume(self):
        run = self._run(
            Run.Status.RUNNING,
            timedelta(minutes=5),
            timedelta(minutes=5),
        )
        run.resume_by = timezone.now() + timedelta(hours=1)
        run.save(update_fields=["resume_by"])

        with patch(
            "lens.tasks.confirm_reconcile_orphan.apply_async"
        ) as apply_async:
            count = reconcile_lensnode_active_runs(self.lensnode.uuid, [])

        self.assertEqual(count, 0)
        apply_async.assert_not_called()

    def test_reconcile_keeps_claimed_and_defers_fresh_run(self):
        claimed = self._run(
            Run.Status.STREAMING,
            timedelta(minutes=5),
            timedelta(minutes=1),
        )
        fresh = self._run(
            Run.Status.RUNNING,
            timedelta(seconds=10),
            timedelta(seconds=10),
        )
        for run in (claimed, fresh):
            run.execution.status = RunExecution.Status.RUNNING
            run.execution.save(update_fields=["status"])

        with patch(
            "lens.tasks.confirm_reconcile_orphan.apply_async"
        ) as apply_async:
            count = reconcile_lensnode_active_runs(
                self.lensnode.uuid, [str(claimed.uuid)]
            )

        claimed.refresh_from_db()
        fresh.refresh_from_db()
        self.assertEqual(count, 1)
        self.assertEqual(claimed.status, Run.Status.STREAMING)
        self.assertEqual(fresh.status, Run.Status.RUNNING)
        apply_async.assert_called_once()
        kwargs = apply_async.call_args.kwargs
        self.assertEqual(kwargs["args"], [str(fresh.uuid)])
        self.assertGreater(kwargs["countdown"], 40)

    def test_reconcile_skips_fresh_run_on_legacy_node(self):
        self.lensnode.labels = {}
        self.lensnode.save(update_fields=["labels"])
        fresh = self._run(
            Run.Status.RUNNING,
            timedelta(seconds=10),
            timedelta(seconds=10),
        )
        fresh.execution.status = RunExecution.Status.RUNNING
        fresh.execution.save(update_fields=["status"])

        with patch(
            "lens.tasks.confirm_reconcile_orphan.apply_async"
        ) as apply_async:
            count = reconcile_lensnode_active_runs(self.lensnode.uuid, [])

        fresh.refresh_from_db()
        self.assertEqual(count, 0)
        self.assertEqual(fresh.status, Run.Status.RUNNING)
        apply_async.assert_not_called()

    def test_reconcile_skips_run_that_has_not_been_dispatched(self):
        run = self._run(
            Run.Status.RUNNING,
            timedelta(minutes=5),
            timedelta(minutes=5),
        )

        with patch(
            "lens.tasks.confirm_reconcile_orphan.apply_async"
        ) as apply_async:
            count = reconcile_lensnode_active_runs(self.lensnode.uuid, [])

        self.assertEqual(run.execution.status, RunExecution.Status.QUEUED)
        self.assertEqual(count, 0)
        apply_async.assert_not_called()

    def test_confirm_reconcile_orphan_parks_still_running_run(self):
        run = self._run(
            Run.Status.STREAMING,
            timedelta(minutes=5),
            timedelta(minutes=5),
        )
        self.lensnode.status = LensNode.Status.OFFLINE
        self.lensnode.save(update_fields=["status"])
        self._mark_execution_running(run)

        confirm_reconcile_orphan(str(run.uuid))

        run.refresh_from_db()
        self.assertEqual(run.status, Run.Status.RUNNING)
        self.assertIsNotNone(run.resume_by)

    def test_confirm_reconcile_orphan_finalizes_running_steps(self):
        run = self._run(
            Run.Status.STREAMING,
            timedelta(minutes=5),
            timedelta(minutes=5),
        )
        step = RunStep.objects.create(
            run=run,
            step_type=RunStep.StepType.GENERAL_CHAT,
            sequence=1,
            status=RunStep.Status.RUNNING,
        )
        self._mark_execution_running(run)

        confirm_reconcile_orphan(str(run.uuid))

        step.refresh_from_db()
        self.assertEqual(step.status, RunStep.Status.FAILED)

    def test_confirm_reconcile_orphan_skips_recent_resume_activity(self):
        run = self._run(
            Run.Status.STREAMING,
            timedelta(minutes=5),
            timedelta(seconds=1),
        )

        confirm_reconcile_orphan(str(run.uuid))

        run.refresh_from_db()
        self.assertEqual(run.status, Run.Status.STREAMING)
        self.assertIsNone(run.resume_by)

    def test_mark_active_runs_awaiting_resume_finalizes_steps(self):
        run = self._run(
            Run.Status.RUNNING,
            timedelta(minutes=5),
            timedelta(minutes=5),
        )
        step = RunStep.objects.create(
            run=run,
            step_type=RunStep.StepType.GENERAL_CHAT,
            sequence=1,
            status=RunStep.Status.RUNNING,
        )

        mark_active_runs_awaiting_resume(self.lensnode.uuid)

        run.refresh_from_db()
        step.refresh_from_db()
        self.assertEqual(run.status, Run.Status.RUNNING)
        self.assertIsNotNone(run.resume_by)
        self.assertEqual(step.status, RunStep.Status.FAILED)
        self.expiration_apply_async.assert_called_once()

    def test_mark_active_runs_awaiting_resume_skips_terminal_runs(self):
        done = self._run(
            Run.Status.DONE,
            timedelta(minutes=5),
            timedelta(minutes=5),
        )

        mark_active_runs_awaiting_resume(self.lensnode.uuid)

        done.refresh_from_db()
        self.assertEqual(done.status, Run.Status.DONE)

    def test_resume_awaiting_runs_redispatch(self):
        run = self._run(
            Run.Status.RUNNING,
            timedelta(minutes=5),
            timedelta(minutes=5),
        )
        run.resume_by = timezone.now() + timedelta(hours=1)
        run.save(update_fields=["resume_by"])
        self._mark_execution_running(run)

        with patch(
            "lens.services.dispatch_run_to_lensnode"
        ) as dispatch:
            count = resume_awaiting_runs_for_lensnode(
                self.lensnode.uuid
            )

        run.refresh_from_db()
        self.assertEqual(count, 1)
        self.assertEqual(dispatch.call_count, 1)
        args, kwargs = dispatch.call_args
        self.assertEqual(str(args[0].uuid), str(run.uuid))
        self.assertEqual(kwargs["resume"], True)
        self.assertEqual(run.status, Run.Status.STREAMING)
        self.assertIsNotNone(run.resume_by)

    def test_manual_resume_redispatches_only_the_selected_run(self):
        selected = self._run(
            Run.Status.RUNNING,
            timedelta(minutes=5),
            timedelta(minutes=5),
        )
        selected.resume_by = timezone.now() + timedelta(hours=1)
        selected.save(update_fields=["resume_by"])
        self._mark_execution_running(selected)
        other = self._run(
            Run.Status.RUNNING,
            timedelta(minutes=5),
            timedelta(minutes=5),
        )
        other.resume_by = timezone.now() + timedelta(hours=1)
        other.save(update_fields=["resume_by"])
        self._mark_execution_running(other)

        with patch("lens.services.dispatch_run_to_lensnode") as dispatch:
            resumed = resume_awaiting_run(selected.pk)

        selected.refresh_from_db()
        other.refresh_from_db()
        self.assertTrue(resumed)
        self.assertEqual(selected.status, Run.Status.STREAMING)
        self.assertEqual(other.status, Run.Status.RUNNING)
        dispatch.assert_called_once()
        self.assertEqual(dispatch.call_args.args[0].pk, selected.pk)

    def test_manual_resume_rejects_an_offline_node(self):
        run = self._run(
            Run.Status.RUNNING,
            timedelta(minutes=5),
            timedelta(minutes=5),
        )
        run.resume_by = timezone.now() + timedelta(hours=1)
        run.save(update_fields=["resume_by"])
        self._mark_execution_running(run)
        self.lensnode.status = LensNode.Status.OFFLINE
        self.lensnode.save(update_fields=["status"])

        with patch("lens.services.dispatch_run_to_lensnode") as dispatch:
            resumed = resume_awaiting_run(run.pk)

        self.assertFalse(resumed)
        dispatch.assert_not_called()

    def test_never_admitted_run_is_requeued_through_normal_dispatch_once(self):
        run = self._run(
            Run.Status.RUNNING,
            timedelta(minutes=5),
            timedelta(minutes=5),
        )
        run.resume_by = timezone.now() + timedelta(hours=1)
        run.save(update_fields=["resume_by"])
        run.execution.status = RunExecution.Status.DISPATCHED
        run.execution.dispatch_id = uuid.uuid4()
        run.execution.save(update_fields=["status", "dispatch_id"])

        with (
            patch(
                "lens.services.get_run_document_expectation",
                return_value=2,
            ),
            patch("lens.tasks.enqueue_answer_run_task") as enqueue,
        ):
            first = resume_awaiting_runs_for_lensnode(self.lensnode.uuid)
            second = resume_awaiting_runs_for_lensnode(self.lensnode.uuid)

        run.refresh_from_db()
        run.execution.refresh_from_db()
        self.assertEqual((first, second), (1, 0))
        self.assertEqual(run.status, Run.Status.QUEUED)
        self.assertIsNone(run.resume_by)
        self.assertEqual(
            run.execution.status,
            RunExecution.Status.QUEUED,
        )
        self.assertIsNone(run.execution.dispatch_id)
        enqueue.assert_called_once_with(run.uuid, 2)

    def test_admitted_run_without_checkpoint_readiness_keeps_awaiting(self):
        self.lensnode.labels["run_admission_checkpoint_v1"] = True
        self.lensnode.save(update_fields=["labels"])
        run = self._run(
            Run.Status.RUNNING,
            timedelta(minutes=5),
            timedelta(minutes=5),
        )
        run.resume_by = timezone.now() + timedelta(hours=1)
        run.save(update_fields=["resume_by"])
        run.execution.status = RunExecution.Status.RUNNING
        run.execution.dispatch_id = uuid.uuid4()
        run.execution.admitted_at = timezone.now()
        run.execution.save(
            update_fields=["status", "dispatch_id", "admitted_at"]
        )

        with patch("lens.services.dispatch_run_to_lensnode") as dispatch:
            count = resume_awaiting_runs_for_lensnode(self.lensnode.uuid)

        run.refresh_from_db()
        run.execution.refresh_from_db()
        self.assertEqual(count, 0)
        self.assertEqual(run.status, Run.Status.RUNNING)
        self.assertIsNotNone(run.resume_by)
        self.assertEqual(
            run.execution.status,
            RunExecution.Status.RUNNING,
        )
        dispatch.assert_not_called()

    def test_checkpoint_ready_run_resumes_with_fresh_dispatch_id(self):
        self.lensnode.labels["run_admission_checkpoint_v1"] = True
        self.lensnode.save(update_fields=["labels"])
        run = self._run(
            Run.Status.RUNNING,
            timedelta(minutes=5),
            timedelta(minutes=5),
        )
        old_dispatch_id = uuid.uuid4()
        ready_at = timezone.now() - timedelta(minutes=1)
        run.resume_by = timezone.now() + timedelta(hours=1)
        run.save(update_fields=["resume_by"])
        run.execution.status = RunExecution.Status.RUNNING
        run.execution.dispatch_id = old_dispatch_id
        run.execution.admitted_at = ready_at
        run.execution.checkpoint_ready_at = ready_at
        run.execution.save(
            update_fields=[
                "status",
                "dispatch_id",
                "admitted_at",
                "checkpoint_ready_at",
            ]
        )

        with patch(
            "lens.services.dispatch_run_to_lensnode"
        ) as dispatch:
            count = resume_awaiting_runs_for_lensnode(self.lensnode.uuid)

        run.execution.refresh_from_db()
        self.assertEqual(count, 1)
        self.assertNotEqual(run.execution.dispatch_id, old_dispatch_id)
        self.assertEqual(
            dispatch.call_args.kwargs["dispatch_id"],
            run.execution.dispatch_id,
        )
        self.assertTrue(dispatch.call_args.kwargs["resume"])

    def test_resume_skips_run_reported_active_by_reconnected_node(self):
        run = self._run(
            Run.Status.RUNNING,
            timedelta(minutes=5),
            timedelta(minutes=5),
        )
        run.resume_by = timezone.now() + timedelta(hours=1)
        run.save(update_fields=["resume_by"])

        with patch(
            "lens.services.dispatch_run_to_lensnode"
        ) as dispatch:
            count = resume_awaiting_runs_for_lensnode(
                self.lensnode.uuid,
                [str(run.uuid)],
            )

        run.refresh_from_db()
        self.assertEqual(count, 0)
        self.assertEqual(run.status, Run.Status.RUNNING)
        self.assertIsNotNone(run.resume_by)
        dispatch.assert_not_called()

    def test_resume_claim_prevents_reentrant_duplicate_dispatch(self):
        run = self._run(
            Run.Status.RUNNING,
            timedelta(minutes=5),
            timedelta(minutes=5),
        )
        run.resume_by = timezone.now() + timedelta(hours=1)
        run.save(update_fields=["resume_by"])
        self._mark_execution_running(run)
        nested_counts = []

        def dispatch_once(*args, **kwargs):
            del args, kwargs
            if not nested_counts:
                nested_counts.append(
                    resume_awaiting_runs_for_lensnode(self.lensnode.uuid)
                )

        with patch(
            "lens.services.dispatch_run_to_lensnode",
            side_effect=dispatch_once,
        ) as dispatch:
            count = resume_awaiting_runs_for_lensnode(
                self.lensnode.uuid
            )

        self.assertEqual(count, 1)
        self.assertEqual(nested_counts, [0])
        self.assertEqual(dispatch.call_count, 1)

    def test_new_node_report_retries_unacknowledged_resume_claim(self):
        run = self._run(
            Run.Status.STREAMING,
            timedelta(minutes=5),
            timedelta(minutes=1),
        )
        run.resume_by = timezone.now() + timedelta(hours=1)
        run.save(update_fields=["resume_by"])
        self._mark_execution_running(run)
        LensNode.objects.filter(pk=self.lensnode.pk).update(
            updated_at=timezone.now()
        )

        with patch(
            "lens.services.dispatch_run_to_lensnode"
        ) as dispatch:
            count = resume_awaiting_runs_for_lensnode(
                self.lensnode.uuid
            )

        self.assertEqual(count, 1)
        dispatch.assert_called_once()

    def test_confirm_orphan_resumes_when_node_is_still_online(self):
        run = self._run(
            Run.Status.STREAMING,
            timedelta(minutes=5),
            timedelta(minutes=5),
        )
        self._mark_execution_running(run)

        with patch(
            "lens.services.dispatch_run_to_lensnode"
        ) as dispatch:
            confirm_reconcile_orphan(str(run.uuid))

        run.refresh_from_db()
        self.assertEqual(dispatch.call_count, 1)
        self.assertEqual(run.status, Run.Status.STREAMING)
        self.assertIsNotNone(run.resume_by)

    def test_resume_awaiting_runs_keeps_run_parked_on_failure(self):
        run = self._run(
            Run.Status.RUNNING,
            timedelta(minutes=5),
            timedelta(minutes=5),
        )
        run.resume_by = timezone.now() + timedelta(hours=1)
        run.save(update_fields=["resume_by"])
        self._mark_execution_running(run)

        with patch(
            "lens.services.dispatch_run_to_lensnode",
            side_effect=Exception("boom"),
        ):
            count = resume_awaiting_runs_for_lensnode(
                self.lensnode.uuid
            )

        run.refresh_from_db()
        self.assertEqual(count, 0)
        self.assertEqual(run.status, Run.Status.RUNNING)

    def test_resume_fails_safely_when_node_lacks_capability(self):
        run = self._run(
            Run.Status.RUNNING,
            timedelta(minutes=5),
            timedelta(minutes=5),
        )
        run.resume_by = timezone.now() + timedelta(hours=1)
        run.save(update_fields=["resume_by"])
        self._mark_execution_running(run)
        self.lensnode.labels = {}
        self.lensnode.save(update_fields=["labels"])

        with patch("lens.services.dispatch_run_to_lensnode") as dispatch:
            count = resume_awaiting_runs_for_lensnode(self.lensnode.uuid)

        run.refresh_from_db()
        self.assertEqual(count, 0)
        self.assertEqual(run.status, Run.Status.FAILED)
        self.assertEqual(run.error, "LENSNODE_RESUME_UNSUPPORTED")
        self.assertIsNone(run.resume_by)
        dispatch.assert_not_called()

    def test_resume_busy_response_keeps_run_awaiting(self):
        run = self._run(
            Run.Status.STREAMING,
            timedelta(hours=1),
            timedelta(minutes=5),
        )
        deadline = timezone.now() + timedelta(hours=1)
        run.resume_by = deadline
        run.save(update_fields=["resume_by"])

        with patch(
            "lens.tasks.retry_awaiting_run_resume.apply_async"
        ) as retry:
            record_lensnode_run_event(
                run.uuid,
                "retrieval",
                "failed",
                {"error": "LENSNODE_BUSY"},
            )
            finish_lensnode_run(
                run.uuid,
                Run.Status.FAILED,
                error="LENSNODE_BUSY",
            )

        run.refresh_from_db()
        self.assertEqual(run.status, Run.Status.RUNNING)
        self.assertEqual(run.resume_by, deadline)
        retry.assert_called_once_with(
            args=[str(run.uuid)],
            countdown=5,
        )

    def test_resume_draining_response_keeps_run_awaiting(self):
        run = self._run(
            Run.Status.STREAMING,
            timedelta(hours=1),
            timedelta(minutes=5),
        )
        deadline = timezone.now() + timedelta(hours=1)
        run.resume_by = deadline
        run.save(update_fields=["resume_by"])

        with patch(
            "lens.tasks.retry_awaiting_run_resume.apply_async"
        ) as retry:
            finish_lensnode_run(
                run.uuid,
                Run.Status.FAILED,
                error="LENSNODE_DRAINING",
            )

        run.refresh_from_db()
        self.assertEqual(run.status, Run.Status.RUNNING)
        self.assertEqual(run.resume_by, deadline)
        retry.assert_called_once_with(
            args=[str(run.uuid)],
            countdown=5,
        )

    def test_buffered_terminal_frame_clears_resume_deadline(self):
        run = self._run(
            Run.Status.RUNNING,
            timedelta(minutes=5),
            timedelta(minutes=5),
        )
        run.resume_by = timezone.now() + timedelta(hours=1)
        run.save(update_fields=["resume_by"])

        finish_lensnode_run(run.uuid, Run.Status.DONE)

        run.refresh_from_db()
        self.assertEqual(run.status, Run.Status.DONE)
        self.assertIsNone(run.resume_by)

    def test_busy_resume_result_is_not_acknowledged_as_terminal(self):
        run = self._run(
            Run.Status.RUNNING,
            timedelta(hours=1),
            timedelta(minutes=5),
        )
        consumer = LensNodeConsumer()
        consumer.send_json = AsyncMock()

        with patch(
            "lens.consumers.finish_lensnode_run",
            return_value=run,
        ):
            async_to_sync(consumer._handle_run_done)(
                {
                    "run_uuid": str(run.uuid),
                    "status": Run.Status.FAILED,
                    "error": "LENSNODE_BUSY",
                }
            )

        consumer.send_json.assert_not_awaited()

    def test_cancelled_completion_race_is_acknowledged(self):
        run = self._run(
            Run.Status.CANCELLED,
            timedelta(minutes=5),
            timedelta(minutes=5),
        )
        consumer = LensNodeConsumer()
        consumer.send_json = AsyncMock()

        with patch(
            "lens.consumers.finish_lensnode_run",
            return_value=run,
        ):
            async_to_sync(consumer._handle_run_done)(
                {
                    "run_uuid": str(run.uuid),
                    "status": Run.Status.DONE,
                }
            )

        consumer.send_json.assert_awaited_once_with(
            {
                "type": "run_done_ack",
                "run_uuid": str(run.uuid),
            }
        )

    def test_terminal_frame_atomically_persists_long_final_output(self):
        run = self._run(
            Run.Status.STREAMING,
            timedelta(minutes=5),
            timedelta(minutes=5),
        )
        final_content = "完整报告" * 2500
        consumer = LensNodeConsumer()
        consumer.send_json = AsyncMock()

        async_to_sync(consumer._handle_run_done)(
            {
                "run_uuid": str(run.uuid),
                "status": Run.Status.DONE,
                "outcome": Run.Outcome.COMPLETED,
                "final_content": final_content,
                "citations": [],
                "planned_evidence": {"sufficient": True},
            }
        )

        run.refresh_from_db()
        self.assertEqual(run.output_message.content, final_content)
        self.assertEqual(run.planned_evidence, {"sufficient": True})
        consumer.send_json.assert_awaited_once_with(
            {
                "type": "run_done_ack",
                "run_uuid": str(run.uuid),
            }
        )

    def test_idle_reaper_expires_stale_awaiting_resume(self):
        run = self._run(
            Run.Status.RUNNING,
            timedelta(hours=25),
            timedelta(hours=25),
        )
        run.resume_by = timezone.now() - timedelta(hours=1)
        run.save(update_fields=["resume_by"])
        step = RunStep.objects.create(
            run=run,
            step_type=RunStep.StepType.GENERAL_CHAT,
            sequence=1,
            status=RunStep.Status.RUNNING,
        )

        lensnode_cleanup_task()

        run.refresh_from_db()
        step.refresh_from_db()
        self.assertEqual(run.status, Run.Status.FAILED)
        self.assertEqual(run.error, "LENSNODE_RESUME_EXPIRED")
        self.assertEqual(step.status, RunStep.Status.FAILED)

    def test_deadline_task_expires_one_awaiting_resume_run(self):
        run = self._run(
            Run.Status.RUNNING,
            timedelta(hours=1),
            timedelta(hours=1),
        )
        run.resume_by = timezone.now() - timedelta(seconds=1)
        run.save(update_fields=["resume_by"])

        count = expire_awaiting_run(str(run.uuid))

        run.refresh_from_db()
        run.execution.refresh_from_db()
        self.assertEqual(count, 1)
        self.assertEqual(run.status, Run.Status.FAILED)
        self.assertEqual(run.error, "LENSNODE_RESUME_EXPIRED")
        self.assertIsNone(run.resume_by)
        self.assertEqual(run.execution.status, RunExecution.Status.FAILED)

    def test_confirm_reconcile_orphan_noops_if_already_terminal(self):
        # The normal completion path (a late but durably-delivered run_done)
        # won the race and finished the run before this fired.
        run = self._run(
            Run.Status.DONE,
            timedelta(minutes=5),
            timedelta(minutes=5),
        )

        confirm_reconcile_orphan(str(run.uuid))

        run.refresh_from_db()
        self.assertEqual(run.status, Run.Status.DONE)
        self.assertEqual(run.error, "")

    def test_confirm_reconcile_orphan_noops_for_unknown_run(self):
        # Must not raise for a run_uuid that no longer exists.
        confirm_reconcile_orphan(str(uuid.uuid4()))
