"""Platform-only task operations and effective scheduler configuration."""

from unittest.mock import patch

from agentcore_task.adapters.django.models import TaskConfig, TaskExecution
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from django_celery_beat.models import PeriodicTask
from rest_framework.test import APIClient

from accounts.models import AuditLog, Profile, Tenant, TenantMembership
from lens.models import DataSource


class PlatformTaskTests(TestCase):
    """Exercise permissions through every public agentcore task route."""

    @classmethod
    def setUpTestData(cls):
        """Create an explicit platform role and unrelated tenant staff."""
        cls.platform = get_user_model().objects.create_user(username="task-platform")
        Profile.objects.update_or_create(user=cls.platform, defaults={"platform_role": "super_admin"})
        cls.staff = get_user_model().objects.create_user(username="task-staff", is_staff=True)
        cls.member = get_user_model().objects.create_user(username="task-member")
        cls.tenant = Tenant.objects.create(name="Task tenant", slug="task-tenant")
        TenantMembership.objects.create(tenant=cls.tenant, user=cls.staff, role="tenant_admin")
        cls.datasource = DataSource.objects.create(name="Task source", tenant=cls.tenant, source_type="git")
        cls.task = TaskExecution.objects.create(
            task_id="platform-task",
            task_name="lens.tasks.sync_datasource",
            module="lens_datasource",
            created_by=cls.staff,
            task_args=["secret"],
            task_kwargs={"password": "secret"},
            traceback="private traceback",
            result={"secret": "value"},
            metadata={"datasource_uuid": str(cls.datasource.uuid), "progress_percent": 42, "secret": "value"},
        )

    def setUp(self):
        """Use a fresh API client for each role."""
        self.client = APIClient()
        self.client.force_authenticate(self.platform)

    def test_tenant_staff_and_anonymous_cannot_access_any_global_task_route(self):
        """Owner filters, format suffixes and inherited actions cannot bypass role checks."""
        base = "/api/v1/tasks/executions/"
        routes = [
            base,
            base + "?my_tasks=false",
            base + "?created_by=1",
            base + "stats/",
            base + "my-tasks/",
            base + "status/?task_id=platform-task&sync=true",
            base + "by-task-id/platform-task/",
            base + f"{self.task.pk}/",
            "/api/v1/tasks/executions.json",
            "/api/v1/tasks/config/",
        ]
        for user, code in [(self.staff, 403), (self.member, 403), (None, 401)]:
            self.client.force_authenticate(user)
            for route in routes:
                with self.subTest(user=user, route=route):
                    self.assertEqual(self.client.get(route).status_code, code)
            self.assertEqual(self.client.post(base + f"{self.task.pk}/sync/").status_code, code)
            self.assertEqual(self.client.patch("/api/v1/tasks/config/", {"retention_days": 60}).status_code, code)

    def test_sync_actions_are_platform_accessible_and_audited_without_task_bodies(self):
        """Each explicit or read-triggered Celery synchronization is recorded."""
        base = "/api/v1/tasks/executions/"
        with patch(
            "agentcore_task.adapters.django.views.task.TaskTracker.sync_task_from_celery", return_value=self.task
        ):
            response = self.client.post(base + f"{self.task.pk}/sync/?my_tasks=false")
        self.assertEqual(response.status_code, 200)
        with patch("agentcore_task.adapters.django.views.task.TaskTracker.get_task", return_value=self.task):
            self.assertEqual(self.client.get(base + "status/?task_id=platform-task").status_code, 200)
            self.assertEqual(self.client.get(base + "by-task-id/platform-task/").status_code, 200)
        logs = AuditLog.objects.filter(resource_type="task")
        self.assertEqual(logs.count(), 3)
        for log in logs:
            self.assertEqual(log.actor_user_id, self.platform.pk)
            self.assertNotIn("secret", str(log.metadata))

    def test_invalid_config_is_atomic_and_unknown_fields_are_rejected(self):
        """A bad cron must not persist the earlier valid numeric field."""
        url = "/api/v1/tasks/config/"
        before = self.client.get(url).data
        for payload in [
            {"retention_days": 70, "cleanup_crontab": "not a cron"},
            {"retention_days": 70, "max_retries": 5},
            {"cleanup_crontab": ""},
        ]:
            self.assertEqual(self.client.patch(url, payload, format="json").status_code, 400)
            self.assertEqual(self.client.get(url).data, before)
        self.assertFalse(TaskConfig.objects.filter(key="retention_days").exists())
        self.assertFalse(AuditLog.objects.filter(resource_type="task_config").exists())

    def test_config_updates_real_beat_schedule_and_preserves_cron_when_disabled(self):
        """The switch controls scheduling, while all four parameters survive reloads."""
        payload = {
            "timeout_minutes": 17,
            "retention_days": 90,
            "cleanup_crontab": "15 3 * * *",
            "mark_timeout_crontab": "*/10 * * * *",
            "cleanup_enabled": False,
            "mark_timeout_enabled": True,
        }
        response = self.client.patch("/api/v1/tasks/config/", payload, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        for key, value in payload.items():
            self.assertEqual(response.data[key], value)
        task = PeriodicTask.objects.get(name="agentcore-task-cleanup-old-executions")
        self.assertFalse(task.enabled)
        self.assertEqual(task.crontab.minute, "15")
        self.assertEqual(task.crontab.hour, "3")
        self.assertEqual(TaskConfig.objects.get(scope="global", user=None, key="retention_days").value, 90)
        self.client.patch("/api/v1/tasks/config/", {"cleanup_enabled": True}, format="json")
        task.refresh_from_db()
        self.assertTrue(task.enabled)
        self.assertEqual(task.crontab.minute, "15")
        self.assertEqual(AuditLog.objects.filter(resource_type="task_config").count(), 2)
        audit = self.client.get("/api/v1/platform/audit", {"action": "task_config"})
        self.assertEqual(audit.status_code, 200)
        self.assertEqual(audit.data["count"], 2)

    def test_unchanged_cron_preserves_scheduler_history_and_operator_options(self):
        """An unrelated parameter save must not reset an existing schedule's execution clock."""
        self.client.patch("/api/v1/tasks/config/", {"cleanup_enabled": True}, format="json")
        task = PeriodicTask.objects.get(name="agentcore-task-cleanup-old-executions")
        last_run = timezone.now()
        task.last_run_at = last_run
        task.queue = "maintenance"
        task.kwargs = '{"custom": true}'
        task.save()
        response = self.client.patch(
            "/api/v1/tasks/config/", {"timeout_minutes": 15, "cleanup_crontab": "0 2 * * *"}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        task.refresh_from_db()
        self.assertEqual(task.last_run_at, last_run)
        self.assertEqual(task.queue, "maintenance")
        self.assertEqual(task.kwargs, '{"custom": true}')

    def test_database_failure_rolls_back_config_and_schedule_together(self):
        """A schedule persistence error cannot leave partially saved configuration."""
        with patch("django_celery_beat.models.PeriodicTask.save", side_effect=RuntimeError("save failed")):
            with self.assertRaises(RuntimeError):
                self.client.patch(
                    "/api/v1/tasks/config/", {"retention_days": 80, "cleanup_enabled": False}, format="json"
                )
        self.assertFalse(TaskConfig.objects.filter(key="retention_days").exists())

    @override_settings(AGENTCORE_TASK_CLEANUP_ENABLED=False)
    def test_runtime_disabled_cleanup_cannot_be_falsely_enabled(self):
        """The UI cannot claim success when the worker configuration forbids cleanup."""
        self.assertFalse(self.client.get("/api/v1/tasks/config/").data["cleanup_enabled"])
        response = self.client.patch("/api/v1/tasks/config/", {"cleanup_enabled": True}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_datasource_task_projection_is_scoped_and_omits_execution_payloads(self):
        """Tenant progress reads remain available without access to global task details."""
        self.client.force_authenticate(self.staff)
        root = f"/api/lens/admin/datasources/{self.datasource.uuid}/sync-tasks/"
        response = self.client.get(root + f"{self.task.pk}/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["metadata"]["progress_percent"], 42)
        for field in ["task_args", "task_kwargs", "traceback", "result", "error"]:
            self.assertNotIn(field, response.data)
        self.assertNotIn("secret", response.data["metadata"])
        listing = self.client.get(root)
        self.assertNotIn("secret", listing.data["results"][0]["metadata"])
        filtered = self.client.get(root + f"{self.task.pk}/?metadata_fields=secret")
        self.assertEqual(filtered.data["metadata"], {})
        other_source = DataSource.objects.create(name="Other", source_type="git", tenant=self.tenant)
        self.assertEqual(
            self.client.get(f"/api/lens/admin/datasources/{other_source.uuid}/sync-tasks/{self.task.pk}/").status_code,
            404,
        )
        outsider = get_user_model().objects.create_user(username="task-outsider", is_staff=True)
        other_tenant = Tenant.objects.create(name="Other", slug="task-other")
        TenantMembership.objects.create(tenant=other_tenant, user=outsider, role="tenant_admin")
        self.client.force_authenticate(outsider)
        self.assertEqual(self.client.get(root + f"{self.task.pk}/").status_code, 404)
        TenantMembership.objects.filter(user=outsider).delete()
        self.client.force_authenticate(get_user_model().objects.get(pk=outsider.pk))
        self.assertEqual(self.client.get(root + f"{self.task.pk}/").status_code, 403)
