"""API key lifecycle, tenant boundaries, and bulk mutation contracts."""

import threading
from datetime import datetime, timedelta
from datetime import timezone as datetime_timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import AuditLog, Profile, Tenant, TenantApiKey, TenantMembership


class ApiKeyLifecycleTests(TestCase):
    """Keep revoked credentials terminal and mutations scoped and auditable."""

    def setUp(self):
        """Build independent tenant and platform identities."""
        self.tenant = Tenant.objects.create(name="Lifecycle", slug="lifecycle")
        self.other = Tenant.objects.create(name="Foreign", slug="foreign-lifecycle")
        self.admin = get_user_model().objects.create_user(username="key-lifecycle-admin")
        self.membership = TenantMembership.objects.create(tenant=self.tenant, user=self.admin, role="tenant_admin")
        self.platform = get_user_model().objects.create_user(username="key-lifecycle-platform", is_staff=True)
        Profile.objects.update_or_create(user=self.platform, defaults={"platform_role": "super_admin"})
        self.client = APIClient()
        self.client.force_authenticate(self.admin)
        self.key, self.secret = self.issue()

    def issue(self, **overrides):
        """Issue one key with a finite future expiry by default."""
        values = dict(
            tenant=self.tenant,
            name="Service key",
            scope="all",
            assistant_uuids=[],
            created_by=self.admin,
            expires_at=timezone.now() + timedelta(days=10),
        )
        values.update(overrides)
        return TenantApiKey.issue(**values)

    def bulk(self, action, keys=None, **extra):
        """Submit the selection snapshot used by the management page."""
        return self.client.post(
            "/api/v1/tenant/api-keys/bulk",
            {
                "action": action,
                "keys": [{"uuid": str(k.uuid), "updated_at": k.updated_at.isoformat()} for k in (keys or [self.key])],
                **extra,
            },
            format="json",
        )

    def test_revocation_cannot_be_undone_by_patch_or_rotation(self):
        """Neither tenant entry point can restore a revoked credential."""
        url = f"/api/v1/tenant/api-keys/{self.key.uuid}"
        self.assertEqual(self.client.delete(url).status_code, 204)
        for payload in ({"status": "active"}, {"name": "Reused"}, {"expires_in_days": 90}):
            self.assertEqual(self.client.patch(url, payload, format="json").status_code, 409)
        self.assertEqual(self.client.post(url).status_code, 409)
        self.key.refresh_from_db()
        self.assertEqual(self.key.status, "revoked")
        self.assertEqual(self.key.key_hash, TenantApiKey.digest(self.secret))
        self.assertEqual(self.key.key_version, 1)
        self.assertEqual(self.client.delete(url).status_code, 204)
        self.assertEqual(AuditLog.objects.filter(action="api_key.revoke").count(), 1)

    def test_single_update_is_audited_without_secrets(self):
        """Status and expiry edits leave an audit record for the actual actor."""
        response = self.client.patch(
            f"/api/v1/tenant/api-keys/{self.key.uuid}", {"status": "disabled", "expires_in_days": 30}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.data)
        audit = AuditLog.objects.get(action="api_key.update", resource_id=str(self.key.uuid))
        self.assertEqual(audit.actor_user, self.admin)
        self.assertEqual(audit.tenant, self.tenant)
        self.assertNotIn(self.secret, str(audit.metadata))
        self.assertNotIn(self.key.key_hash, str(audit.metadata))

    def test_rotation_preserves_disabled_state_and_requires_expired_key_renewal(self):
        """Rotating is not an enable operation; expired replacements need an explicit new expiry."""
        url = f"/api/v1/tenant/api-keys/{self.key.uuid}"
        self.key.status = "disabled"
        self.key.save()
        disabled = self.client.post(url)
        self.assertEqual(disabled.status_code, 200, disabled.data)
        self.assertEqual(disabled.data["status"], "disabled")
        self.key.refresh_from_db()
        self.key.status = "active"
        self.key.expires_at = timezone.now() - timedelta(days=1)
        self.key.save()
        digest = self.key.key_hash
        self.assertEqual(self.client.post(url).status_code, 400)
        self.key.refresh_from_db()
        self.assertEqual(self.key.key_hash, digest)
        renewed = self.client.post(url, {"expires_in_days": 90}, format="json")
        self.assertEqual(renewed.status_code, 200, renewed.data)
        self.assertEqual(renewed.data["status"], "active")
        self.key.refresh_from_db()
        self.assertGreater(self.key.expires_at, timezone.now() + timedelta(days=89))
        external = APIClient()
        external.credentials(HTTP_AUTHORIZATION=f"Bearer {renewed.data['key']}")
        self.assertEqual(external.get("/api/lens/assistants/").status_code, 200)

    def test_invalid_expiry_inputs_are_rejected_without_server_errors(self):
        """All lifecycle entry points validate expiry values before persisting or rotating."""
        for payload in [
            {"expires_in_days": 10**20},
            {"expires_in_days": True},
            {"expires_in_days": 30.5},
            {"expires_in_days": -1},
            {"expires_at": "2026-99-99T00:00:00Z"},
        ]:
            for method, url in [
                (self.client.post, "/api/v1/tenant/api-keys"),
                (self.client.patch, f"/api/v1/tenant/api-keys/{self.key.uuid}"),
                (self.client.post, f"/api/v1/tenant/api-keys/{self.key.uuid}"),
            ]:
                with self.subTest(payload=payload, url=url):
                    response = method(url, {"name": "Invalid expiry", **payload}, format="json")
                    self.assertEqual(response.status_code, 400, response.data)
        self.key.refresh_from_db()
        self.assertEqual(self.key.key_hash, TenantApiKey.digest(self.secret))
        self.assertEqual(self.key.name, "Service key")

    def test_bulk_extension_uses_each_expiry_and_preserves_credentials(self):
        """Extend finite unexpired active and disabled keys; explain all skips."""
        disabled, _ = self.issue()
        disabled.status = "disabled"
        disabled.save()
        permanent, _ = self.issue(expires_at=None)
        expired, _ = self.issue(expires_at=timezone.now() - timedelta(days=1))
        revoked, _ = self.issue()
        revoked.status = "revoked"
        revoked.save()
        rows = [self.key, disabled, permanent, expired, revoked]
        before = {k.pk: (k.expires_at, k.key_hash, k.key_version) for k in rows}
        response = self.bulk("extend", rows, days=90)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual((response.data["changed"], response.data["skipped"]), (2, 3))
        self.assertEqual({i["reason"] for i in response.data["skipped_items"]}, {"never_expires", "expired", "revoked"})
        for k in rows:
            k.refresh_from_db()
            expiry, digest, version = before[k.pk]
            self.assertEqual(k.expires_at, expiry + timedelta(days=90) if k in [self.key, disabled] else expiry)
            self.assertEqual((k.key_hash, k.key_version), (digest, version))
        self.assertEqual(AuditLog.objects.filter(action="api_key.bulk_extend", tenant=self.tenant).count(), 2)

    def test_bulk_enable_disable_and_revoke_skip_inapplicable_keys(self):
        """Re-enabling never revives expired or revoked keys."""
        expired, _ = self.issue(expires_at=timezone.now() - timedelta(days=1))
        response = self.bulk("disable", [self.key, expired])
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual((response.data["changed"], response.data["skipped"]), (1, 1))
        self.key.refresh_from_db()
        response = self.bulk("enable", [self.key, expired])
        self.assertEqual(response.data["changed"], 1)
        self.key.refresh_from_db()
        response = self.bulk("revoke", [self.key, expired])
        self.assertEqual(response.data["changed"], 2)
        self.key.refresh_from_db()
        self.assertEqual(self.bulk("enable").data["skipped_items"][0]["reason"], "revoked")

    def test_foreign_missing_or_stale_selection_changes_nothing(self):
        """The whole batch is rejected before changing valid local keys."""
        foreign, _ = self.issue(tenant=self.other)
        self.assertEqual(self.bulk("disable", [self.key, foreign]).status_code, 404)
        TenantApiKey.objects.filter(pk=self.key.pk).update(updated_at=timezone.now() + timedelta(seconds=1))
        self.assertEqual(self.bulk("revoke").status_code, 409)
        self.key.refresh_from_db()
        self.assertEqual(self.key.status, "active")
        self.assertFalse(AuditLog.objects.filter(action__startswith="api_key.bulk_").exists())

    def test_bulk_rejects_invalid_or_unbounded_input(self):
        """Bulk rotation, malformed snapshots and arbitrary extension values are rejected."""
        self.assertEqual(self.bulk("rotate").status_code, 400)
        for days in [0, -1, 1, True, 30.5, "90"]:
            self.assertEqual(self.bulk("extend", days=days).status_code, 400)
        item = {"uuid": str(self.key.uuid), "updated_at": self.key.updated_at.isoformat()}
        for rows in [[], [item, item], [item] * 101, [{"uuid": "bad"}], [{"uuid": str(self.key.uuid)}]]:
            response = self.client.post(
                "/api/v1/tenant/api-keys/bulk", {"action": "disable", "keys": rows}, format="json"
            )
            self.assertEqual(response.status_code, 400, response.data)

    def test_expiry_overflow_rejects_the_entire_batch(self):
        """An otherwise valid date near the storage limit cannot cause a partial extension."""
        far_future, _ = self.issue(expires_at=datetime(9999, 12, 15, tzinfo=datetime_timezone.utc))
        original = self.key.expires_at
        response = self.bulk("extend", [self.key, far_future], days=365)
        self.assertEqual(response.status_code, 400, response.data)
        self.key.refresh_from_db()
        self.assertEqual(self.key.expires_at, original)
        self.assertFalse(AuditLog.objects.filter(action="api_key.bulk_extend").exists())

    def test_staff_member_and_inactive_tenant_cannot_manage_keys(self):
        """Only a live explicit tenant administrator may use any tenant key endpoint."""
        self.admin.is_staff = True
        self.admin.save()
        for field, value in [("role", "member"), ("status", "suspended")]:
            setattr(self.membership, field, value)
            self.membership.save()
            self.assertEqual(self.client.get("/api/v1/tenant/api-keys").status_code, 403)
            self.assertEqual(self.bulk("revoke").status_code, 403)
            self.membership.role, self.membership.status = "tenant_admin", "active"
        self.membership.save()
        self.tenant.status = "suspended"
        self.tenant.save()
        self.assertEqual(self.client.post("/api/v1/tenant/api-keys", {"name": "Denied"}).status_code, 403)

    def test_search_creator_and_effective_status_filter_agree(self):
        """Search creator names and show expired disabled keys under expired."""
        self.key.status = "disabled"
        self.key.expires_at = timezone.now() - timedelta(seconds=1)
        self.key.save()
        found = self.client.get("/api/v1/tenant/api-keys", {"search": self.admin.username, "status": "expired"})
        self.assertEqual(len(found.data["results"]), 1)
        self.assertIn("updated_at", found.data["results"][0])
        self.assertEqual(self.client.get("/api/v1/tenant/api-keys", {"status": "disabled"}).data["results"], [])
        self.client.force_authenticate(self.platform)
        platform = self.client.get("/api/v1/platform/api-keys", {"status": "expired"})
        self.assertEqual([row["uuid"] for row in platform.data["results"]], [str(self.key.uuid)])
        self.assertEqual(self.client.get("/api/v1/platform/api-keys", {"status": "disabled"}).data["results"], [])

    def test_platform_cannot_enable_and_revocation_remains_terminal(self):
        """Platform control is restricted to disabling and revoking."""
        self.client.force_authenticate(self.platform)
        base = f"/api/v1/platform/api-keys/{self.key.uuid}"
        self.assertEqual(self.client.post(base + "/enable").status_code, 400)
        self.assertEqual(self.client.post(base + "/revoke").status_code, 200)
        self.assertEqual(self.client.post(base + "/disable").status_code, 409)

    def test_delegation_and_api_key_identity_cannot_manage_tenant_keys(self):
        """Header-based delegation and a key owner identity do not inherit key management."""
        for token, headers in [
            ({"principal_type": "tenant_api_key"}, {}),
            (None, {"HTTP_X_DELEGATION_ID": "support-context"}),
        ]:
            self.client.force_authenticate(self.admin, token=token)
            self.assertEqual(self.client.get("/api/v1/tenant/api-keys", **headers).status_code, 403)
            self.assertEqual(self.client.delete(f"/api/v1/tenant/api-keys/{self.key.uuid}", **headers).status_code, 403)

    def test_bulk_lifecycle_changes_real_bearer_authentication(self):
        """Disabled secrets fail immediately and enabling never revives a revoked secret."""
        external = APIClient()
        external.credentials(HTTP_AUTHORIZATION=f"Bearer {self.secret}")
        url = "/api/lens/assistants/"
        self.assertEqual(external.get(url).status_code, 200)
        self.assertEqual(self.bulk("disable").data["changed"], 1)
        self.assertEqual(external.get(url).status_code, 401)
        self.key.refresh_from_db()
        self.assertEqual(self.bulk("enable").data["changed"], 1)
        self.assertEqual(external.get(url).status_code, 200)
        self.key.refresh_from_db()
        self.assertEqual(self.bulk("revoke").data["changed"], 1)
        self.assertEqual(external.get(url).status_code, 401)
        self.key.refresh_from_db()
        self.assertEqual(self.bulk("enable").data["changed"], 0)
        self.assertEqual(external.get(url).status_code, 401)

    def test_pagination_is_bounded_and_preserves_scope(self):
        """Page counts and results include only the current tenant."""
        for i in range(22):
            self.issue(name=f"Paged {i}")
        self.issue(tenant=self.other)
        first = self.client.get("/api/v1/tenant/api-keys", {"page": 1, "page_size": 20})
        second = self.client.get("/api/v1/tenant/api-keys", {"page": 2, "page_size": 20})
        self.assertEqual(first.data["count"], 23)
        self.assertEqual(len(first.data["results"]), 20)
        self.assertEqual(len(second.data["results"]), 3)
        self.assertFalse({r["uuid"] for r in first.data["results"]} & {r["uuid"] for r in second.data["results"]})
        invalid = self.client.get("/api/v1/tenant/api-keys", {"page": "bad", "page_size": "bad"})
        self.assertEqual((invalid.data["page"], invalid.data["page_size"]), (1, 20))
        bounded = self.client.get("/api/v1/tenant/api-keys", {"page_size": 10000})
        self.assertEqual(bounded.data["page_size"], 100)

    def test_list_assistant_counts_do_not_query_each_key(self):
        """Mixed scopes share assistant queries across the bounded page."""
        from lens.models import Assistant

        assistant = Assistant.objects.create(tenant=self.tenant, name="Counted", slug="key-counted")
        for _ in range(8):
            self.issue(scope="selected", assistant_uuids=[str(assistant.uuid)])
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get("/api/v1/tenant/api-keys")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["assistant_count"] for row in response.data["results"]], [1] * 9)
        assistant_queries = [item["sql"] for item in queries if 'FROM "lens_assistant"' in item["sql"]]
        self.assertLessEqual(len(assistant_queries), 2)


