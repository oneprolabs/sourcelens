import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from lens.evaluation.engine.batch import (
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
    LensNode,
    Run,
    RunTraceEvent,
    Session,
)

User = get_user_model()


class EvaluationEngineTests(TestCase):
    def setUp(self):
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
        self.assertEqual(result.output_snapshot, "Answer")
        self.assertEqual(enqueue.call_count, 1)

    def test_case_with_expected_answer_passes(self):
        EvaluationCase.objects.filter(dataset=self.dataset).update(expected_answer="Answer")
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
        EvaluationCase.objects.filter(dataset=self.dataset).update(expected_answer="Answer")
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
        self.assertFalse(result.passed)
        evaluation_run.status = EvaluationRun.Status.COMPLETED
        evaluation_run.save(update_fields=["status", "updated_at"])

        retried = retry_failed_cases(evaluation_run.uuid)

        self.assertEqual(retried, 2)
        result.refresh_from_db()
        self.assertIsNone(result.source_run)
        self.assertIsNone(result.passed)
        evaluation_run.refresh_from_db()
        self.assertEqual(evaluation_run.status, EvaluationRun.Status.QUEUED)

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
