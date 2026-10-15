"""Regression coverage for lean task execution list reads."""

from agentcore_task.adapters.django.models import TaskExecution
from agentcore_task.constants import TaskStatus
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import resolve
from rest_framework.test import APIClient


class TaskExecutionReadPerformanceTests(TestCase):
    """Exercise the public task routes and their database read footprint."""

    @classmethod
    def setUpTestData(cls):
        """Create task payloads that must remain available on detail reads."""

        user_model = get_user_model()
        cls.user = user_model.objects.create_user(username="task-list-owner")
        cls.other_user = user_model.objects.create_user(username="task-list-other")
        cls.tasks = []
        for index, (owner, status) in enumerate(
            [
                (cls.user, TaskStatus.SUCCESS),
                (cls.user, TaskStatus.SUCCESS),
                (cls.user, TaskStatus.FAILURE),
                (cls.other_user, TaskStatus.SUCCESS),
            ]
        ):
            cls.tasks.append(
                TaskExecution.objects.create(
                    task_id=f"task-list-{index}",
                    task_name="lens.tasks.sync_datasource",
                    module="lens",
                    status=status,
                    created_by=owner,
                    task_args=["argument" * 1000],
                    task_kwargs={"input": "value" * 1000},
                    result={"output": "result" * 1000},
                    error="error" * 1000,
                    traceback="traceback" * 1000,
                    metadata={
                        "config_platform": "github" if index == 0 else "gitlab",
                        "config_key": f"repository-{index}",
                        "payload": "metadata" * 1000,
                    },
                )
            )

    def setUp(self):
        """Authenticate without introducing unrelated authentication queries."""

        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.url = "/api/v1/tasks/executions/"

    def test_list_without_metadata_does_not_read_large_columns(self):
        """Omitting metadata must avoid loading unused payloads or lazy rereads."""

        for flag in ("false", "0", "no", "OFF"):
            with self.subTest(include_metadata=flag), CaptureQueriesContext(connection) as queries:
                response = self.client.get(self.url, {"include_metadata": flag})

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data["count"], 3)
            self.assertTrue(all(row["metadata"] is None for row in response.data["results"]))
            self.assertTrue(all(row["created_by_username"] == self.user.username for row in response.data["results"]))
            task_queries = [
                query["sql"] for query in queries if 'FROM "agentcore_task_execution"' in query["sql"]
            ]
            self.assertEqual(len(task_queries), 2, task_queries)
            for sql in task_queries:
                projection = sql.split(" FROM ", 1)[0]
                for field in ("task_args", "task_kwargs", "result", "error", "traceback", "metadata"):
                    self.assertNotIn(f'"agentcore_task_execution"."{field}"', projection)

    def test_list_keeps_metadata_by_default_and_when_requested(self):
        """The existing default and explicit metadata contracts remain intact."""

        for params in ({}, {"include_metadata": "true"}):
            with self.subTest(params=params):
                response = self.client.get(self.url, params)

                self.assertEqual(response.status_code, 200)
                rows = {row["task_id"]: row for row in response.data["results"]}
                self.assertEqual(rows[self.tasks[0].task_id]["metadata"], self.tasks[0].metadata)

        response = self.client.get(self.url, {"include_metadata": "true", "metadata_fields": "config_key"})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(all(set(row["metadata"]) == {"config_key"} for row in response.data["results"]))

    def test_lean_list_preserves_metadata_filters(self):
        """Deferred metadata can still participate in database-side filtering."""

        response = self.client.get(
            self.url,
            {"include_metadata": "false", "config_platform": "github", "config_key": "repository-0"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["task_id"], self.tasks[0].task_id)
        self.assertIsNone(response.data["results"][0]["metadata"])

    def test_lean_list_preserves_owner_filters_and_pagination(self):
        """Default ownership, explicit all-user queries, and page limits survive."""

        params = {"include_metadata": "false", "module": "lens", "status": TaskStatus.SUCCESS, "page_size": 1}
        first = self.client.get(self.url, params)
        second = self.client.get(self.url, {**params, "page": 2})

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.data["count"], 2)
        self.assertEqual(len(first.data["results"]), 1)
        self.assertIsNotNone(first.data["next"])
        self.assertNotEqual(first.data["results"][0]["task_id"], second.data["results"][0]["task_id"])

        all_users = self.client.get(self.url, {**params, "my_tasks": "false"})
        other_user = self.client.get(self.url, {**params, "created_by": self.other_user.pk})

        self.assertEqual(all_users.data["count"], 3)
        self.assertEqual(other_user.data["count"], 1)
        self.assertEqual(other_user.data["results"][0]["created_by_id"], self.other_user.pk)

    def test_list_requires_authentication_and_rejects_writes(self):
        """A list-specific route must retain the plugin's read-only protection."""

        self.assertEqual(self.client.post(self.url, {}, format="json").status_code, 405)
        self.client.force_authenticate(user=None)

        self.assertEqual(self.client.get(self.url).status_code, 401)

    def test_detail_and_action_routes_remain_available(self):
        """Only collection reads change; detail payloads and actions still work."""

        task = self.tasks[0]
        detail = self.client.get(f"{self.url}{task.pk}/")
        stats = self.client.get(f"{self.url}stats/")

        self.assertEqual(detail.status_code, 200)
        for field in ("task_args", "task_kwargs", "result", "error", "traceback", "metadata"):
            self.assertEqual(detail.data[field], getattr(task, field))
        self.assertEqual(stats.status_code, 200)
        self.assertEqual(stats.data["total"], 3)
        self.assertEqual(resolve(f"{self.url}{task.pk}/sync/").func.actions["post"], "sync")
        self.assertEqual(resolve(f"{self.url}my-tasks/").func.actions["get"], "my_tasks")
