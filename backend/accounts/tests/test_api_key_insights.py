"""Tenant key details and prototype lifecycle risk summaries."""

import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Profile, Tenant, TenantApiKey, TenantApiKeyCall, TenantMembership
from lens.models import Assistant, LensNode, Message, Run, Session


class ApiKeyInsightsTests(TestCase):
    """Report complete tenant risks without exposing secrets or other tenant resources."""

    def setUp(self):
        """Create independent credentials and human administrators."""
        self.now = timezone.now()
        self.tenant = Tenant.objects.create(name="Insights", slug="key-insights")
        self.other = Tenant.objects.create(name="Other", slug="other-key-insights")
        self.admin = get_user_model().objects.create_user(username="key-insights-admin")
        self.membership = TenantMembership.objects.create(tenant=self.tenant, user=self.admin, role="tenant_admin")
        self.client = APIClient()
        self.client.force_authenticate(self.admin)
        self.platform = get_user_model().objects.create_user(username="key-insights-platform")
        Profile.objects.update_or_create(user=self.platform, defaults={"platform_role": "super_admin"})

    def issue(self, **overrides):
        """Issue a recently used credential unless a test asks for another lifecycle."""
        values = {
            "tenant": self.tenant,
            "created_by": self.admin,
            "name": "Integration",
            "scope": "all",
            "assistant_uuids": [],
            "expires_at": self.now + timedelta(days=10),
        }
        last_used = overrides.pop("last_used_at", self.now)
        state = overrides.pop("status", "active")
        values.update(overrides)
        key, secret = TenantApiKey.issue(**values)
        TenantApiKey.objects.filter(pk=key.pk).update(last_used_at=last_used, status=state)
        key.refresh_from_db()
        return key, secret

    def test_risk_summary_is_not_limited_to_the_filtered_page(self):
        """The warning counts use all effective active credentials in the tenant."""
        self.issue(name="Soon", expires_at=self.now + timedelta(days=7), last_used_at=None)
        self.issue(name="Stale", last_used_at=self.now - timedelta(days=30, seconds=1))
        self.issue(name="Boundary", last_used_at=self.now - timedelta(days=30))
        for state in ("disabled", "revoked"):
            self.issue(status=state, expires_at=self.now + timedelta(days=1), last_used_at=None)
        self.issue(expires_at=self.now, last_used_at=None)
        self.issue(tenant=self.other, expires_at=self.now + timedelta(days=1), last_used_at=None)
        with patch("django.utils.timezone.now", return_value=self.now):
            response = self.client.get("/api/v1/tenant/api-keys", {"page_size": 1, "search": "Boundary"})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["summary"], {"expiring_soon": 1, "unused": 2})
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(len(response.data["results"]), 1)

    def test_platform_summary_respects_the_selected_tenant(self):
        """Platform aggregates do not change their scope when paginating or filtering state."""
        self.issue(expires_at=self.now + timedelta(days=1), last_used_at=None)
        self.issue(tenant=self.other, last_used_at=None)
        self.client.force_authenticate(self.platform)
        response = self.client.get("/api/v1/platform/api-keys", {"tenant": str(self.other.pk), "status": "disabled"})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["summary"], {"expiring_soon": 0, "unused": 1})
        self.assertEqual(response.data["count"], 0)

    def test_detail_returns_only_safe_metadata_and_own_assistant_labels(self):
        """Even an old corrupted scope must not disclose another tenant's assistant name."""
        assistant = Assistant.objects.create(name="Our assistant", slug="insights-ours", tenant=self.tenant)
        foreign = Assistant.objects.create(name="Private foreign", slug="insights-foreign", tenant=self.other)
        key, secret = self.issue(scope="selected", assistant_uuids=[str(assistant.uuid), str(foreign.uuid)])
        response = self.client.get(f"/api/v1/tenant/api-keys/{key.pk}")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["assistants"], [{"uuid": str(assistant.uuid), "name": assistant.name}])
        self.assertNotIn("Private foreign", str(response.data))
        self.assertNotIn(secret, str(response.data))
        self.assertNotIn(key.key_hash, str(response.data))
        self.assertEqual(response.data["calls_7d"], 0)
        self.assertEqual(response.data["recent_calls"], [])

    def test_detail_is_not_a_platform_delegation_or_member_read_surface(self):
        """The same explicit tenant administrator gate applies to the new read action."""
        key, secret = self.issue()
        foreign, _ = self.issue(tenant=self.other)
        self.assertEqual(self.client.get(f"/api/v1/tenant/api-keys/{foreign.pk}").status_code, 404)
        self.membership.role = "member"
        self.membership.save()
        self.admin.is_staff = True
        self.admin.save()
        self.assertEqual(self.client.get(f"/api/v1/tenant/api-keys/{key.pk}").status_code, 403)
        self.client.force_authenticate(self.platform)
        self.assertEqual(self.client.get(f"/api/v1/tenant/api-keys/{key.pk}").status_code, 403)
        external = APIClient()
        external.credentials(HTTP_AUTHORIZATION=f"Bearer {secret}")
        self.assertEqual(external.get(f"/api/v1/tenant/api-keys/{key.pk}").status_code, 403)

    def test_real_http_execution_records_validation_failure_without_request_content(self):
        """Only execution requests count; discovery, polling and browser authentication do not."""
        assistant = Assistant.objects.create(name="Callable", slug="insights-callable", tenant=self.tenant)
        key, secret = self.issue()
        session = Session.objects.create(tenant=self.tenant, api_key=key, assistant=assistant)
        external = APIClient()
        external.credentials(HTTP_AUTHORIZATION=f"Bearer {secret}")
        self.assertEqual(external.get("/api/lens/assistants/").status_code, 200)
        response = external.post(
            f"/api/lens/sessions/{session.uuid}/runs/?sensitive=do-not-store",
            {"question": "", "private_value": "never-record-this"},
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.data)
        call = TenantApiKeyCall.objects.get(key=key)
        self.assertEqual((call.status_code, call.assistant_uuid, call.key_version), (400, assistant.uuid, 1))
        self.assertGreaterEqual(call.duration_ms, 0)
        self.assertIsNone(call.run_uuid)
        detail = self.client.get(f"/api/v1/tenant/api-keys/{key.pk}")
        self.assertEqual(detail.data["calls_7d"], 1)
        self.assertEqual(detail.data["recent_calls"][0]["assistant_name"], "Callable")
        for private in ("never-record-this", "do-not-store", secret, key.key_hash):
            self.assertNotIn(private, str(detail.data))
        self.client.post(f"/api/lens/sessions/{session.uuid}/runs/", {}, format="json")
        self.assertEqual(TenantApiKeyCall.objects.count(), 1)

    def test_real_accepted_run_records_key_source_and_live_execution_status(self):
        """A 201 is an accepted asynchronous run, not a completed answer."""
        node = LensNode.objects.create(name="Insights node", status="online")
        assistant = Assistant.objects.create(name="Runnable", slug="insights-run", tenant=self.tenant, lensnode=node)
        key, secret = self.issue()
        session = Session.objects.create(tenant=self.tenant, api_key=key, assistant=assistant)
        external = APIClient()
        external.credentials(HTTP_AUTHORIZATION=f"Bearer {secret}")
        response = external.post(
            f"/api/lens/sessions/{session.uuid}/runs/",
            {"question": "Private question must not enter accounting", "enqueue": False},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        run = Run.objects.get(uuid=response.data["uuid"])
        call = TenantApiKeyCall.objects.get(key=key)
        self.assertEqual(call.run_uuid, run.uuid)
        self.assertEqual(call.assistant_uuid, assistant.uuid)
        Run.objects.filter(pk=run.pk).update(
            status="failed",
            error="Private model error",
            started_at=self.now,
            finished_at=self.now + timedelta(seconds=5),
        )
        detail = self.client.get(f"/api/v1/tenant/api-keys/{key.pk}").data
        self.assertEqual(detail["recent_calls"][0]["run_status"], "failed")
        self.assertEqual(detail["recent_calls"][0]["execution_duration_ms"], 5000)
        self.assertNotIn("Private question", str(detail))
        self.assertNotIn("Private model error", str(detail))

    def test_foreign_or_forbidden_target_is_not_identified_in_accounting(self):
        """Failed scope probes cannot be used to discover assistant names."""
        ours = Assistant.objects.create(name="Own target", slug="insights-own-target", tenant=self.tenant)
        foreign = Assistant.objects.create(name="Foreign target", slug="insights-foreign-target", tenant=self.other)
        target = Session.objects.create(tenant=self.other, assistant=foreign, user=self.admin)
        key, secret = self.issue(scope="selected", assistant_uuids=[str(ours.uuid)])
        external = APIClient()
        external.credentials(HTTP_AUTHORIZATION=f"Bearer {secret}")
        response = external.post(f"/api/lens/sessions/{target.uuid}/runs/", {}, format="json")
        self.assertEqual(response.status_code, 404)
        call = TenantApiKeyCall.objects.get(key=key)
        self.assertIsNone(call.assistant_uuid)
        self.assertIsNone(call.run_uuid)
        detail = self.client.get(f"/api/v1/tenant/api-keys/{key.pk}").data
        self.assertNotIn("Foreign target", str(detail))
        self.assertNotIn(str(target.uuid), str(detail))

    def test_recent_and_seven_day_counts_are_bounded_and_tenant_scoped(self):
        """Old calls and corrupted foreign-tenant rows do not inflate current usage."""
        key, _ = self.issue()
        for code in [201] * 8 + [400]:
            TenantApiKeyCall.objects.create(
                key=key, tenant=self.tenant, key_version=1, status_code=code, duration_ms=10
            )
        old = TenantApiKeyCall.objects.filter(key=key).first()
        TenantApiKeyCall.objects.filter(pk=old.pk).update(created_at=self.now - timedelta(days=8))
        TenantApiKeyCall.objects.create(
            key=key,
            tenant=self.other,
            key_version=1,
            status_code=201,
            duration_ms=10,
            assistant_uuid=uuid.uuid4(),
        )
        detail = self.client.get(f"/api/v1/tenant/api-keys/{key.pk}").data
        self.assertEqual(detail["calls_7d"], 8)
        self.assertEqual(len(detail["recent_calls"]), 6)
        self.assertEqual(self.client.get("/api/v1/tenant/api-keys").data["results"][0]["calls_7d"], 8)
        self.client.force_authenticate(self.platform)
        listing = self.client.get("/api/v1/platform/api-keys").data
        self.assertEqual(listing["results"][0]["calls_7d"], 8)
        self.assertNotIn("recent_calls", str(listing))

    def test_rotation_keeps_historical_calls_and_preserves_key_versions(self):
        """Credential rotation cannot erase recent usage or confuse old and new requests."""
        key, secret = self.issue()
        external = APIClient()
        external.credentials(HTTP_AUTHORIZATION=f"Bearer {secret}")
        url = f"/api/lens/sessions/{uuid.uuid4()}/runs/"
        self.assertEqual(external.post(url, {}, format="json").status_code, 404)
        rotated = self.client.post(f"/api/v1/tenant/api-keys/{key.pk}")
        self.assertEqual(rotated.status_code, 200, rotated.data)
        self.assertEqual(external.post(url, {}, format="json").status_code, 401)
        external.credentials(HTTP_AUTHORIZATION=f"Bearer {rotated.data['key']}")
        self.assertEqual(external.post(url, {}, format="json").status_code, 404)
        self.assertEqual(set(TenantApiKeyCall.objects.values_list("key_version", flat=True)), {1, 2})
        detail = self.client.get(f"/api/v1/tenant/api-keys/{key.pk}")
        self.assertEqual(detail.data["calls_7d"], 2)

    def test_accounting_failure_does_not_change_the_api_response(self):
        """An observability failure cannot turn a valid HTTP error into a second failure."""
        _, secret = self.issue()
        external = APIClient()
        external.credentials(HTTP_AUTHORIZATION=f"Bearer {secret}")
        with patch("accounts.services.api_key_insights.record_invocation", side_effect=OSError("private db error")):
            response = external.post(f"/api/lens/sessions/{uuid.uuid4()}/runs/", {}, format="json")
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("private db error", str(response.data))

    def test_history_filter_returns_only_recorded_runs_for_the_current_tenant_key(self):
        """Key history survives rotation and excludes browser runs and corrupted cross-tenant links."""
        key, _ = self.issue(status="revoked")
        assistant = Assistant.objects.create(name="History", slug="insights-history", tenant=self.tenant)
        session = Session.objects.create(tenant=self.tenant, user=self.admin, assistant=assistant)
        runs = []
        for i in range(4):
            message = Message.objects.create(session=session, role="user", content="Question", sequence=i + 1)
            runs.append(Run.objects.create(tenant=self.tenant, session=session, input_message=message))
        for i in (0, 1):
            TenantApiKeyCall.objects.create(
                key=key,
                tenant=self.tenant,
                run_uuid=runs[i].uuid,
                key_version=i + 1,
                status_code=201,
                duration_ms=1,
            )
        TenantApiKeyCall.objects.create(
            key=key, tenant=self.other, run_uuid=runs[2].uuid, key_version=1, status_code=201, duration_ms=1
        )
        foreign = Assistant.objects.create(name="Foreign", slug="insights-history-foreign", tenant=self.other)
        foreign_session = Session.objects.create(tenant=self.other, user=self.admin, assistant=foreign)
        foreign_message = Message.objects.create(session=foreign_session, role="user", sequence=1)
        polluted = Run.objects.create(tenant=self.tenant, session=foreign_session, input_message=foreign_message)
        TenantApiKeyCall.objects.create(
            key=key, tenant=self.tenant, run_uuid=polluted.uuid, key_version=1, status_code=201, duration_ms=1
        )
        response = self.client.get("/api/lens/admin/runs/", {"api_key": str(key.pk), "page_size": 1})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["total"], 2)
        self.assertEqual(response.data["summary"]["total"], 2)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertIn(response.data["results"][0]["uuid"], [str(run.uuid) for run in runs[:2]])
        self.assertEqual(self.client.get("/api/lens/admin/runs/", {"api_key": "invalid"}).status_code, 400)
        other_key, _ = self.issue(tenant=self.other)
        self.assertEqual(self.client.get("/api/lens/admin/runs/", {"api_key": str(other_key.pk)}).status_code, 404)

    def test_key_history_rejects_platform_and_staff_members(self):
        """An explicit Key source filter does not provide a credential management bypass."""
        key, secret = self.issue()
        params = {"api_key": str(key.pk)}
        self.client.force_authenticate(self.platform)
        self.assertEqual(self.client.get("/api/lens/admin/runs/", params).status_code, 403)
        self.client.force_authenticate(self.admin)
        self.membership.role = "member"
        self.membership.save()
        self.admin.is_staff = True
        self.admin.save()
        self.assertEqual(self.client.get("/api/lens/admin/runs/", params).status_code, 403)
        external = APIClient()
        external.credentials(HTTP_AUTHORIZATION=f"Bearer {secret}")
        self.assertEqual(external.get("/api/lens/admin/runs/", params).status_code, 403)

    def test_database_accounting_failure_rolls_back_only_the_accounting_savepoint(self):
        """An actual failed INSERT cannot poison the surrounding request/test transaction."""
        key, secret = self.issue()
        external = APIClient()
        external.credentials(HTTP_AUTHORIZATION=f"Bearer {secret}")
        create = TenantApiKeyCall.objects.create

        def invalid_insert(**values):
            """Force a database NOT NULL violation within the accounting transaction."""
            values["status_code"] = None
            return create(**values)

        with patch("accounts.services.api_key_insights.TenantApiKeyCall.objects.create", side_effect=invalid_insert):
            response = external.post(f"/api/lens/sessions/{uuid.uuid4()}/runs/", {}, format="json")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(TenantApiKeyCall.objects.filter(key=key).count(), 0)
        self.assertTrue(TenantApiKey.objects.filter(pk=key.pk).exists())