class ApiKeyRevocationConcurrencyTests(TransactionTestCase):
    """Serialize tenant and platform mutations against an irreversible revocation."""

    def test_competing_updates_wait_for_revocation_and_cannot_restore_it(self):
        """Every credential writer reads the committed revoked state under its own row lock."""
        if connection.vendor != "postgresql":
            self.skipTest("Requires PostgreSQL row locks")
        tenant = Tenant.objects.create(name="Race", slug="key-race")
        admin = get_user_model().objects.create_user(username="key-race-admin")
        TenantMembership.objects.create(tenant=tenant, user=admin, role="tenant_admin")
        platform = get_user_model().objects.create_user(username="key-race-platform")
        Profile.objects.update_or_create(user=platform, defaults={"platform_role": "super_admin"})
        for operation in ("patch", "rotate", "bulk", "platform"):
            with self.subTest(operation=operation):
                record, _ = TenantApiKey.issue(
                    tenant=tenant, name="Race", scope="all", assistant_uuids=[], created_by=admin, expires_at=None
                )
                entered, release, started, finished = [threading.Event() for _ in range(4)]
                responses, errors = {}, []
                original_save = TenantApiKey.save

                def paused_save(instance, *args, **kwargs):
                    """Pause the revoker after it has taken the authoritative row lock."""
                    if threading.current_thread().name == "revoker":
                        entered.set()
                        if not release.wait(5):
                            raise TimeoutError("API key race was not released")
                    return original_save(instance, *args, **kwargs)

                def request(revoker):
                    """Use independent authenticated requests and database connections."""
                    close_old_connections()
                    try:
                        client = APIClient()
                        actor = platform if not revoker and operation == "platform" else admin
                        client.force_authenticate(get_user_model().objects.get(pk=actor.pk))
                        url = f"/api/v1/tenant/api-keys/{record.pk}"
                        if revoker:
                            responses["revoke"] = client.delete(url)
                        else:
                            started.set()
                            if operation == "patch":
                                response = client.patch(url, {"status": "active"}, format="json")
                            elif operation == "rotate":
                                response = client.post(url)
                            elif operation == "platform":
                                response = client.post(f"/api/v1/platform/api-keys/{record.pk}/disable")
                            else:
                                response = client.post(
                                    "/api/v1/tenant/api-keys/bulk",
                                    {
                                        "action": "enable",
                                        "keys": [{"uuid": str(record.pk), "updated_at": record.updated_at.isoformat()}],
                                    },
                                    format="json",
                                )
                            responses["competing"] = response
                    except Exception as exc:
                        errors.append(exc)
                    finally:
                        if not revoker:
                            finished.set()
                        close_old_connections()

                with patch.object(TenantApiKey, "save", paused_save):
                    first = threading.Thread(target=request, args=(True,), name="revoker")
                    second = threading.Thread(target=request, args=(False,))
                    first.start()
                    try:
                        self.assertTrue(entered.wait(5))
                        second.start()
                        self.assertTrue(started.wait(5))
                        overtook = finished.wait(0.25)
                    finally:
                        release.set()
                        first.join(5)
                        if second.ident is not None:
                            second.join(5)
                self.assertFalse(first.is_alive())
                self.assertFalse(second.is_alive())
                if errors:
                    raise errors[0]
                self.assertFalse(overtook)
                self.assertEqual(responses["revoke"].status_code, 204)
                self.assertEqual(responses["competing"].status_code, 409)
                record.refresh_from_db()
                self.assertEqual(record.status, "revoked")
