from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from lens.models import (
    Assistant,
    EvaluationCalibration,
    EvaluationCase,
    EvaluationDataset,
    EvaluationResult,
    EvaluationRun,
)


class CalibrationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="calibrator", is_staff=True)
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        assistant = Assistant.objects.create(name="A", slug="calibration")
        dataset = EvaluationDataset.objects.create(name="D", assistant=assistant)
        case = EvaluationCase.objects.create(dataset=dataset, input="question")
        self.batch = EvaluationRun.objects.create(dataset=dataset)
        self.result = EvaluationResult.objects.create(
            evaluation_run=self.batch,
            case=case,
            scores={"answer_correctness": {"passed": None}},
            judge_evidence={"verdict": "inconclusive"},
        )
        self.url = f"/api/lens/admin/evaluation/runs/{self.batch.uuid}/results/{self.result.uuid}/calibrations/"

    def test_append_only_and_preserves_judge_snapshot(self):
        payload = {"answer_verdict": "pass", "evidence_verdict": "inconclusive", "explanation": "Reviewed."}
        self.assertEqual(self.client.post(self.url, payload, format="json").status_code, 201)
        self.result.judge_evidence = {"verdict": "changed"}
        self.result.save(update_fields=["judge_evidence"])
        payload["answer_verdict"] = "fail"
        self.assertEqual(self.client.post(self.url, payload, format="json").status_code, 201)
        rows = self.client.get(self.url).json()["data"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["judge_snapshot"], {"verdict": "inconclusive"})
        self.assertEqual(rows[0]["answer_verdict"], "pass")
        self.assertEqual(rows[1]["answer_verdict"], "fail")
        self.result.refresh_from_db()
        self.assertIsNone(self.result.passed)

    def test_requires_permission_valid_input_and_scored_result(self):
        self.assertEqual(self.client.post(self.url, {"answer_verdict": "pass"}, format="json").status_code, 400)
        self.result.scores = {}
        self.result.save(update_fields=["scores"])
        payload = {"answer_verdict": "pass", "evidence_verdict": "pass", "explanation": "Reviewed"}
        self.assertEqual(self.client.post(self.url, payload, format="json").status_code, 409)
        self.client.force_authenticate(get_user_model().objects.create_user(username="not-allowed"))
        self.assertEqual(self.client.post(self.url, payload, format="json").status_code, 403)
        self.assertFalse(EvaluationCalibration.objects.exists())

    def test_calibration_summary_counts_agreement_and_excludes_stale_reviews(self):
        self.result.judge_evidence = {"answer": {"verdict": "pass"}, "evidence": {"verdict": "fail"}}
        self.result.output_snapshot = "original"
        self.result.save(update_fields=["judge_evidence", "output_snapshot"])
        payload = {"answer_verdict": "pass", "evidence_verdict": "pass", "explanation": "Reviewed."}
        self.assertEqual(self.client.post(self.url, payload, format="json").status_code, 201)
        report_url = f"/api/lens/admin/evaluation/runs/{self.batch.uuid}/report/"
        calibration = self.client.get(report_url).json()["data"]["calibration"]
        self.assertEqual(calibration["dimensions"]["answer"]["agreement_rate"], 1.0)
        self.assertEqual(calibration["dimensions"]["evidence"]["agreement_rate"], 0.0)
        self.result.output_snapshot = "changed after retry"
        self.result.save(update_fields=["output_snapshot"])
        calibration = self.client.get(report_url).json()["data"]["calibration"]
        self.assertEqual(calibration["stale_reviews"], 1)
        self.assertIsNone(calibration["dimensions"]["answer"]["agreement_rate"])
