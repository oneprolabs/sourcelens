import json
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from lens.models import (
    Assistant,
    EvaluationCase,
    EvaluationResult,
    EvaluationRun,
    GlobalSetting,
    LensNode,
    Run,
)

User = get_user_model()


class EvaluationApiTests(TestCase):
    def setUp(self):
        GlobalSetting.objects.create(key="evaluation.execution_pool", value="evaluation")
        self.client = APIClient()
        self.operator = User.objects.create_user(
            username="evaluation-admin",
            is_staff=True,
        )
        self.client.force_authenticate(user=self.operator)
        self.node = LensNode.objects.create(
            name="Evaluation node",
            pool=LensNode.Pool.EVALUATION,
            status=LensNode.Status.ONLINE,
            enrollment_status=LensNode.EnrollmentStatus.APPROVED,
            evaluation_concurrency_limit=2,
        )
        self.assistant = Assistant.objects.create(
            name="Evaluation assistant",
            slug="evaluation-assistant",
            lensnode=self.node,
            selected_task="knowledge_qa",
            agent_model_ref=uuid.uuid4(),
        )

    def _create_dataset(self, name="Smoke"):
        response = self.client.post(
            "/api/lens/admin/evaluation/datasets/",
            {
                "name": name,
                "assistant": str(self.assistant.uuid),
                "status": "active",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()["data"]

    def test_non_operator_cannot_list_datasets(self):
        self.client.force_authenticate(user=None)

        response = self.client.get("/api/lens/admin/evaluation/datasets/")

        self.assertIn(response.status_code, {401, 403})

    def test_dataset_creation_versions_repeated_names(self):
        first = self._create_dataset()
        second = self._create_dataset()

        self.assertEqual(first["version"], 1)
        self.assertEqual(second["version"], 2)
        self.assertEqual(first["assistant"], str(self.assistant.uuid))

    def test_dataset_version_is_read_only(self):
        dataset = self._create_dataset()

        response = self.client.patch(
            f"/api/lens/admin/evaluation/datasets/{dataset['uuid']}/",
            {"version": 99},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["data"]["version"], dataset["version"])

    def test_create_ignores_supplied_version(self):
        response = self.client.post(
            "/api/lens/admin/evaluation/datasets/",
            {
                "name": "Forced",
                "assistant": str(self.assistant.uuid),
                "version": 99,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["data"]["version"], 1)

    def test_delete_rejects_dataset_with_runs(self):
        dataset = self._create_dataset()
        EvaluationRun.objects.create(dataset_id=dataset["uuid"])

        response = self.client.delete(
            f"/api/lens/admin/evaluation/datasets/{dataset['uuid']}/"
        )

        self.assertEqual(response.status_code, 409, response.content)

    def test_delete_rejects_case_with_results(self):
        dataset = self._create_dataset()
        case = self.client.post(
            f"/api/lens/admin/evaluation/datasets/{dataset['uuid']}/cases/",
            {"input": "Question"},
            format="json",
        ).json()["data"]
        evaluation_run = EvaluationRun.objects.create(dataset_id=dataset["uuid"])
        EvaluationResult.objects.create(
            evaluation_run=evaluation_run,
            case_id=case["uuid"],
        )

        response = self.client.delete(
            f"/api/lens/admin/evaluation/cases/{case['uuid']}/"
        )

        self.assertEqual(response.status_code, 409, response.content)

    def test_case_creation_rejects_duplicate_input(self):
        dataset = self._create_dataset()
        url = f"/api/lens/admin/evaluation/datasets/{dataset['uuid']}/cases/"
        payload = {"input": "What changed?", "expected_sources": ["report.md"]}

        first = self.client.post(url, payload, format="json")
        second = self.client.post(url, payload, format="json")

        self.assertEqual(first.status_code, 201, first.content)
        self.assertEqual(second.status_code, 400)
        self.assertEqual(dataset["case_count"], 0)

    def test_jsonl_import_rejects_whole_batch_on_invalid_row(self):
        dataset = self._create_dataset()
        url = f"/api/lens/admin/evaluation/datasets/{dataset['uuid']}/import/"
        text = "\n".join(
            [
                json.dumps({"input": "Valid question"}),
                json.dumps({"expected_answer": "missing input"}),
            ]
        )

        response = self.client.post(url, {"text": text}, format="json")

        self.assertEqual(response.status_code, 400)
        errors = response.json()["data"]["errors"]
        self.assertEqual(errors[0]["line"], 2)
        self.assertFalse(EvaluationCase.objects.exists())

    def test_csv_import_creates_cases(self):
        dataset = self._create_dataset()
        url = f"/api/lens/admin/evaluation/datasets/{dataset['uuid']}/import/"
        text = (
            "input,expected_sources,tags\n" "Question one,report.md|budget.csv,smoke\n" "Question two,report.md,smoke\n"
        )

        response = self.client.post(
            url,
            {"text": text, "filename": "cases.csv"},
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["data"]["created"], 2)
        case = EvaluationCase.objects.get(input="Question one")
        self.assertEqual(case.expected_sources, ["report.md", "budget.csv"])
        self.assertEqual(case.tags, ["smoke"])

    def test_batch_launch_creates_runs_and_reports(self):
        dataset = self._create_dataset()
        cases_url = f"/api/lens/admin/evaluation/datasets/{dataset['uuid']}/cases/"
        for index in range(2):
            response = self.client.post(
                cases_url,
                {"input": f"Question {index}", "expected_sources": ["report.md"]},
                format="json",
            )
            self.assertEqual(response.status_code, 201, response.content)
        runs_url = f"/api/lens/admin/evaluation/datasets/{dataset['uuid']}/runs/"

        with patch("lens.evaluation.engine.batch._enqueue_run"):
            response = self.client.post(runs_url, {}, format="json")

        self.assertEqual(response.status_code, 201, response.content)
        run_payload = response.json()["data"]
        self.assertEqual(run_payload["total_cases"], 2)
        self.assertEqual(
            Run.objects.filter(source=Run.Source.EVALUATION).count(),
            2,
        )

        report = self.client.get(f"/api/lens/admin/evaluation/runs/{run_payload['uuid']}/report/")
        self.assertEqual(report.status_code, 200, report.content)
        summary = report.json()["data"]["summary"]
        self.assertEqual(summary["total_cases"], 2)
        self.assertEqual(summary["completed_cases"], 0)
        self.assertEqual(summary["coverage"]["ratio"], 0.0)

        results = self.client.get(f"/api/lens/admin/evaluation/runs/{run_payload['uuid']}/results/")
        self.assertEqual(results.status_code, 200, results.content)
        self.assertEqual(results.json()["data"]["count"], 2)

    def test_dataset_run_launch_requires_cases(self):
        dataset = self._create_dataset()
        runs_url = f"/api/lens/admin/evaluation/datasets/{dataset['uuid']}/runs/"

        response = self.client.post(runs_url, {}, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["data"]["detail"], "EVALUATION_DATASET_EMPTY")

    def test_launch_rejects_when_no_online_node_is_available(self):
        dataset = self._create_dataset()
        cases_url = f"/api/lens/admin/evaluation/datasets/{dataset['uuid']}/cases/"
        self.client.post(cases_url, {"input": "Question"}, format="json")
        self.node.status = LensNode.Status.OFFLINE
        self.node.save(update_fields=["status"])
        runs_url = f"/api/lens/admin/evaluation/datasets/{dataset['uuid']}/runs/"

        response = self.client.post(runs_url, {}, format="json")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json()["data"]["detail"],
            "LENSNODE_EVALUATION_NODE_UNAVAILABLE",
        )

    def test_launch_runs_on_the_shared_default_pool(self):
        GlobalSetting.objects.filter(key="evaluation.execution_pool").update(
            value="default"
        )
        self.node.pool = LensNode.Pool.DEFAULT
        self.node.save(update_fields=["pool"])
        dataset = self._create_dataset()
        cases_url = f"/api/lens/admin/evaluation/datasets/{dataset['uuid']}/cases/"
        self.client.post(cases_url, {"input": "Question"}, format="json")
        runs_url = f"/api/lens/admin/evaluation/datasets/{dataset['uuid']}/runs/"

        with patch("lens.evaluation.engine.batch._enqueue_run"):
            response = self.client.post(runs_url, {}, format="json")

        self.assertEqual(response.status_code, 201, response.content)
        run = Run.objects.get(source=Run.Source.EVALUATION)
        self.assertEqual(run.lensnode, self.node)

    def test_metrics_endpoint_lists_defaults_after_creation(self):
        response = self.client.post(
            "/api/lens/admin/evaluation/metrics/",
            {
                "name": "custom_tool_success",
                "category": "reliability",
                "evaluator_type": "tool_success",
                "weight": "0.5",
                "threshold": "0.9",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)

        listed = self.client.get("/api/lens/admin/evaluation/metrics/")

        self.assertEqual(listed.status_code, 200)
        names = [item["name"] for item in listed.json()["data"]["results"]]
        self.assertIn("custom_tool_success", names)
        self.assertIn("execution_success", names)
        self.assertIn("latency_budget", names)

    def test_builtin_metric_cannot_be_deleted(self):
        listed = self.client.get("/api/lens/admin/evaluation/metrics/").json()["data"]["results"]
        metric = next(item for item in listed if item["name"] == "execution_success")

        response = self.client.delete(
            f"/api/lens/admin/evaluation/metrics/{metric['uuid']}/"
        )

        self.assertEqual(response.status_code, 409, response.content)
