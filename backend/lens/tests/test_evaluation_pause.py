"""Pause/resume transitions for evaluation batches."""

import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from lens.evaluation.engine.batch import (
    advance_evaluation_run,
    create_evaluation_run,
    finalize_case_result,
    pause_evaluation_run,
    resume_evaluation_run,
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
)

User = get_user_model()


class EvaluationPauseTests(TestCase):
    def setUp(self):
        GlobalSetting.objects.create(key="evaluation.execution_pool", value="evaluation")
        self.user = User.objects.create_user(username="pause-operator", is_staff=True)
        self.node = LensNode.objects.create(
            name="Evaluation node",
            pool=LensNode.Pool.EVALUATION,
            status=LensNode.Status.ONLINE,
            enrollment_status=LensNode.EnrollmentStatus.APPROVED,
            evaluation_concurrency_limit=1,
        )
        self.assistant = Assistant.objects.create(
            name="Evaluation assistant",
            slug="pause-assistant",
            lensnode=self.node,
            selected_task="knowledge_qa",
            agent_model_ref=uuid.uuid4(),
        )
        self.dataset = EvaluationDataset.objects.create(
            name="Pause dataset",
            assistant=self.assistant,
            created_by=self.user,
            status=EvaluationDataset.Status.ACTIVE,
        )
        self.cases = [
            EvaluationCase.objects.create(dataset=self.dataset, input=f"Question {index}")
            for index in range(2)
        ]

    def _start(self):
        evaluation_run = create_evaluation_run(dataset=self.dataset, requested_by=self.user)
        with patch("lens.evaluation.engine.batch._enqueue_run"):
            with self.captureOnCommitCallbacks(execute=True):
                start_evaluation_run(evaluation_run.uuid)
        evaluation_run.refresh_from_db()
        return evaluation_run

    def _finish_run(self, run):
        run.status = Run.Status.DONE
        run.outcome = Run.Outcome.COMPLETED
        run.started_at = timezone.now() - timedelta(seconds=20)
        run.finished_at = timezone.now()
        run.output_message.content = "Answer"
        run.output_message.save(update_fields=["content"])
        run.save()
        RunTraceEvent.objects.create(
            run=run,
            event_id=uuid.uuid4(),
            sequence=1,
            event_type="model.completed",
            timestamp=timezone.now(),
            payload={"usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}},
        )

    def test_pause_drains_in_flight_cases_and_resume_dispatches_the_rest(self):
        evaluation_run = self._start()
        first = Run.objects.filter(source=Run.Source.EVALUATION).get()

        pause_evaluation_run(evaluation_run.uuid)
        evaluation_run.refresh_from_db()
        self.assertEqual(evaluation_run.status, EvaluationRun.Status.PAUSED)
        self.assertTrue(evaluation_run.config_snapshot["pause"]["paused_at"])

        self._finish_run(first)
        with patch("lens.evaluation.engine.batch._enqueue_run"):
            with self.captureOnCommitCallbacks(execute=True):
                finalize_case_result(first.uuid)

        evaluation_run.refresh_from_db()
        scored = EvaluationResult.objects.get(evaluation_run=evaluation_run, source_run_uuid=first.uuid)
        self.assertTrue(scored.scores)
        self.assertEqual(evaluation_run.completed_cases, 1)
        self.assertEqual(evaluation_run.status, EvaluationRun.Status.PAUSED)
        self.assertEqual(
            Run.objects.filter(source=Run.Source.EVALUATION).count(),
            1,
        )

        resume_evaluation_run(evaluation_run.uuid)
        with patch("lens.evaluation.engine.batch._enqueue_run"):
            with self.captureOnCommitCallbacks(execute=True):
                advance_evaluation_run(evaluation_run.uuid)

        evaluation_run.refresh_from_db()
        self.assertEqual(evaluation_run.status, EvaluationRun.Status.RUNNING)
        self.assertNotIn("pause", evaluation_run.config_snapshot)
        self.assertIn("last_pause", evaluation_run.config_snapshot)
        second = Run.objects.filter(source=Run.Source.EVALUATION).exclude(uuid=first.uuid).get()

        self._finish_run(second)
        with patch("lens.evaluation.engine.batch._enqueue_run"):
            with self.captureOnCommitCallbacks(execute=True):
                finalize_case_result(second.uuid)

        evaluation_run.refresh_from_db()
        self.assertEqual(evaluation_run.status, EvaluationRun.Status.COMPLETED)
        self.assertEqual(evaluation_run.completed_cases, 2)

    def test_pause_rejects_terminal_batches(self):
        evaluation_run = self._start()
        evaluation_run.status = EvaluationRun.Status.COMPLETED
        evaluation_run.save(update_fields=["status"])

        with self.assertRaisesMessage(ValueError, "EVALUATION_RUN_NOT_PAUSABLE"):
            pause_evaluation_run(evaluation_run.uuid)

    def test_resume_rejects_batches_that_are_not_paused(self):
        evaluation_run = self._start()

        with self.assertRaisesMessage(ValueError, "EVALUATION_RUN_NOT_PAUSED"):
            resume_evaluation_run(evaluation_run.uuid)


class EvaluationPauseApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="pause-api", is_staff=True)
        self.client.force_authenticate(self.user)
        self.assistant = Assistant.objects.create(name="Pause API", slug="pause-api")
        self.dataset = EvaluationDataset.objects.create(
            name="Pause API dataset",
            assistant=self.assistant,
            created_by=self.user,
            status=EvaluationDataset.Status.ACTIVE,
        )
        EvaluationCase.objects.create(dataset=self.dataset, input="Question")
        self.batch = create_evaluation_run(
            dataset=self.dataset,
            requested_by=self.user,
        )

    def test_pause_and_resume_round_trip(self):
        response = self.client.post(
            f"/api/lens/admin/evaluation/runs/{self.batch.uuid}/pause/",
            {"reason": "freeze the node"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["data"]["status"], "paused")

        response = self.client.post(
            f"/api/lens/admin/evaluation/runs/{self.batch.uuid}/pause/",
            {},
            format="json",
        )
        self.assertEqual(response.status_code, 409, response.content)

        response = self.client.post(
            f"/api/lens/admin/evaluation/runs/{self.batch.uuid}/resume/",
            {},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn(response.json()["data"]["status"], {"queued", "running"})

    def test_resume_rejects_batches_that_are_not_paused(self):
        response = self.client.post(
            f"/api/lens/admin/evaluation/runs/{self.batch.uuid}/resume/",
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 409, response.content)
