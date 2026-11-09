import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from lens.evaluation.engine.batch import (
    advance_evaluation_run,
    cancel_evaluation_run,
    create_evaluation_run,
    finalize_case_result,
    retry_failed_cases,
    start_evaluation_run,
)
from lens.models import (
    Assistant,
    EvaluationCase,
    EvaluationDataset,
    EvaluationResult,
    EvaluationRun,
    GlobalSetting,
    LensNode,
    Run,
    RunTraceEvent,
    Session,
)

User = get_user_model()


class EvaluationEngineTests(TestCase):
    def setUp(self):
        GlobalSetting.objects.create(key="evaluation.execution_pool", value="evaluation")
        self.user = User.objects.create_user(username="evaluation-operator")
        self.node = LensNode.objects.create(
            name="Evaluation node",
            pool=LensNode.Pool.EVALUATION,
            status=LensNode.Status.ONLINE,
            enrollment_status=LensNode.EnrollmentStatus.APPROVED,
            evaluation_concurrency_limit=1,
        )
        self.assistant = Assistant.objects.create(
            name="Evaluation assistant",
            slug="evaluation-assistant",
            lensnode=self.node,
            selected_task="knowledge_qa",
            agent_model_ref=uuid.uuid4(),
        )
        self.dataset = EvaluationDataset.objects.create(
            name="Smoke",
            assistant=self.assistant,
            created_by=self.user,
            status=EvaluationDataset.Status.ACTIVE,
        )
        self.cases = [
            EvaluationCase.objects.create(
                dataset=self.dataset,
                input=f"Question {index}",
                expected_sources=["report.md"],
            )
            for index in range(2)
        ]

    def _start(self):
        evaluation_run = create_evaluation_run(
            dataset=self.dataset,
            requested_by=self.user,
        )
        with patch("lens.evaluation.engine.batch._enqueue_run") as enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                start_evaluation_run(evaluation_run.uuid)
        evaluation_run.refresh_from_db()
        return evaluation_run, enqueue

    def _finish_run(self, run, *, passed=True):
        run.status = Run.Status.DONE
        run.outcome = Run.Outcome.COMPLETED
        run.termination_detail = {}
        run.started_at = timezone.now() - timedelta(seconds=30)
        run.finished_at = timezone.now()
        run.citations = [
            {
                "id": "c1",
                "path": "report.md",
                "start_line": 1,
                "end_line": 5,
            }
        ]
        run.output_message.content = "Answer"
        run.output_message.save(update_fields=["content"])
        run.save()
        RunTraceEvent.objects.create(
            run=run,
            event_id=uuid.uuid4(),
            sequence=1,
            event_type="model.completed",
            timestamp=timezone.now(),
            payload={
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "total_tokens": 120,
                    "cached_tokens": 0,
                },
                "ttft_ms": 1200,
                "duration_ms": 3000,
            },
        )
        RunTraceEvent.objects.create(
            run=run,
            event_id=uuid.uuid4(),
            sequence=2,
            event_type="tool.completed",
            timestamp=timezone.now(),
            call_id="tool-1",
            payload={"result": {"status": "success"}},
        )

    def test_start_creates_results_and_respects_node_capacity(self):
        evaluation_run, enqueue = self._start()

        self.assertEqual(evaluation_run.results.count(), 2)
        self.assertEqual(
            Run.objects.filter(source=Run.Source.EVALUATION).count(),
            1,
        )
        self.assertEqual(enqueue.call_count, 1)
        self.assertEqual(evaluation_run.status, EvaluationRun.Status.RUNNING)

    def test_case_finalize_scores_result_and_dispatches_next_case(self):
        evaluation_run, _enqueue = self._start()
        first = Run.objects.filter(source=Run.Source.EVALUATION).get()
        self._finish_run(first)

        with patch("lens.evaluation.engine.batch._enqueue_run") as enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                finalize_case_result(first.uuid)

        result = EvaluationResult.objects.get(
            evaluation_run=evaluation_run,
            source_run_uuid=first.uuid,
        )
        self.assertIsNone(result.passed)
        self.assertIn("execution_success", result.scores)
        self.assertEqual(result.scores["execution_success"]["score"], 1.0)
        self.assertEqual(result.execution_metrics["total_tokens"], 120)
        self.assertEqual(result.execution_metrics["scoring_version"], "3")
        self.assertEqual(result.output_snapshot, "Answer")
        self.assertEqual(enqueue.call_count, 1)

    def test_case_with_expected_answer_passes(self):
        EvaluationCase.objects.filter(dataset=self.dataset).update(
            expected_answer="Answer",
            config={"answer_match_mode": "exact"},
        )
        evaluation_run, _enqueue = self._start()
        run = Run.objects.get(source=Run.Source.EVALUATION)
        self._finish_run(run)
        with patch("lens.evaluation.engine.batch._enqueue_run"):
            with self.captureOnCommitCallbacks(execute=True):
                finalize_case_result(run.uuid)
        result = EvaluationResult.objects.get(evaluation_run=evaluation_run, source_run_uuid=run.uuid)
        self.assertTrue(result.passed)
        self.assertTrue(result.scores["answer_correctness"]["passed"])

    def test_batch_completes_and_aggregates_after_every_case(self):
        EvaluationCase.objects.filter(dataset=self.dataset).update(
            expected_answer="Answer",
            config={"answer_match_mode": "exact"},
        )
        evaluation_run, _enqueue = self._start()
        for _index in range(2):
            run = Run.objects.filter(
                source=Run.Source.EVALUATION,
                status__in=[Run.Status.QUEUED, Run.Status.RUNNING],
            ).first()
            self._finish_run(run)
            with patch("lens.evaluation.engine.batch._enqueue_run"):
                finalize_case_result(run.uuid)

        evaluation_run.refresh_from_db()
        self.assertEqual(evaluation_run.status, EvaluationRun.Status.COMPLETED)
        self.assertEqual(evaluation_run.completed_cases, 2)
        self.assertEqual(evaluation_run.passed_cases, 2)
        self.assertIsNotNone(evaluation_run.aggregate_score)
        self.assertEqual(evaluation_run.coverage["ratio"], 0.8)
        self.assertEqual(evaluation_run.coverage["scored_metrics"], 4)

    def test_cancel_marks_active_case_runs_cancelled(self):
        evaluation_run, _enqueue = self._start()

        with patch("lens.services.cancel_run_on_lensnode"):
            cancel_evaluation_run(evaluation_run.uuid)

        evaluation_run.refresh_from_db()
        self.assertEqual(evaluation_run.status, EvaluationRun.Status.CANCELLED)
        self.assertTrue(
            Run.objects.filter(
                source=Run.Source.EVALUATION,
                status=Run.Status.CANCELLED,
            ).exists()
        )

    def test_retry_resets_failed_cases_and_requeues_batch(self):
        evaluation_run, _enqueue = self._start()
        first = Run.objects.filter(source=Run.Source.EVALUATION).get()
        first.status = Run.Status.FAILED
        first.error = "LENS_RUN_FAILED"
        first.finished_at = timezone.now()
        first.save(update_fields=["status", "error", "finished_at", "updated_at"])
        with patch("lens.evaluation.engine.batch._enqueue_run"):
            finalize_case_result(first.uuid)
        result = EvaluationResult.objects.get(
            evaluation_run=evaluation_run,
            source_run_uuid=first.uuid,
        )
        self.assertIs(result.passed, False)
        issue_metrics = {issue["metric"] for issue in result.issues}
        self.assertIn("execution_success", issue_metrics)
        evaluation_run.status = EvaluationRun.Status.COMPLETED
        evaluation_run.save(update_fields=["status", "updated_at"])

        retried = retry_failed_cases(evaluation_run.uuid)

        self.assertEqual(retried, 2)
        result.refresh_from_db()
        self.assertIsNone(result.source_run)
        self.assertIsNone(result.passed)
        evaluation_run.refresh_from_db()
        self.assertEqual(evaluation_run.status, EvaluationRun.Status.QUEUED)

    def test_retry_dispatches_a_new_run_for_the_same_case(self):
        self.cases[1].delete()
        evaluation_run, _ = self._start()
        first = Run.objects.get(source=Run.Source.EVALUATION)
        first.status = Run.Status.FAILED
        first.save(update_fields=["status"])
        finalize_case_result(first.uuid)

        self.assertEqual(retry_failed_cases(evaluation_run.uuid), 1)
        with patch("lens.evaluation.engine.batch._enqueue_run"):
            start_evaluation_run(evaluation_run.uuid)

        result = evaluation_run.results.get()
        self.assertNotEqual(result.source_run_uuid, first.uuid)
        self.assertEqual(result.source_run.status, Run.Status.QUEUED)

    def test_retry_recovers_cases_never_dispatched_after_node_timeout(self):
        self.node.status = LensNode.Status.OFFLINE
        self.node.save(update_fields=["status"])
        evaluation_run = create_evaluation_run(dataset=self.dataset, requested_by=self.user)
        start_evaluation_run(evaluation_run.uuid)
        evaluation_run.refresh_from_db()
        snapshot = dict(evaluation_run.config_snapshot)
        snapshot["waiting"]["since"] = (timezone.now() - timedelta(hours=2)).isoformat()
        evaluation_run.config_snapshot = snapshot
        evaluation_run.save(update_fields=["config_snapshot"])
        advance_evaluation_run(evaluation_run.uuid)
        evaluation_run.refresh_from_db()
        self.assertEqual(evaluation_run.status, EvaluationRun.Status.FAILED)

        self.node.status = LensNode.Status.ONLINE
        self.node.save(update_fields=["status"])
        self.assertEqual(retry_failed_cases(evaluation_run.uuid), 2)
        with patch("lens.evaluation.engine.batch._enqueue_run"):
            start_evaluation_run(evaluation_run.uuid)
        self.assertEqual(evaluation_run.results.filter(source_run__isnull=False).count(), 1)

    def test_node_timeout_cancels_queued_runs(self):
        evaluation_run, _ = self._start()
        run = Run.objects.get(source=Run.Source.EVALUATION)
        self.node.status = LensNode.Status.OFFLINE
        self.node.save(update_fields=["status"])
        advance_evaluation_run(evaluation_run.uuid)
        evaluation_run.refresh_from_db()
        snapshot = dict(evaluation_run.config_snapshot)
        snapshot["waiting"]["since"] = (timezone.now() - timedelta(hours=2)).isoformat()
        evaluation_run.config_snapshot = snapshot
        evaluation_run.save(update_fields=["config_snapshot"])

        with patch("lens.services.cancel_run_on_lensnode"):
            advance_evaluation_run(evaluation_run.uuid)
        evaluation_run.refresh_from_db()
        run.refresh_from_db()
        self.assertEqual(evaluation_run.status, EvaluationRun.Status.FAILED)
        self.assertEqual(run.status, Run.Status.CANCELLED)

    def test_disabled_metrics_remain_frozen_after_configuration_changes(self):
        from lens.evaluation.engine.batch import _metric_configs_from_snapshot
        from lens.models import EvaluationMetric

        EvaluationMetric.objects.all().update(enabled=False)
        evaluation_run = create_evaluation_run(dataset=self.dataset, requested_by=self.user)
        self.assertEqual(evaluation_run.config_snapshot["metrics"], [])
        EvaluationMetric.objects.filter(name="execution_success").update(enabled=True)
        self.assertEqual(_metric_configs_from_snapshot(evaluation_run), [])

    def test_failed_run_remains_failed_when_all_metrics_are_disabled(self):
        from lens.models import EvaluationMetric

        EvaluationMetric.objects.all().update(enabled=False)
        self.cases[1].delete()
        evaluation_run, _ = self._start()
        run = Run.objects.get(source=Run.Source.EVALUATION)
        run.status = Run.Status.FAILED
        run.save(update_fields=["status"])

        finalize_case_result(run.uuid)

        result = evaluation_run.results.get()
        self.assertIs(result.passed, False)
        self.assertNotIn("execution_success", result.scores)

    def test_legacy_batch_fails_if_a_frozen_case_was_deleted(self):
        case_uuids = [str(case.uuid) for case in self.cases]
        evaluation_run = EvaluationRun.objects.create(
            dataset=self.dataset,
            requested_by=self.user,
            total_cases=2,
            config_snapshot={"case_uuids": case_uuids},
        )
        self.cases[1].delete()

        with patch("lens.evaluation.engine.batch._enqueue_run"):
            start_evaluation_run(evaluation_run.uuid)

        evaluation_run.refresh_from_db()
        self.assertEqual(evaluation_run.status, EvaluationRun.Status.FAILED)
        self.assertEqual(evaluation_run.error, "EVALUATION_CASES_MISSING")
        self.assertEqual(evaluation_run.total_cases, 2)
        self.assertFalse(Run.objects.filter(source=Run.Source.EVALUATION).exists())

    def test_dispatch_failure_is_permanent_after_retry_budget(self):
        evaluation_run = create_evaluation_run(
            dataset=self.dataset,
            requested_by=self.user,
        )
        with patch(
            "lens.evaluation.engine.batch.ensure_case_run",
            side_effect=RuntimeError("boom"),
        ):
            with patch("lens.evaluation.engine.batch._enqueue_run"):
                start_evaluation_run(evaluation_run.uuid)
                for _index in range(3):
                    advance_evaluation_run(evaluation_run.uuid)

        results = list(evaluation_run.results.all())
        self.assertEqual(len(results), 2)
        for result in results:
            self.assertEqual(result.dispatch_attempts, 3)
            self.assertIs(result.passed, False)
            self.assertIn("EVALUATION_DISPATCH_FAILED", result.error)

        evaluation_run.refresh_from_db()
        self.assertEqual(evaluation_run.status, EvaluationRun.Status.COMPLETED)
        self.assertEqual(evaluation_run.failed_cases, 2)
        self.assertEqual(evaluation_run.completed_cases, 2)

    def test_retry_ignores_cases_that_never_started(self):
        evaluation_run, _enqueue = self._start()
        evaluation_run.status = EvaluationRun.Status.COMPLETED
        evaluation_run.save(update_fields=["status", "updated_at"])

        retried = retry_failed_cases(evaluation_run.uuid)

        self.assertEqual(retried, 1)

    def test_batch_waits_when_no_evaluation_node_is_available(self):
        self.node.status = LensNode.Status.OFFLINE
        self.node.save(update_fields=["status"])
        evaluation_run = create_evaluation_run(
            dataset=self.dataset,
            requested_by=self.user,
        )

        start_evaluation_run(evaluation_run.uuid)

        evaluation_run.refresh_from_db()
        self.assertEqual(evaluation_run.status, EvaluationRun.Status.RUNNING)
        self.assertFalse(Run.objects.filter(source=Run.Source.EVALUATION).exists())
        self.assertEqual(
            evaluation_run.results.filter(source_run__isnull=True).count(),
            2,
        )
        self.assertEqual(
            evaluation_run.config_snapshot["waiting"]["reason"],
            "NO_EVALUATION_NODE",
        )

    def test_default_pool_node_is_used_when_no_dedicated_pool_exists(self):
        GlobalSetting.objects.filter(key="evaluation.execution_pool").delete()
        self.node.pool = LensNode.Pool.DEFAULT
        self.node.save(update_fields=["pool"])
        evaluation_run = create_evaluation_run(
            dataset=self.dataset,
            requested_by=self.user,
        )

        with patch("lens.evaluation.engine.batch._enqueue_run"):
            with self.captureOnCommitCallbacks(execute=True):
                start_evaluation_run(evaluation_run.uuid)

        evaluation_run.refresh_from_db()
        run = Run.objects.get(source=Run.Source.EVALUATION)
        self.assertEqual(run.lensnode, self.node)
        self.assertEqual(
            evaluation_run.config_snapshot["lensnode_pool"],
            LensNode.Pool.DEFAULT,
        )
        self.assertNotIn("waiting", evaluation_run.config_snapshot)

    def test_busy_wait_does_not_expire_while_a_case_is_executing(self):
        evaluation_run, _enqueue = self._start()
        active = Run.objects.filter(source=Run.Source.EVALUATION).get()
        active.status = Run.Status.RUNNING
        active.save(update_fields=["status", "updated_at"])
        snapshot = dict(evaluation_run.config_snapshot or {})
        snapshot["waiting"] = {
            "reason": "EVALUATION_NODE_BUSY",
            "since": (timezone.now() - timedelta(hours=3)).isoformat(),
        }
        evaluation_run.config_snapshot = snapshot
        evaluation_run.save(update_fields=["config_snapshot", "updated_at"])

        advance_evaluation_run(evaluation_run.uuid)

        evaluation_run.refresh_from_db()
        self.assertEqual(evaluation_run.status, EvaluationRun.Status.RUNNING)
        self.assertNotIn("waiting", evaluation_run.config_snapshot)

    def test_batch_fails_after_waiting_for_a_missing_node(self):
        self.node.status = LensNode.Status.OFFLINE
        self.node.save(update_fields=["status"])
        evaluation_run = create_evaluation_run(
            dataset=self.dataset,
            requested_by=self.user,
        )
        start_evaluation_run(evaluation_run.uuid)
        snapshot = dict(evaluation_run.config_snapshot or {})
        snapshot["waiting"] = {
            "reason": "NO_EVALUATION_NODE",
            "since": (timezone.now() - timedelta(hours=2)).isoformat(),
        }
        evaluation_run.config_snapshot = snapshot
        evaluation_run.save(update_fields=["config_snapshot", "updated_at"])

        advance_evaluation_run(evaluation_run.uuid)

        evaluation_run.refresh_from_db()
        self.assertEqual(evaluation_run.status, EvaluationRun.Status.FAILED)
        self.assertEqual(
            evaluation_run.error,
            "EVALUATION_NODE_UNAVAILABLE",
        )

    @override_settings(APP_VERSION="0.63.8")
    def test_batch_records_platform_and_node_versions(self):
        self.node.agent_version = "0.63.8"
        self.node.save(update_fields=["agent_version"])
        evaluation_run = create_evaluation_run(
            dataset=self.dataset,
            requested_by=self.user,
        )

        with patch("lens.evaluation.engine.batch._enqueue_run"):
            with self.captureOnCommitCallbacks(execute=True):
                start_evaluation_run(evaluation_run.uuid)

        evaluation_run.refresh_from_db()
        snapshot = evaluation_run.config_snapshot
        self.assertEqual(snapshot["platform_version"], "0.63.8")
        self.assertEqual(snapshot["scoring_version"], "3")
        self.assertEqual(snapshot["lensnode_agent_version"], "0.63.8")
        self.assertEqual(snapshot["lensnode_pool"], LensNode.Pool.EVALUATION)

    def test_terminal_run_schedules_case_finalize(self):
        from lens.services import finish_lensnode_run

        evaluation_run, _enqueue = self._start()
        run = Run.objects.filter(source=Run.Source.EVALUATION).get()

        with patch(
            "lens.evaluation.tasks.finalize_evaluation_case_task.delay"
        ) as delay:
            with self.captureOnCommitCallbacks(execute=True):
                finish_lensnode_run(
                    run.uuid,
                    Run.Status.DONE,
                    outcome=Run.Outcome.COMPLETED,
                    final_content="Answer",
                )

        delay.assert_called_once()
        self.assertEqual(str(delay.call_args.args[0]), str(run.uuid))

    def test_assistant_promotion_skips_evaluation_runs(self):
        from lens.services import _promote_next_queued_run, create_execution_run

        evaluation_session = Session.objects.create(
            assistant=self.assistant,
            user=self.user,
            source=Session.Source.EVALUATION,
        )
        create_execution_run(
            session=evaluation_session,
            question="Evaluation question",
            enqueue=False,
            run_source=Run.Source.EVALUATION,
        )
        user_session = Session.objects.create(
            assistant=self.assistant,
            user=self.user,
            source=Session.Source.USER,
        )
        user_run = create_execution_run(
            session=user_session,
            question="User question",
            enqueue=False,
            run_source=Run.Source.USER,
        )

        with patch("lens.tasks.enqueue_answer_run_task") as enqueue:
            _promote_next_queued_run(self.assistant)

        enqueue.assert_called_once()
        self.assertEqual(str(enqueue.call_args.args[0]), str(user_run.uuid))
