import uuid

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from rest_framework.test import APIClient

from lens.evaluation.engine.batch import create_evaluation_run
from lens.models import Assistant, EvaluationCase, EvaluationDataset, EvaluationResult, LensNode, Message, Run, Session
from lens.services import create_execution_run


class EvaluationBaselineTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="baseline-admin", is_staff=True)
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.node = LensNode.objects.create(name="node", status="online", enrollment_status="approved")
        self.assistant = Assistant.objects.create(
            name="assistant", slug="baseline-assistant", lensnode=self.node,
            selected_task="knowledge_qa", agent_model_ref=uuid.uuid4(),
        )
        self.dataset = EvaluationDataset.objects.create(name="Dataset", assistant=self.assistant, created_by=self.user)
        self.session = Session.objects.create(assistant=self.assistant, user=self.user)
        self.run = create_execution_run(session=self.session, question="Question?", enqueue=False)
        self.run.output_message.content = "A reference answer"
        self.run.output_message.save(update_fields=["content"])
        self.run.status = Run.Status.DONE
        self.run.outcome = Run.Outcome.COMPLETED
        self.run.citations = [{"path": "reference.md"}]
        self.run.save(update_fields=["status", "outcome", "citations"])
        self.url = f"/api/lens/admin/evaluation/datasets/{self.dataset.uuid}/baseline/"

    def test_create_and_freeze_baseline_without_exact_match(self):
        response = self.client.post(self.url, {"run_uuid": str(self.run.uuid)}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        case = EvaluationCase.objects.get(dataset=self.dataset)
        self.assertEqual(case.input, "Question?")
        self.assertEqual(case.expected_answer, "")
        self.assertEqual(case.config["baseline"]["answer"], "A reference answer")
        batch = create_evaluation_run(dataset=self.dataset, requested_by=self.user)
        result = EvaluationResult.objects.get(evaluation_run=batch, case=case)
        case.config["baseline"]["answer"] = "Changed later"
        case.save(update_fields=["config"])
        response = self.client.get(f"/api/lens/admin/evaluation/runs/{batch.uuid}/results/{result.uuid}/")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["data"]["baseline"]["answer"], "A reference answer")
        self.assertEqual(self.client.post(self.url, {"run_uuid": str(self.run.uuid)}, format="json").status_code, 400)

    def test_rejects_multiturn(self):
        Message.objects.create(session=self.session, role="user", content="previous", sequence=0)
        response = self.client.post(self.url, {"run_uuid": str(self.run.uuid)}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(EvaluationCase.objects.exists())

    def test_rejects_failed_or_mismatched_run(self):
        self.run.status = Run.Status.FAILED
        self.run.save(update_fields=["status"])
        self.assertEqual(self.client.post(self.url, {"run_uuid": str(self.run.uuid)}, format="json").status_code, 400)
        other = Assistant.objects.create(name="other", slug="other", agent_model_ref=uuid.uuid4())
        self.dataset.assistant = other
        self.dataset.save(update_fields=["assistant"])
        self.assertEqual(self.client.post(self.url, {"run_uuid": str(self.run.uuid)}, format="json").status_code, 400)

    def test_requires_permissions_and_valid_uuid(self):
        self.assertEqual(self.client.post(self.url, {"run_uuid": "invalid"}, format="json").status_code, 400)
        self.client.force_authenticate(get_user_model().objects.create_user(username="other-user"))
        self.assertEqual(self.client.post(self.url, {"run_uuid": str(self.run.uuid)}, format="json").status_code, 403)

    def test_evaluation_permission_alone_cannot_read_admin_run(self):
        user = get_user_model().objects.create_user(username="evaluator")
        user.user_permissions.add(Permission.objects.get(codename="run_evaluations"))
        self.dataset.created_by = user
        self.dataset.save(update_fields=["created_by"])
        self.client.force_authenticate(user)
        with patch("accounts.permissions.get_effective_feature_keys", return_value={"run_evaluations"}):
            response = self.client.post(self.url, {"run_uuid": str(self.run.uuid)}, format="json")
        self.assertEqual(response.status_code, 403)
        self.assertFalse(EvaluationCase.objects.exists())
