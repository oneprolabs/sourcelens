"""Platform dashboard statistics must reflect real, bounded aggregate data."""

from datetime import timedelta
from decimal import Decimal

from agentcore_metering.adapters.django.models import LLMUsage
from agentcore_notifier.adapters.django.models import NotificationRecord
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Profile, Tenant
from lens.models import Assistant, LensNode, LLMUsageScope, Message, NotificationRecordScope, Run, Session


class PlatformOverviewTests(TestCase):
    """Exercise the dashboard endpoint with production ownership and run models."""

    def setUp(self):
        self.platform = get_user_model().objects.create_user(username="overview-platform")
        Profile.objects.update_or_create(user=self.platform, defaults={"platform_role": "super_admin"})
        self.client = APIClient()
        self.client.force_authenticate(self.platform)
        self.seeded_active_tenants = Tenant.objects.filter(status=Tenant.Status.ACTIVE).count()
        self.tenant = Tenant.objects.create(name="Tenant A", slug="overview-a")
        self.quiet = Tenant.objects.create(name="Quiet", slug="overview-quiet")
        self.assistant = Assistant.objects.create(
            name="Private assistant", slug="overview-assistant", tenant=self.tenant
        )
        self.session = Session.objects.create(
            tenant=self.tenant, assistant=self.assistant, user=self.platform, title="Private conversation"
        )
        self.message = Message.objects.create(session=self.session, role="user", content="Private prompt", sequence=1)

    def run_at(self, age, source="user", tenant=None):
        """Create a dated request without dispatching work."""

        run = Run.objects.create(
            tenant=tenant or self.tenant, session=self.session, input_message=self.message, source=source, status="done"
        )
        Run.objects.filter(pk=run.pk).update(created_at=timezone.now() - age)
        return run

    def usage(self, model, tokens, tenant, currency="USD", cost=Decimal("1.25")):
        """Attach immutable ownership rather than inferring user membership."""

        usage = LLMUsage.objects.create(model=model, total_tokens=tokens, cost=cost, cost_currency=currency)
        LLMUsageScope.objects.create(usage=usage, tenant=tenant)
        return usage

    def overview(self):
        """Retrieve the real platform dashboard response."""

        response = self.client.get("/api/v1/platform/overview")
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_normal_tenant_count_is_lifecycle_status_not_recent_activity(self):
        Tenant.objects.create(name="Pending", slug="overview-pending", status="pending")
        self.run_at(timedelta(hours=1))
        data = self.overview()
        self.assertEqual(data["active_tenant_count"], self.seeded_active_tenants + 2)
        self.assertEqual(data["engaged_tenant_count_7d"], 1)

    def test_questions_exclude_evaluations_future_and_previous_window(self):
        self.run_at(timedelta(hours=1))
        self.run_at(timedelta(days=2), source="evaluation")
        self.run_at(timedelta(days=8))
        self.run_at(timedelta(days=9))
        self.run_at(timedelta(days=-1))
        data = self.overview()
        self.assertEqual(data["run_count_7d"], 1)
        self.assertEqual(data["run_count_previous_7d"], 2)
        self.assertEqual(data["run_change_percent"], -50.0)
        self.assertEqual(len(data["question_series_30d"]), 30)
        self.assertEqual(sum(row["count"] for row in data["question_series_30d"]), 3)
        self.assertTrue(any(row["count"] == 0 for row in data["question_series_30d"]))

    def test_question_counts_match_tenant_list_and_statistics(self):
        self.run_at(timedelta(hours=1))
        self.run_at(timedelta(hours=2), source="evaluation")
        self.run_at(timedelta(days=-1))
        for endpoint, count_field in (("tenants", "run_count_7d"), ("tenant-stats", "run_count")):
            response = self.client.get(f"/api/v1/platform/{endpoint}")
            row = next(row for row in response.data["results"] if row["uuid"] == str(self.tenant.uuid))
            self.assertEqual(row[count_field], self.overview()["run_count_7d"])

    def test_ranking_is_bounded_and_never_returns_tenant_content(self):
        self.run_at(timedelta(hours=1))
        self.run_at(timedelta(hours=2))
        for index in range(6):
            Tenant.objects.create(name=f"Other {index}", slug=f"overview-other-{index}")
        data = self.overview()
        self.assertEqual(len(data["top_tenants_7d"]), 5)
        self.assertEqual(
            data["top_tenants_7d"][0], {"uuid": str(self.tenant.uuid), "name": self.tenant.name, "run_count": 2}
        )
        self.assertNotIn("Private prompt", str(data))
        self.assertNotIn("Private conversation", str(data))
        self.assertNotIn("Private assistant", str(data))

    def test_usage_groups_models_and_separates_currencies_with_immutable_scope(self):
        self.usage("shared-model", 30, self.tenant)
        self.usage("shared-model", 20, None, currency="CNY", cost=Decimal("2.5"))
        self.usage("other-model", 10, self.quiet, cost=None)
        LLMUsage.objects.create(model="unknown-owner", total_tokens=900)
        data = self.overview()
        self.assertEqual(data["tokens_7d"], 60)
        self.assertEqual(data["costs_7d"], {"USD": 1.25, "CNY": 2.5})
        self.assertEqual(data["unpriced_calls_7d"], 1)
        self.assertEqual(data["model_usage_7d"][0]["model"], "shared-model")
        self.assertEqual(data["model_usage_7d"][0]["tokens"], 50)

    def test_model_distribution_keeps_other_bucket_without_losing_tokens(self):
        for index in range(8):
            self.usage(f"model-{index}", index + 1, self.tenant)
        distribution = self.overview()["model_usage_7d"]
        self.assertEqual(len(distribution), 6)
        self.assertEqual(sum(row["tokens"] for row in distribution), 36)
        self.assertTrue(distribution[-1]["other"])
        self.assertEqual(distribution[-1]["tokens"], 6)

    def test_platform_attention_excludes_tenant_notifications_and_unapproved_nodes(self):
        LensNode.objects.create(name="Offline shared", status="offline", enrollment_status="approved")
        LensNode.objects.create(name="Pending node", status="offline", enrollment_status="pending")
        old = Tenant.objects.create(name="Old pending", slug="overview-old", status="pending")
        Tenant.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=9))
        for tenant in (None, self.tenant):
            record = NotificationRecord.objects.create(
                source_app="overview", provider_type="email", status="failed", payload={}
            )
            NotificationRecordScope.objects.create(record=record, tenant=tenant)
        data = self.overview()
        self.assertEqual(data["attention"]["offline_node_count"], 1)
        self.assertEqual(data["attention"]["notification_failure_count_7d"], 1)
        self.assertEqual(data["attention"]["stale_pending_tenant_count"], 1)
        self.assertEqual(data["node_total_count"], 1)

    def test_empty_activity_has_no_fabricated_change_or_model_distribution(self):
        data = self.overview()
        self.assertIsNone(data["run_change_percent"])
        self.assertEqual(data["model_usage_7d"], [])
        self.assertEqual(data["costs_7d"], {})

    def test_staff_without_platform_role_cannot_read_dashboard(self):
        staff = get_user_model().objects.create_user(username="overview-staff", is_staff=True)
        self.client.force_authenticate(staff)
        self.assertEqual(self.client.get("/api/v1/platform/overview").status_code, 403)
