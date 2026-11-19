"""Datasource schedule changes preserve content settings and tenant boundaries."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from agentcore_task.adapters.django.models import TaskExecution
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from django_celery_beat.models import PeriodicTask
from rest_framework.test import APIClient

from accounts.models import Profile, Tenant, TenantDelegation, TenantMembership
from lens.models import DataSource, ScheduledTask
from lens.periodic_tasks import ensure_datasource_periodic_task, estimate_datasource_next_run
from lens.serializers import DataSourceSerializer
from lens.tasks import source_sync_task


class DataSourceScheduleTests(TestCase):
    """Exercise real schedule persistence without contacting external providers."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Schedule A", slug="schedule-a")
        self.foreign = Tenant.objects.create(name="Schedule B", slug="schedule-b")
        self.user = get_user_model().objects.create_user(username="scheduler")
        TenantMembership.objects.create(user=self.user, tenant=self.tenant, role="tenant_admin")
        self.source = DataSource.objects.create(
            tenant=self.tenant,
            name="Docs",
            source_type="git",
            config={"repo_url": "https://example.com/docs.git", "branch": "main"},
            sync_policy={"mode": "interval", "interval_seconds": 3600, "conversion": {"document": True}},
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.url = f"/api/lens/admin/datasources/{self.source.uuid}/sync-policy/"

    def test_manual_disables_existing_schedule_without_disabling_source(self):
        """Manual policy stops Beat and the UI schedule and remains manually syncable."""

        record = ensure_datasource_periodic_task(self.source)
        response = self.client.post(self.url, {"mode": "manual"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.source.refresh_from_db()
        record.refresh_from_db()
        self.assertEqual(self.source.status, "active")
        self.assertEqual(self.source.sync_policy, {"mode": "manual", "conversion": {"document": True}})
        self.assertFalse(record.enabled)
        self.assertFalse(PeriodicTask.objects.get(pk=record.periodic_task_ref).enabled)
        record.last_run_at = timezone.now()
        self.assertIsNone(estimate_datasource_next_run(self.source, record))
        self.assertIsNone(ensure_datasource_periodic_task(self.source))
        with patch("lens.views.datasources.DataSourceViewSet._enqueue_datasource_sync") as enqueue:
            enqueue.return_value = SimpleNamespace(id=42, task_id="manual-test")
            response = self.client.post(f"/api/lens/admin/datasources/{self.source.uuid}/sync/")
        self.assertEqual(response.status_code, 202, response.data)
        self.assertEqual(enqueue.call_args.args[2], "manual")

    def test_new_manual_source_has_no_automatic_schedule(self):
        """Reconciliation must not invent an interval for a manual-only source."""

        self.source.sync_policy = {"mode": "manual"}
        self.source.save(update_fields=["sync_policy"])
        self.assertIsNone(ensure_datasource_periodic_task(self.source))
        self.assertFalse(PeriodicTask.objects.filter(name=f"lens-source-sync-{self.source.uuid}").exists())
        with patch("lens.tasks.register_datasource_sync_task") as register:
            self.assertEqual(source_sync_task.run(str(self.source.uuid), "scheduled"), 0)
        register.assert_not_called()
        self.assertFalse(DataSourceSerializer(self.source).data["sync_state"]["enabled"])

    def test_duplicate_sync_returns_the_existing_task_identity(self):
        """A stale selection must not receive a new id for work that was never queued."""

        TaskExecution.objects.create(
            task_id="existing-sync",
            task_name="sync",
            module="lens_datasource",
            status="PENDING",
            metadata={"datasource_uuid": str(self.source.uuid)},
        )
        with patch("lens.views.datasources.source_sync_task.apply_async") as enqueue:
            response = self.client.post(f"/api/lens/admin/datasources/{self.source.uuid}/sync/")
        self.assertEqual(response.status_code, 202, response.data)
        self.assertEqual(response.data["task_id"], "existing-sync")
        enqueue.assert_not_called()

    def test_delete_rejects_upload_conversion_and_sync_work_in_progress(self):
        """The server rechecks all task kinds even when the selection was made earlier."""

        for module in ("lens_datasource", "lens_datasource_conversion", "lens_datasource_upload"):
            for status in ("PENDING", "STARTED", "CANCELLING"):
                with self.subTest(module=module, status=status):
                    task = TaskExecution.objects.create(
                        task_id=f"{module}-{status}",
                        task_name="in-progress",
                        module=module,
                        status=status,
                        metadata={"datasource_uuid": str(self.source.uuid)},
                    )
                    response = self.client.delete(f"/api/lens/admin/datasources/{self.source.uuid}/")
                    self.assertEqual(response.status_code, 409, response.data)
                    self.assertTrue(DataSource.objects.filter(pk=self.source.pk).exists())
                    task.delete()

    def test_manual_policy_round_trips_through_full_datasource_editor(self):
        """The ordinary edit API must accept manual schedules without losing conversion."""

        serializer = DataSourceSerializer(
            self.source, data={"sync_policy": {"mode": "manual", "conversion": {"document": True}}}, partial=True
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()
        self.source.refresh_from_db()
        self.assertEqual(self.source.sync_policy, {"mode": "manual", "conversion": {"document": True}})

    def test_changing_schedule_does_not_discard_previously_admitted_work(self):
        """A queued scheduled task with an issued task id can still complete."""

        self.source.sync_policy = {"mode": "manual"}
        self.source.save(update_fields=["sync_policy"])
        completed = SimpleNamespace(task_id="admitted", status="SUCCESS")
        with patch("lens.tasks.register_datasource_sync_task", return_value=completed) as register:
            self.assertEqual(source_sync_task.run(str(self.source.uuid), "scheduled", "admitted"), 0)
        register.assert_called_once()
        record = ScheduledTask.objects.get(target_id=self.source.uuid, task_type="source_sync")
        self.assertFalse(record.enabled)

    def test_interval_and_cron_reenable_schedule_and_keep_conversion(self):
        """Each prototype preset changes only scheduling fields."""

        self.client.post(self.url, {"mode": "manual"}, format="json")
        for policy in (
            {"mode": "interval", "interval_seconds": 3600},
            {"mode": "interval", "interval_seconds": 21600},
            {"mode": "crontab", "cron": "0 2 * * *", "timezone": "Asia/Shanghai"},
        ):
            response = self.client.post(self.url, policy, format="json")
            self.assertEqual(response.status_code, 200, response.data)
            self.source.refresh_from_db()
            self.assertEqual(self.source.sync_policy, {**policy, "conversion": {"document": True}})
            record = ScheduledTask.objects.get(target_id=self.source.uuid, task_type="source_sync")
            self.assertTrue(record.enabled)
            self.assertTrue(PeriodicTask.objects.get(pk=record.periodic_task_ref).enabled)

    def test_invalid_policy_is_rejected_without_changing_settings(self):
        """Reject invalid schedules and attempts to overwrite processing or ownership."""

        original = self.source.sync_policy
        for payload in (
            {"mode": "other"},
            {"mode": "interval", "interval_seconds": 1},
            {"mode": "crontab", "cron": "99 2 * * *", "timezone": "UTC"},
            {"mode": "crontab", "cron": "0 2 * * *", "timezone": "Missing/Zone"},
            {"mode": "manual", "conversion": {}},
            {"mode": "manual", "tenant": str(self.foreign.uuid)},
        ):
            response = self.client.post(self.url, payload, format="json")
            self.assertEqual(response.status_code, 400, response.data)
            self.source.refresh_from_db()
            self.assertEqual(self.source.sync_policy, original)

    def test_upload_and_managed_sources_do_not_accept_schedules(self):
        """Manual data containers cannot gain automatic sync jobs."""

        for source_type in ("upload", "managed_workspace"):
            self.source.source_type = source_type
            self.source.save(update_fields=["source_type"])
            self.assertEqual(self.client.post(self.url, {"mode": "manual"}, format="json").status_code, 409)

    def test_foreign_and_member_requests_cannot_change_policy(self):
        """The dedicated endpoint retains ordinary tenant administration scope."""

        self.source.tenant = self.foreign
        self.source.save(update_fields=["tenant"])
        self.assertEqual(self.client.post(self.url, {"mode": "manual"}, format="json").status_code, 404)
        TenantMembership.objects.filter(user=self.user).update(role="member")
        self.client.force_authenticate(get_user_model().objects.get(pk=self.user.pk))
        self.assertEqual(self.client.post(self.url, {"mode": "manual"}, format="json").status_code, 403)

    def test_policy_changes_preserve_disabled_status_and_manual_resume(self):
        """Changing frequency does not resume a paused source or schedule a manual one."""

        enabled_url = f"/api/lens/admin/datasources/{self.source.uuid}/set-enabled/"
        self.assertEqual(self.client.post(enabled_url, {"enabled": False}, format="json").status_code, 200)
        self.assertEqual(self.client.post(self.url, {"mode": "manual"}, format="json").status_code, 200)
        self.source.refresh_from_db()
        self.assertEqual(self.source.status, "disabled")
        self.assertEqual(self.client.post(enabled_url, {"enabled": True}, format="json").status_code, 200)
        self.source.refresh_from_db()
        self.assertEqual(self.source.status, "active")
        self.assertIsNone(ensure_datasource_periodic_task(self.source))

    def test_readonly_delegation_cannot_change_policy(self):
        """Read-only delegation cannot write through the schedule endpoint."""

        platform = get_user_model().objects.create_user(username="schedule-platform")
        Profile.objects.update_or_create(user=platform, defaults={"platform_role": "super_admin"})
        grant = TenantDelegation.objects.create(
            platform_user=platform,
            tenant=self.tenant,
            reason="Inspect schedule",
            mode="readonly",
            expires_at=timezone.now() + timedelta(minutes=5),
        )
        self.client.force_authenticate(platform)
        response = self.client.post(self.url, {"mode": "manual"}, format="json", HTTP_X_DELEGATION_ID=str(grant.uuid))
        self.assertEqual(response.status_code, 403)

    def test_detail_settings_merge_only_requested_fields(self):
        """Saving the detail form preserves concurrent conversion and content settings."""

        self.source.sync_policy["conversion"].update(image=True, max_pages=120)
        self.source.save(update_fields=["sync_policy"])
        url = f"/api/lens/admin/datasources/{self.source.uuid}/settings/"
        response = self.client.post(
            url,
            {
                "schedule": {"mode": "manual"},
                "max_file_size_mb": 30,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.source.refresh_from_db()
        self.assertEqual(
            self.source.sync_policy,
            {
                "mode": "manual",
                "conversion": {"document": True, "image": True, "max_pages": 120, "max_file_size_mb": 30},
            },
        )
        response = self.client.post(url, {"max_file_size_mb": None}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.source.refresh_from_db()
        self.assertNotIn("max_file_size_mb", self.source.sync_policy["conversion"])
        self.assertEqual(self.source.sync_policy["mode"], "manual")

    def test_detail_settings_are_atomic_and_reject_unsupported_fields(self):
        """Bad content settings must not partly apply an otherwise valid schedule."""

        url = f"/api/lens/admin/datasources/{self.source.uuid}/settings/"
        original = self.source.sync_policy
        for payload in (
            {},
            {"max_file_size_mb": 0},
            {"max_file_size_mb": True},
            {"max_file_size_mb": "inf"},
            {"max_file_size_mb": "nan"},
            {"max_file_size_mb": 12.5},
            {"max_file_size_mb": -1},
            {"schedule": {"mode": "manual"}, "max_file_size_mb": "bad"},
            {"schedule": {"mode": "interval", "interval_seconds": 1}},
            {"retry_count": 3},
            {"conversion": {"document": False}},
            {"tenant": str(self.foreign.uuid)},
        ):
            response = self.client.post(url, payload, format="json")
            self.assertEqual(response.status_code, 400, response.data)
            self.source.refresh_from_db()
            self.assertEqual(self.source.sync_policy, original)

    def test_manual_containers_allow_file_limit_but_never_schedules(self):
        """Upload and workspace settings remain conversion-only."""

        url = f"/api/lens/admin/datasources/{self.source.uuid}/settings/"
        for source_type in ("upload", "managed_workspace"):
            self.source.source_type = source_type
            self.source.sync_policy = {"conversion": {"document": True}}
            self.source.save(update_fields=["source_type", "sync_policy"])
            response = self.client.post(url, {"max_file_size_mb": 12}, format="json")
            self.assertEqual(response.status_code, 200, response.data)
            self.source.refresh_from_db()
            self.assertEqual(self.source.sync_policy, {"conversion": {"document": True, "max_file_size_mb": 12}})
            response = self.client.post(url, {"schedule": {"mode": "manual"}, "max_file_size_mb": 40}, format="json")
            self.assertEqual(response.status_code, 409, response.data)
            self.source.refresh_from_db()
            self.assertEqual(self.source.sync_policy["conversion"]["max_file_size_mb"], 12)

    def test_detail_settings_retain_tenant_and_delegation_boundaries(self):
        """The inline settings endpoint cannot bypass resource or write permissions."""

        url = f"/api/lens/admin/datasources/{self.source.uuid}/settings/"
        payload = {"max_file_size_mb": 30}
        self.source.tenant = self.foreign
        self.source.save(update_fields=["tenant"])
        self.assertEqual(self.client.post(url, payload, format="json").status_code, 404)
        self.source.tenant = self.tenant
        self.source.save(update_fields=["tenant"])
        TenantMembership.objects.filter(user=self.user).update(role="member")
        self.client.force_authenticate(get_user_model().objects.get(pk=self.user.pk))
        self.assertEqual(self.client.post(url, payload, format="json").status_code, 403)
        platform = get_user_model().objects.create_user(username="detail-platform")
        Profile.objects.update_or_create(user=platform, defaults={"platform_role": "super_admin"})
        grant = TenantDelegation.objects.create(
            platform_user=platform,
            tenant=self.tenant,
            reason="Inspect settings",
            mode="readonly",
            expires_at=timezone.now() + timedelta(minutes=5),
        )
        self.client.force_authenticate(platform)
        response = self.client.post(url, payload, format="json", HTTP_X_DELEGATION_ID=str(grant.uuid))
        self.assertEqual(response.status_code, 403)

    def test_history_conversion_counts_exclude_internal_metadata(self):
        """Timeline results expose conversion counters without provider or model payloads."""

        TaskExecution.objects.create(
            task_id="timeline-conversion",
            task_name="conversion",
            module="lens_datasource_conversion",
            status="SUCCESS",
            metadata={
                "datasource_uuid": str(self.source.uuid),
                "conversion_summary": {
                    "failed": 2,
                    "skipped": 0,
                    "warnings": ["private"],
                    "config": {"secret": "hidden"},
                },
                "internal_secret": "hidden",
            },
        )
        response = self.client.get(
            f"/api/lens/admin/datasources/{self.source.uuid}/sync-tasks/",
            {"task_type": "lens_datasource_all", "metadata_fields": "conversion_summary,internal_secret"},
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["results"][0]["metadata"], {"conversion_summary": {"failed": 2, "skipped": 0}})
