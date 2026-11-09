from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from lens.models import Assistant, EvaluationCase, EvaluationDataset, EvaluationResult, EvaluationRun


class EvaluationComparisonTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="comparison", is_staff=True)
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.assistant = Assistant.objects.create(name="A", slug="compare")
        self.dataset = EvaluationDataset.objects.create(name="D", assistant=self.assistant)
        self.case = EvaluationCase.objects.create(dataset=self.dataset, input="Question")
        self.snapshot = {"dataset": {"revision": 1}, "scoring_version": "2", "metrics": [{"name": "answer"}],
                         "case_uuids": [str(self.case.uuid)]}
        self.left = EvaluationRun.objects.create(dataset=self.dataset, status="completed", config_snapshot=self.snapshot)
        self.right = EvaluationRun.objects.create(dataset=self.dataset, status="completed", config_snapshot=self.snapshot)
        EvaluationResult.objects.create(evaluation_run=self.left, case=self.case, passed=None, output_snapshot="left")
        EvaluationResult.objects.create(evaluation_run=self.right, case=self.case, passed=False, output_snapshot="right")

    def test_pair_results_and_report_unfrozen_environment(self):
        response = self.client.get(f"/api/lens/admin/evaluation/runs/{self.left.uuid}/compare/", {"other": self.right.uuid})
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()["data"]
        self.assertFalse(data["fully_comparable"])
        self.assertEqual(data["limitations"], ["runtime_environment_not_fully_frozen"])
        self.assertEqual(data["cases"][0]["left"]["output"], "left")
        self.assertIsNone(data["cases"][0]["left"]["passed"])
        self.assertFalse(data["cases"][0]["right"]["passed"])

    def test_mismatch_and_invalid_uuid(self):
        self.right.config_snapshot = {"dataset": {"revision": 2}, "scoring_version": "3"}
        self.right.save(update_fields=["config_snapshot"])
        url = f"/api/lens/admin/evaluation/runs/{self.left.uuid}/compare/"
        data = self.client.get(url, {"other": self.right.uuid}).json()["data"]
        self.assertIn("different_dataset_revision", data["limitations"])
        self.assertIn("different_scoring_version", data["limitations"])
        self.assertEqual(self.client.get(url, {"other": "invalid"}).status_code, 400)
