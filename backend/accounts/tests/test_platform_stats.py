"""Platform tenant statistics tests."""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Profile, Tenant, TenantMembership
from lens.models import Assistant, Message, Run, Session


class PlatformTenantStatsTests(TestCase):
    """Per-tenant metrics aggregate runs, feedback, members, and usage."""

    def setUp(self):
        user_model = get_user_model()
        self.platform = user_model.objects.create_user(
            username="stats-platform",
            email="stats-platform@example.com",
            password="x",
            is_staff=True,
        )
        profile, _ = Profile.objects.get_or_create(user=self.platform)
        profile.platform_role = Profile.PlatformRole.SUPER_ADMIN
        profile.save(update_fields=["platform_role"])
        self.member = user_model.objects.create_user(
            username="stats-member",
            email="stats-member@example.com",
            password="x",
        )
        self.tenant_a = Tenant.objects.create(name="Acme", slug="acme")
        self.tenant_b = Tenant.objects.create(name="Beta", slug="beta")
        TenantMembership.objects.create(
            tenant=self.tenant_a,
            user=self.platform,
            role=TenantMembership.Role.TENANT_ADMIN,
        )
        TenantMembership.objects.create(
            tenant=self.tenant_b,
            user=self.member,
            role=TenantMembership.Role.MEMBER,
        )
        self.assistant = Assistant.objects.create(
            name="Acme assistant",
            slug="stats-acme-assistant",
            tenant=self.tenant_a,
        )
        self.session = Session.objects.create(
            assistant=self.assistant,
            user=self.platform,
            tenant=self.tenant_a,
            title="t",
        )
        self.start = timezone.now() - timedelta(minutes=30)
        self.finish = self.start + timedelta(seconds=2)
        question = Message.objects.create(
            session=self.session,
            role=Message.Role.USER,
            content="Question",
            sequence=1,
        )
        Run.objects.create(
            session=self.session,
            tenant=self.tenant_a,
            status=Run.Status.DONE,
            feedback=Run.Feedback.POSITIVE,
            input_message=question,
            started_at=self.start,
            finished_at=self.finish,
        )
        Run.objects.create(
            session=self.session,
            tenant=self.tenant_a,
            status=Run.Status.FAILED,
            input_message=question,
            started_at=self.start,
            finished_at=self.finish + timedelta(seconds=2),
        )
        Run.objects.create(
            session=self.session,
            tenant=self.tenant_a,
            status=Run.Status.RUNNING,
            input_message=question,
        )
        try:
            from agentcore_metering.adapters.django.models import LLMUsage

            usage = LLMUsage.objects.create(
                user=self.platform,
                model="test-model",
                total_tokens=120,
                cost=Decimal("0.5"),
                cost_currency="USD",
            )
            from lens.models import LLMUsageScope

            LLMUsageScope.objects.create(usage=usage, tenant=self.tenant_a)
        except Exception:
            pass
        self.client = APIClient()
        self.client.force_authenticate(self.platform)
        self.regular = APIClient()
        self.regular.force_authenticate(self.member)

    def _row(self, slug="acme"):
        response = self.client.get("/api/v1/platform/tenant-stats")
        self.assertEqual(response.status_code, 200, response.data)
        return next(row for row in response.data["results"] if row["slug"] == slug)

    def test_requires_super_admin(self):
        response = self.regular.get("/api/v1/platform/tenant-stats")

        self.assertEqual(response.status_code, 403)

    def test_run_health_metrics(self):
        row = self._row()

        self.assertEqual(row["run_count"], 3)
        self.assertEqual(row["failed_count"], 1)
        self.assertEqual(row["running_count"], 1)
        self.assertAlmostEqual(row["fail_rate"], 33.3)
        self.assertEqual(row["like_rate"], 100.0)
        self.assertEqual(row["active_member_count"], 1)
        self.assertEqual(row["avg_duration_ms"], 3000)
        self.assertEqual(row["p95_duration_ms"], 4000)

    def test_usage_and_member_counts(self):
        row = self._row()

        self.assertEqual(row["member_count"], 1)
        self.assertEqual(row["assistant_count"], 1)
        self.assertEqual(row["tokens"], 120)
        self.assertEqual(row["costs"].get("USD"), 0.5)

    def test_empty_tenant_has_null_rates(self):
        row = self._row("beta")

        self.assertEqual(row["run_count"], 0)
        self.assertIsNone(row["fail_rate"])
        self.assertIsNone(row["p95_duration_ms"])
        self.assertEqual(row["tokens"], 0)
