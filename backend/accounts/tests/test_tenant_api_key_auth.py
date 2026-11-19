"""Runtime authentication tests for tenant API keys."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Tenant, TenantApiKey, TenantMembership
from lens.models import Assistant


class TenantApiKeyAuthenticationTests(TestCase):
    """API keys authenticate independently within their tenant scope."""

    def setUp(self):
        user_model = get_user_model()
        self.tenant = Tenant.objects.create(name="Acme", slug="acme")
        self.other_tenant = Tenant.objects.create(name="Other", slug="other")
        self.owner = user_model.objects.create_user(
            username="api-key-owner",
            email="api-key-owner@example.com",
            password="x",
        )
        TenantMembership.objects.create(
            tenant=self.tenant,
            user=self.owner,
            role=TenantMembership.Role.TENANT_ADMIN,
        )
        self.assistant = Assistant.objects.create(
            name="Allowed assistant",
            slug="allowed-assistant",
            tenant=self.tenant,
        )
        self.other_assistant = Assistant.objects.create(
            name="Other assistant",
            slug="other-assistant",
            tenant=self.tenant,
        )
        self.foreign_assistant = Assistant.objects.create(
            name="Foreign assistant",
            slug="foreign-assistant",
            tenant=self.other_tenant,
        )

    def _issue(self, **overrides):
        values = {
            "tenant": self.tenant,
            "name": "API key",
            "scope": TenantApiKey.Scope.ALL,
            "assistant_uuids": [],
            "created_by": self.owner,
            "expires_at": None,
        }
        values.update(overrides)
        record, raw = TenantApiKey.issue(**values)
        return record, raw

    def _client(self, raw):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
        return client

    def test_valid_key_lists_tenant_assistants_and_updates_last_used(self):
        record, raw = self._issue()
        response = self._client(raw).get(
            "/api/lens/assistants/",
            {"page_size": 100},
        )

        self.assertEqual(response.status_code, 200, response.data)
        slugs = {row["slug"] for row in response.data["results"]}
        self.assertIn("allowed-assistant", slugs)
        self.assertIn("other-assistant", slugs)
        self.assertNotIn("foreign-assistant", slugs)

        record.refresh_from_db()
        self.assertIsNotNone(record.last_used_at)

    def test_invalid_revoked_and_expired_keys_are_rejected(self):
        _, raw = self._issue()
        invalid = self._client("slk_not-a-real-key").get("/api/lens/assistants/")
        self.assertEqual(invalid.status_code, 401)

        record, raw = self._issue()
        record.status = TenantApiKey.Status.REVOKED
        record.save(update_fields=["status"])
        revoked = self._client(raw).get("/api/lens/assistants/")
        self.assertEqual(revoked.status_code, 401)

        record, raw = self._issue(expires_at=timezone.now() - timedelta(minutes=1))
        expired = self._client(raw).get("/api/lens/assistants/")
        self.assertEqual(expired.status_code, 401)

    def test_key_cannot_reach_admin_routes(self):
        _, raw = self._issue()
        response = self._client(raw).get("/api/lens/admin/runs/")

        self.assertEqual(response.status_code, 403)

    def test_selected_scope_limits_assistant_list_and_session_creation(self):
        _, raw = self._issue(
            scope=TenantApiKey.Scope.SELECTED,
            assistant_uuids=[str(self.assistant.uuid)],
        )
        client = self._client(raw)

        listed = client.get("/api/lens/assistants/", {"page_size": 100})
        slugs = {row["slug"] for row in listed.data["results"]}
        self.assertEqual(slugs, {"allowed-assistant"})

        allowed = client.post(
            "/api/lens/sessions/",
            {"assistant_uuid": str(self.assistant.uuid)},
            format="json",
        )
        denied = client.post(
            "/api/lens/sessions/",
            {"assistant_uuid": str(self.other_assistant.uuid)},
            format="json",
        )

        self.assertEqual(allowed.status_code, 201, allowed.data)
        self.assertEqual(denied.status_code, 403)

    def test_key_cannot_use_foreign_assistant(self):
        _, raw = self._issue()
        response = self._client(raw).post(
            "/api/lens/sessions/",
            {"assistant_uuid": str(self.foreign_assistant.uuid)},
            format="json",
        )

        self.assertEqual(response.status_code, 403)

    def test_creator_lifecycle_does_not_revoke_key(self):
        """Creator inactivity, membership changes and deletion are audit-only changes."""
        record, raw = self._issue()
        client = self._client(raw)
        self.owner.is_active = False
        self.owner.save(update_fields=["is_active"])
        self.assertEqual(client.get("/api/lens/assistants/").status_code, 200)
        TenantMembership.objects.filter(user=self.owner).update(
            tenant=self.other_tenant, status="inactive", role="member"
        )
        self.assertEqual(client.get("/api/lens/assistants/").status_code, 200)
        self.owner.delete()
        record.refresh_from_db()
        self.assertIsNone(record.created_by_id)
        self.assertEqual(client.get("/api/lens/assistants/").status_code, 200)

    def test_sessions_belong_to_the_key_instead_of_creator(self):
        """Separate credentials never reuse the creator's or each other's sessions."""
        from lens.models import Session

        personal = Session.objects.create(tenant=self.tenant, assistant=self.assistant, user=self.owner)
        first, raw = self._issue()
        second, other_raw = self._issue()
        sessions = []
        for record, secret in ((first, raw), (second, other_raw)):
            response = self._client(secret).post(
                "/api/lens/sessions/", {"assistant_uuid": str(self.assistant.uuid)}, format="json"
            )
            self.assertEqual(response.status_code, 201, response.data)
            session = Session.objects.get(uuid=response.data["uuid"])
            self.assertIsNone(session.user_id)
            self.assertEqual(session.api_key_id, record.pk)
            self.assertNotEqual(session.pk, personal.pk)
            sessions.append(session)
        self.assertNotEqual(sessions[0].pk, sessions[1].pk)
        for session in (personal, sessions[1]):
            denied = self._client(raw).post(f"/api/lens/sessions/{session.uuid}/runs/", {}, format="json")
            self.assertEqual(denied.status_code, 404, denied.data)
        web = APIClient()
        web.force_authenticate(self.owner)
        self.assertEqual(web.get(f"/api/lens/sessions/{sessions[0].uuid}/").status_code, 404)

    def test_key_rejects_delegation_context(self):
        """A machine credential cannot combine scopes with platform delegation."""
        import uuid

        _, raw = self._issue()
        response = self._client(raw).get("/api/lens/assistants/", HTTP_X_DELEGATION_ID=str(uuid.uuid4()))
        self.assertEqual(response.status_code, 403)

    def _key_session(self, key, assistant=None):
        """Persist a session with an unambiguous machine owner."""
        from lens.models import Session

        return Session.objects.create(tenant=self.tenant, api_key=key, assistant=assistant or self.assistant)

    def test_private_access_uses_key_scope_without_creator_grants(self):
        """Machine scope grants local private assistants and never foreign public ones."""
        self.assistant.visibility = "private"
        self.assistant.save()
        self.foreign_assistant.visibility = "public"
        self.foreign_assistant.save()
        TenantMembership.objects.filter(user=self.owner).update(role="member")
        _, raw = self._issue(scope="selected", assistant_uuids=[str(self.assistant.uuid)])
        response = self._client(raw).get("/api/lens/assistants/", {"page_size": 100})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual({row["uuid"] for row in response.data["results"]}, {str(self.assistant.uuid)})

    def test_owner_constraint_rejects_ambiguous_and_missing_principals(self):
        """Database writes cannot create ownerless or shared human/machine sessions."""
        from django.db import IntegrityError, transaction

        from lens.models import Session

        key, _ = self._issue()
        for owners in ({}, {"user": self.owner, "api_key": key}):
            with self.subTest(owners=owners), self.assertRaises(IntegrityError), transaction.atomic():
                Session.objects.create(tenant=self.tenant, assistant=self.assistant, **owners)

    def test_rotation_keeps_identity_but_rejects_old_secret_and_inactive_tenant(self):
        """Ownership follows the key UUID across credential rotation."""
        key, raw = self._issue()
        session = self._key_session(key)
        rotated = key.rotate()
        self.assertEqual(self._client(raw).get("/api/lens/assistants/").status_code, 401)
        response = self._client(rotated).post(f"/api/lens/sessions/{session.uuid}/runs/", {}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        Tenant.objects.filter(pk=self.tenant.pk).update(status="disabled")
        self.assertEqual(self._client(rotated).get("/api/lens/assistants/").status_code, 401)

    def test_run_polling_and_file_download_are_key_owned(self):
        """Final answer and file bytes are isolated between keys and the creator."""
        from tempfile import TemporaryDirectory

        from django.conf import settings
        from django.core.files.base import ContentFile
        from django.test import override_settings

        from lens.models import Message, Run, RunOutputFile

        key, raw = self._issue()
        _, other_raw = self._issue()
        session = self._key_session(key)
        answer = Message.objects.create(session=session, role="assistant", sequence=1, content="Key answer")
        question = Message.objects.create(session=session, role="user", sequence=2, content="Report")
        run = Run.objects.create(
            tenant=self.tenant, session=session, status="done", input_message=question, output_message=answer
        )
        with TemporaryDirectory() as directory, override_settings(
            STORAGES={
                **settings.STORAGES,
                "deliverables": {
                    "BACKEND": "django.core.files.storage.FileSystemStorage",
                    "OPTIONS": {"location": directory},
                },
            }
        ):
            output = RunOutputFile.objects.create(
                tenant=self.tenant,
                run=run,
                session=session,
                assistant=self.assistant,
                message=answer,
                filename="key.txt",
                content_type="text/plain",
                byte_size=6,
                file=ContentFile(b"secret", name="key.txt"),
            )
            urls = [
                f"/api/lens/runs/{run.uuid}/",
                f"/api/lens/runs/{run.uuid}/output-files/",
                f"/api/lens/output-files/{output.uuid}/",
            ]
            for url in urls:
                response = self._client(raw).get(url)
                self.assertEqual(response.status_code, 200, getattr(response, "data", None))
                if response.streaming:
                    self.assertEqual(b"".join(response.streaming_content), b"secret")
                self.assertIn(self._client(other_raw).get(url).status_code, (403, 404))
            self.owner.delete()
            self.assertEqual(self._client(raw).get(urls[0]).status_code, 200)

    def test_background_collaboration_inherits_key_and_rechecks_selected_scope(self):
        """Private subagents use the machine principal after the creator is gone."""
        from accounts.principals import ApiKeyPrincipal
        from lens.assistant_lifecycle import create_assistant_session
        from lens.models import LensNode
        from lens.services import LensNodeDispatchError, create_delegated_run, create_execution_run

        node = LensNode.objects.create(
            name="Machine node", status="online", enrollment_status="approved", tasks=[{"name": "general_chat"}]
        )
        self.assistant.lensnode = node
        self.assistant.visibility = "private"
        self.assistant.save()
        coordinator = Assistant.objects.create(
            name="Key coordinator",
            slug="key-coordinator",
            tenant=self.tenant,
            routing_mode="smart",
            visibility="private",
        )
        coordinator.collaboration_members.add(self.assistant)
        key, _ = self._issue(scope="selected", assistant_uuids=[str(coordinator.uuid), str(self.assistant.uuid)])
        session = create_assistant_session(coordinator.uuid, ApiKeyPrincipal(key))
        self.owner.delete()
        parent = create_execution_run(session, "Coordinate", enqueue=False)
        self.assertEqual(parent.execution.runtime_snapshot["answer_language"], self.tenant.default_language)
        self.assertEqual(
            {item["uuid"] for item in parent.execution.runtime_snapshot["subagents"]}, {str(self.assistant.uuid)}
        )
        parent.status = "running"
        parent.save(update_fields=["status"])
        child = create_delegated_run(parent, self.assistant.uuid, "Child task", delegation_key="first")
        self.assertEqual(child.session.api_key_id, key.pk)
        self.assertIsNone(child.session.user_id)
        TenantApiKey.objects.filter(pk=key.pk).update(assistant_uuids=[str(coordinator.uuid)])
        with self.assertRaises(LensNodeDispatchError):
            create_delegated_run(parent, self.assistant.uuid, "Denied child", delegation_key="second")

    def test_key_cannot_create_hidden_system_sessions(self):
        """The ordinary create route cannot bypass the unsupported ad-hoc coordinator route."""
        _, raw = self._issue()
        for tenant in (self.tenant, self.other_tenant):
            system = Assistant.objects.create(
                tenant=tenant, name="Hidden", slug=f"hidden-{tenant.slug}", is_system=True
            )
            response = self._client(raw).post(
                "/api/lens/sessions/", {"assistant_uuid": str(system.uuid)}, format="json"
            )
            self.assertEqual(response.status_code, 403, response.data)

    def test_denied_cross_key_probe_does_not_identify_target_in_call_ledger(self):
        """An authenticated credential's attempts never borrow a sibling key's target labels."""
        from accounts.models import TenantApiKeyCall

        first, raw = self._issue()
        second, _ = self._issue()
        session = self._key_session(second)
        response = self._client(raw).post(f"/api/lens/sessions/{session.uuid}/runs/", {}, format="json")
        self.assertEqual(response.status_code, 404, response.data)
        call = TenantApiKeyCall.objects.get(key=first)
        self.assertIsNone(call.assistant_uuid)
        self.assertIsNone(call.run_uuid)

    def test_alert_trace_and_admin_views_identify_machine_without_creator(self):
        """Operational outputs tolerate a missing human and preserve machine provenance."""
        from lens.alerts import _run_payload
        from lens.models import LensNode, Message, Run, RunExecution
        from lens.trace_export import build_ingestion_batch

        key, _ = self._issue()
        session = self._key_session(key)
        question = Message.objects.create(session=session, role="user", sequence=1, content="Question")
        run = Run.objects.create(tenant=self.tenant, session=session, input_message=question, status="failed")
        node = LensNode.objects.create(name="Operational machine node")
        run.lensnode = node
        run.save(update_fields=["lensnode"])
        RunExecution.objects.create(run=run, lensnode=node, task="general_chat")
        payload = _run_payload(run)
        self.assertIsNone(payload["user_id"])
        self.assertEqual(payload["user_display_name"], key.name)
        batch = build_ingestion_batch(run, "0123456789abcdef")
        self.assertIsNone(batch[0]["body"]["userId"])
        self.assertEqual(batch[0]["body"]["metadata"]["apiKeyUuid"], str(key.pk))
        web = APIClient()
        web.force_authenticate(self.owner)
        response = web.get(f"/api/lens/admin/runs/{run.uuid}/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIsNone(response.data["username"])
        self.assertEqual(response.data["api_key"], {"uuid": str(key.pk), "name": key.name})
        listing = web.get("/api/lens/admin/runs/", {"api_key": str(key.pk)}).data
        self.assertEqual([row["uuid"] for row in listing["results"]], [str(run.uuid)])

    def test_in_flight_issuance_rechecks_creator_before_creating_independent_key(self):
        """A request authenticated before disable cannot issue a surviving new credential afterwards."""
        from unittest.mock import patch

        web = APIClient()
        web.force_authenticate(self.owner)

        def expire_authority(*args):
            get_user_model().objects.filter(pk=self.owner.pk).update(is_active=False)
            return None, None

        with patch("accounts.views.tenant_api_keys._parse_expiry", side_effect=expire_authority):
            response = web.post("/api/v1/tenant/api-keys", {"name": "Too late"}, format="json")
        self.assertEqual(response.status_code, 403, response.data)
        self.assertFalse(TenantApiKey.objects.exists())

    def test_head_and_options_stay_inside_machine_route_allowlist(self):
        """Metadata verbs cannot reach human endpoints with a machine principal."""
        _, raw = self._issue()
        client = self._client(raw)
        for method in (client.head, client.options):
            self.assertEqual(method("/api/lens/assistants/").status_code, 200)
            for url in ("/api/v1/auth/user", "/api/lens/admin/runs/"):
                self.assertEqual(method(url).status_code, 403)
