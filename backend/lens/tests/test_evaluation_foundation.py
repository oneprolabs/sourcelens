import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from lens.evaluation.evaluators import derive_run_quality
from lens.models import (
    Assistant,
    EvaluationCase,
    EvaluationDataset,
    EvaluationMetric,
    EvaluationResult,
    EvaluationRun,
    GlobalSetting,
    LensNode,
    Run,
    Session,
)
from lens.services import create_execution_run, select_execution_lensnode
from lens.tasks import run_retention_task

User = get_user_model()


class EvaluationFoundationTests(TestCase):
    def setUp(self):
        GlobalSetting.objects.create(key="evaluation.execution_pool", value="evaluation")
        self.client = APIClient()
        self.user = User.objects.create_user(username="evaluation-user")
        self.default_node = LensNode.objects.create(
            name="Default node",
            status=LensNode.Status.ONLINE,
            enrollment_status=LensNode.EnrollmentStatus.APPROVED,
        )
        self.evaluation_node = LensNode.objects.create(
            name="Evaluation node",
            pool=LensNode.Pool.EVALUATION,
            status=LensNode.Status.ONLINE,
            enrollment_status=LensNode.EnrollmentStatus.APPROVED,
        )
        self.assistant = Assistant.objects.create(
            name="Evaluation assistant",
            slug="evaluation-assistant",
            lensnode=self.evaluation_node,
            selected_task="knowledge_qa",
            agent_model_ref=uuid.uuid4(),
        )
        self.session = Session.objects.create(
            assistant=self.assistant,
            user=self.user,
            source=Session.Source.EVALUATION,
        )

    def test_evaluation_run_uses_only_evaluation_node(self):
        selected = select_execution_lensnode(
            self.assistant,
            run_source=Run.Source.EVALUATION,
        )

        self.assertEqual(selected, self.evaluation_node)

    def test_evaluation_falls_back_to_bound_default_node_without_pool(self):
        self.assistant.lensnode = self.default_node
        self.assistant.save(update_fields=["lensnode"])
        GlobalSetting.objects.filter(key="evaluation.execution_pool").delete()

        selected = select_execution_lensnode(
            self.assistant,
            run_source=Run.Source.EVALUATION,
        )

        self.assertEqual(selected, self.default_node)

    def test_user_session_listing_excludes_evaluation_source(self):
        self.assertFalse(
            Session.objects.filter(
                user=self.user,
                source=Session.Source.USER,
            ).exists()
        )

    def test_evaluation_result_keeps_snapshot_after_source_run_retention(self):
        dataset = EvaluationDataset.objects.create(
            name="Smoke",
            assistant=self.assistant,
            created_by=self.user,
        )
        case = EvaluationCase.objects.create(dataset=dataset, input="Question")
        evaluation_run = EvaluationRun.objects.create(
            dataset=dataset, requested_by=self.user
        )
        source_run = create_execution_run(
            session=self.session,
            question="Question",
            enqueue=False,
            run_source=Run.Source.EVALUATION,
        )
        source_run.status = Run.Status.DONE
        source_run.finished_at = timezone.now() - timedelta(days=40)
        source_run.save(update_fields=["status", "finished_at", "updated_at"])
        result = EvaluationResult.objects.create(
            evaluation_run=evaluation_run,
            case=case,
            source_run=source_run,
            source_run_uuid=source_run.uuid,
            output_snapshot="Frozen answer",
        )

        run_retention_task()

        result.refresh_from_db()
        self.assertIsNone(result.source_run)
        self.assertEqual(result.source_run_uuid, source_run.uuid)
        self.assertEqual(result.output_snapshot, "Frozen answer")

    def test_evidence_unavailable_without_required_evidence_is_inconclusive(self):
        run = create_execution_run(
            session=self.session,
            question="Question",
            enqueue=False,
            run_source=Run.Source.EVALUATION,
        )
        run.status = Run.Status.DONE
        run.outcome = Run.Outcome.PARTIAL
        run.termination_detail = {"reason": "evidence_unavailable"}
        run.save(
            update_fields=["status", "outcome", "termination_detail", "updated_at"]
        )

        quality = derive_run_quality(run)

        self.assertIsNone(quality["passed"])
        self.assertEqual(quality["status"], "inconclusive")

    def test_evaluation_access_requires_authorization(self):
        self.client.force_authenticate(user=self.user)

        response = self.client.get("/api/lens/admin/evaluation/access/")

        self.assertEqual(response.status_code, 403)

    def test_staff_can_access_evaluation_surface(self):
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        self.client.force_authenticate(user=self.user)

        response = self.client.get("/api/lens/admin/evaluation/access/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["feature"], "run_evaluations")

    def test_builtin_metric_catalog_scores_answer_quality(self):
        metrics = {metric.name: metric for metric in EvaluationMetric.objects.all()}

        self.assertEqual(metrics["answer_correctness"].category, "correctness")
        self.assertAlmostEqual(float(metrics["answer_correctness"].weight), 0.4)
        self.assertEqual(metrics["execution_success"].category, "reliability")
        self.assertAlmostEqual(
            sum(float(metric.weight) for metric in metrics.values() if metric.enabled),
            1.0,
        )
