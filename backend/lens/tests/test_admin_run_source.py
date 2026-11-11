"""Coverage for Run source filtering and evaluation batch linkage."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient
from unittest.mock import patch

from lens.models import EvaluationCase, EvaluationDataset, EvaluationResult, EvaluationRun, Run
from lens.services import create_execution_run
from lens.tests.test_run_trace import RunTraceFixtureMixin


class AdminRunSourceTests(RunTraceFixtureMixin, TestCase):
    """Exercise source visibility without exposing evaluation result data."""

    def setUp(self):
        self.create_trace_fixture()
        self.client = APIClient()
        self.client.force_authenticate(get_user_model().objects.create_user(username="source-admin", is_staff=True))
        self.evaluation = create_execution_run(self.run.session, "Evaluation question", enqueue=False)
        self.evaluation.source = Run.Source.EVALUATION
        self.evaluation.status = Run.Status.FAILED
        self.evaluation.save(update_fields=["source", "status"])
        self.dataset = EvaluationDataset.objects.create(name="Private dataset", assistant=self.run.session.assistant)
        self.case = EvaluationCase.objects.create(dataset=self.dataset, input="Secret case")
        self.batch = EvaluationRun.objects.create(dataset=self.dataset)
        EvaluationResult.objects.create(
            evaluation_run=self.batch, case=self.case, source_run=self.evaluation, output_snapshot="Secret output"
        )

    def test_source_filter_and_summary_respect_other_filters(self):
        for source, expected, status in (
            ("user", self.run, self.run.status),
            ("evaluation", self.evaluation, Run.Status.FAILED),
        ):
            with self.subTest(source=source):
                response = self.client.get("/api/lens/admin/runs/", {"source": source, "status": "failed"})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data["summary"]["total"], 1)
                self.assertEqual(response.data["summary"][status], 1)
                if status == Run.Status.FAILED:
                    self.assertEqual(response.data["results"][0]["uuid"], str(expected.uuid))
                    self.assertEqual(response.data["results"][0]["source"], source)
                else:
                    self.assertEqual(response.data["total"], 0)
        response = self.client.get("/api/lens/admin/runs/")
        self.assertEqual({row["source"] for row in response.data["results"]}, {"user", "evaluation"})
        self.assertTrue(all("evaluation_run_uuid" not in row for row in response.data["results"]))

    def test_detail_only_exposes_batch_uuid_for_evaluation_runs(self):
        response = self.client.get(f"/api/lens/admin/runs/{self.evaluation.uuid}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["source"], "evaluation")
        self.assertEqual(response.data["evaluation_run_uuid"], str(self.batch.uuid))
        self.assertNotIn("Secret case", str(response.data))
        self.assertNotIn("Secret output", str(response.data))
        EvaluationResult.objects.create(evaluation_run=self.batch, case=EvaluationCase.objects.create(
            dataset=self.dataset, input="Baseline"), source_run=self.run)
        response = self.client.get(f"/api/lens/admin/runs/{self.run.uuid}/")
        self.assertEqual(response.data["source"], "user")
        self.assertIsNone(response.data["evaluation_run_uuid"])
        self.evaluation.evaluation_results.all().delete()
        response = self.client.get(f"/api/lens/admin/runs/{self.evaluation.uuid}/")
        self.assertIsNone(response.data["evaluation_run_uuid"])

    def test_batch_uuid_requires_evaluation_access_and_batch_visibility(self):
        user = get_user_model().objects.create_user(username="limited-operator")
        self.client.force_authenticate(user)
        url = f"/api/lens/admin/runs/{self.evaluation.uuid}/"
        with patch("accounts.permissions.get_effective_feature_keys", return_value={"admin_console"}):
            self.assertIsNone(self.client.get(url).data["evaluation_run_uuid"])
        user.user_permissions.add(Permission.objects.get(codename="run_evaluations"))
        with patch("accounts.permissions.get_effective_feature_keys", return_value={"admin_console", "run_evaluations"}):
            with patch("lens.views.admin_runs.user_sees_all_assistants", return_value=False):
                self.assertIsNone(self.client.get(url).data["evaluation_run_uuid"])
                self.batch.requested_by = user
                self.batch.save(update_fields=["requested_by"])
                self.assertEqual(self.client.get(url).data["evaluation_run_uuid"], str(self.batch.uuid))
            self.batch.requested_by = None
            self.batch.save(update_fields=["requested_by"])
            with patch("lens.views.admin_runs.user_sees_all_assistants", return_value=True):
                self.assertEqual(self.client.get(url).data["evaluation_run_uuid"], str(self.batch.uuid))

    def test_list_does_not_query_evaluation_results(self):
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get("/api/lens/admin/runs/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(any(EvaluationResult._meta.db_table in query["sql"] for query in captured))
