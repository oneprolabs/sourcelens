"""Verify live management checks and atomic tenant-scoped visibility batches."""

from datetime import timedelta
from uuid import uuid4

from agentcore_metering.adapters.django.models import LLMConfig
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import AuditLog, Profile, Tenant, TenantDelegation, TenantGroup, TenantMembership
from lens.assistant_management import BulkVisibilitySerializer
from lens.models import Assistant, AssistantAccess, LensNode, LLMConfigScope, Message, Run, Session, SharedQA


class AssistantManagementTests(TestCase):
    """Management metadata and writes must stay inside the owning tenant."""

    def setUp(self):
        """Create two assistants and explicit tenant-owned grant subjects."""
        self.tenant = Tenant.objects.create(name="A", slug="manage-a")
        self.foreign = Tenant.objects.create(name="B", slug="manage-b")
        self.admin = self.member("manager", self.tenant, "tenant_admin")
        self.peer = self.member("peer", self.tenant)
        self.other = self.member("other", self.foreign, "tenant_admin")
        self.client = APIClient()
        self.client.force_authenticate(self.admin)
        self.model = LLMConfig.objects.create(
            scope=LLMConfig.Scope.GLOBAL, provider="openai", config={"model": "chat", "api_key": "secret"}
        )
        LLMConfigScope.objects.create(config=self.model, tenant=self.tenant)
        self.node = LensNode.objects.create(name="node", status="online", enrollment_status="approved")
        self.rows = [
            Assistant.objects.create(
                name=f"A{i}",
                slug=f"manage-{i}",
                tenant=self.tenant,
                agent_model_ref=self.model.uuid,
                lensnode=self.node,
            )
            for i in range(2)
        ]
        self.group = Group.objects.create(name="management-group")
        TenantGroup.objects.create(tenant=self.tenant, auth_group=self.group, name="Team")

    def member(self, name, tenant, role="member"):
        """Create an explicit tenant member."""
        user = get_user_model().objects.create_user(username=f"manage-{name}")
        TenantMembership.objects.create(user=user, tenant=tenant, role=role)
        return user

    def payload(self, **changes):
        """Use the exact configuration versions read by the administrator."""
        return {
            "assistants": [{"uuid": str(row.uuid), "updated_at": row.updated_at.isoformat()} for row in self.rows],
            "visibility": "tenant",
            **changes,
        }

    def post(self, **changes):
        """Apply one visibility batch."""
        return self.client.post("/api/lens/assistants/bulk-visibility/", self.payload(**changes), format="json")

    def test_managed_checks_are_live_and_do_not_expose_provider_configuration(self):
        """Read current model and node state without exposing provider credentials."""
        url = "/api/lens/assistants/"
        response = self.client.get(url, {"managed": "true"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["results"][0]["management_checks"]["issues"], [])
        LLMConfig.objects.filter(pk=self.model.pk).update(is_active=False)
        LensNode.objects.filter(pk=self.node.pk).update(token_revoked=True)
        response = self.client.get(url, {"managed": "true"})
        issues = response.data["results"][0]["management_checks"]["issues"]
        self.assertEqual({item["code"] for item in issues}, {"model_unavailable", "node_unavailable"})
        self.assertNotIn("secret", str(response.data))
        self.assertNotIn("api_key", str(response.data))
        self.assertIsNone(self.client.get(url).data["results"][0]["management_checks"])

    def test_optional_models_and_automatic_node_do_not_report_missing_configuration(self):
        """Skip optional slots and avoid inferring automatic node readiness."""
        Assistant.objects.filter(pk=self.rows[0].pk).update(lensnode=None, agent_model_ref=None)
        rows = self.client.get("/api/lens/assistants/", {"managed": "true"}).data["results"]
        row = next(item for item in rows if item["uuid"] == str(self.rows[0].uuid))
        self.assertEqual(row["management_checks"]["issues"], [{"code": "model_missing", "field": "agent_model_ref"}])

    def test_managed_question_counts_exclude_retries_children_evaluation_and_foreign_history(self):
        """Show real top-level questions without disclosing foreign or duplicate activity."""
        session = Session.objects.create(tenant=self.tenant, user=self.admin, assistant=self.rows[0])
        message = Message.objects.create(session=session, role="user", sequence=1, content="question")
        root = Run.objects.create(tenant=self.tenant, session=session, input_message=message)
        Run.objects.create(tenant=self.tenant, session=session, input_message=message, retry_of_run=root)
        Run.objects.create(tenant=self.tenant, session=session, input_message=message, parent_run=root)
        Run.objects.create(tenant=self.tenant, session=session, input_message=message, source="evaluation")
        Run.objects.create(tenant=self.foreign, session=session, input_message=message)
        old = Run.objects.create(tenant=self.tenant, session=session, input_message=message)
        future = Run.objects.create(tenant=self.tenant, session=session, input_message=message)
        Run.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=31))
        Run.objects.filter(pk=future.pk).update(created_at=timezone.now() + timedelta(days=1))
        foreign = Session.objects.create(tenant=self.foreign, user=self.other, assistant=self.rows[0])
        foreign_message = Message.objects.create(session=foreign, role="user", sequence=1, content="foreign")
        Run.objects.create(tenant=self.tenant, session=foreign, input_message=foreign_message)
        evaluation = Session.objects.create(
            tenant=self.tenant, user=self.admin, assistant=self.rows[0], source="evaluation"
        )
        evaluation_message = Message.objects.create(session=evaluation, role="user", sequence=1, content="evaluation")
        Run.objects.create(tenant=self.tenant, session=evaluation, input_message=evaluation_message)
        response = self.client.get("/api/lens/assistants/", {"managed": "true"})
        self.assertEqual(response.status_code, 200)
        counts = {row["uuid"]: row["management_checks"]["questions_30d"] for row in response.data["results"]}
        self.assertEqual(counts[str(self.rows[0].uuid)], 1)
        self.assertEqual(counts[str(self.rows[1].uuid)], 0)
        self.client.force_authenticate(self.peer)
        Assistant.objects.filter(pk=self.rows[0].pk).update(visibility="tenant")
        usage = self.client.get("/api/lens/assistants/").data["results"]
        self.assertNotIn("management_checks", usage[0])

    def test_model_ownership_and_slot_type_are_checked(self):
        """Treat foreign ownership and incompatible model types as unavailable."""
        LLMConfigScope.objects.filter(config=self.model).update(tenant=self.foreign)
        rows = self.client.get("/api/lens/assistants/", {"managed": "true"}).data["results"]
        self.assertEqual(rows[0]["management_checks"]["issues"][0]["code"], "model_unavailable")
        LLMConfigScope.objects.filter(config=self.model).update(tenant=self.tenant)
        LLMConfig.objects.filter(pk=self.model.pk).update(model_type="embedding")
        rows = self.client.get("/api/lens/assistants/", {"managed": "true"}).data["results"]
        self.assertEqual(rows[0]["management_checks"]["issues"][0]["code"], "model_unavailable")

    def test_batch_changes_only_visibility_and_preserves_share_policy_and_grants(self):
        """Preserve all unrelated configuration and audit the bounded change."""
        AssistantAccess.objects.create(assistant=self.rows[0], user=self.peer)
        share = SharedQA.objects.create(assistant=self.rows[0], token="management-share", question="q", answer="a")
        response = self.post()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual((response.data["updated"], response.data["skipped"]), (2, 0))
        for row in self.rows:
            row.refresh_from_db()
            self.assertEqual(row.visibility, "tenant")
            self.assertEqual(row.agent_model_ref, self.model.uuid)
        self.assertTrue(self.rows[0].access_grants.filter(user=self.peer).exists())
        share.refresh_from_db()
        self.assertIsNone(share.require_login)
        self.assertEqual(AuditLog.objects.get(action="assistant.bulk_visibility").tenant, self.tenant)

    def test_private_batch_replaces_grants_even_when_visibility_was_already_private(self):
        """Compare grants as well as visibility when deciding which rows to skip."""
        response = self.post(
            visibility="private",
            access_grants=[{"type": "user", "id": self.peer.pk}, {"type": "group", "id": self.group.pk}],
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["updated"], 2)
        for row in self.rows:
            self.assertEqual(row.access_grants.count(), 2)
        for row in self.rows:
            row.refresh_from_db()
        response = self.post(
            visibility="private",
            access_grants=[{"type": "user", "id": self.peer.pk}, {"type": "group", "id": self.group.pk}],
        )
        self.assertEqual(response.data["skipped"], 2)

    def test_invalid_subjects_never_partially_clear_existing_grants(self):
        """Reject invalid subjects before deleting any existing grants."""
        AssistantAccess.objects.create(assistant=self.rows[0], user=self.peer)
        for grants in (
            [],
            [{"type": "user", "id": self.other.pk}],
            [{"type": "user", "id": True}],
            [{"type": "group", "id": -1}],
        ):
            with self.subTest(grants=grants):
                self.assertEqual(self.post(visibility="private", access_grants=grants).status_code, 400)
                self.assertTrue(self.rows[0].access_grants.filter(user=self.peer).exists())
        self.assertEqual(self.post(access_grants=[{"type": "user", "id": self.peer.pk}]).status_code, 400)

    def test_foreign_system_and_stale_rows_reject_the_entire_batch(self):
        """Reject stale or inaccessible batches before applying any row."""
        for change, expected in (
            ({"tenant": self.foreign}, 400),
            ({"is_system": True}, 400),
            ({"updated_at": timezone.now()}, 409),
        ):
            Assistant.objects.filter(pk=self.rows[1].pk).update(**change)
            response = self.post()
            self.assertEqual(response.status_code, expected, response.data)
            self.rows[0].refresh_from_db()
            self.assertEqual(self.rows[0].visibility, "private")
            Assistant.objects.filter(pk=self.rows[1].pk).update(
                tenant=self.tenant, is_system=False, updated_at=self.rows[1].updated_at
            )

    def test_archived_and_unchanged_rows_are_skipped(self):
        """Return accurate skip counts without changing archived assistants."""
        Assistant.objects.filter(pk=self.rows[0].pk).update(status="archived")
        Assistant.objects.filter(pk=self.rows[1].pk).update(visibility="tenant")
        response = self.post()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual((response.data["updated"], response.data["skipped"]), (0, 2))

    def test_duplicate_empty_and_oversized_batches_are_rejected(self):
        """Reject malformed or unbounded assistant batches at the API boundary."""
        items = self.payload()["assistants"]
        for batch in ([], items * 2, items * 501):
            self.assertEqual(self.post(assistants=batch).status_code, 400)

    def test_only_active_tenant_admin_or_operational_delegation_can_write(self):
        """Keep ordinary members, staff and readonly operators out of batch writes."""
        staff = get_user_model().objects.create_user(username="manage-staff", is_staff=True)
        platform = get_user_model().objects.create_user(username="manage-platform")
        Profile.objects.filter(user=platform).update(platform_role="super_admin")
        for user in (self.peer, staff, platform):
            self.client.force_authenticate(user)
            self.assertEqual(self.post().status_code, 403)
        self.client.force_authenticate(platform)
        delegation = TenantDelegation.objects.create(
            platform_user=platform,
            tenant=self.tenant,
            mode="readonly",
            expires_at=timezone.now() + timezone.timedelta(hours=1),
        )
        headers = {"HTTP_X_DELEGATION_ID": str(delegation.uuid)}
        self.assertEqual(
            self.client.post(
                "/api/lens/assistants/bulk-visibility/", self.payload(), format="json", **headers
            ).status_code,
            403,
        )
        delegation.mode = "operational"
        delegation.save(update_fields=["mode"])
        response = self.client.post("/api/lens/assistants/bulk-visibility/", self.payload(), format="json", **headers)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(AuditLog.objects.get(action="assistant.bulk_visibility").delegation, delegation)

    def test_subject_selector_is_tenant_scoped_and_hides_inactive_members(self):
        """Selectors never expose foreign identities or unavailable grant targets."""
        TenantMembership.objects.filter(user=self.peer).update(status="disabled")
        response = self.client.get("/api/lens/assistants/access-subjects/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["id"] for row in response.data["users"]["results"]], [self.admin.pk])
        self.assertEqual(response.data["groups"]["results"], [{"id": self.group.pk, "name": "Team"}])
        self.client.force_authenticate(self.peer)
        self.assertEqual(self.client.get("/api/lens/assistants/access-subjects/").status_code, 403)

    def test_management_all_includes_archived_without_affecting_public_collection(self):
        """The management tabs receive accurate counts from one scoped collection."""
        Assistant.objects.filter(pk=self.rows[0].pk).update(status="archived")
        response = self.client.get("/api/lens/assistants/", {"managed": "true", "archived": "all"})
        self.assertEqual(response.data["count"], 2)
        self.assertEqual(self.client.get("/api/lens/assistants/").data["count"], 1)
        self.client.force_authenticate(self.peer)
        self.assertEqual(
            self.client.get("/api/lens/assistants/", {"managed": "true", "archived": "all"}).status_code, 403
        )

    def test_api_keys_cannot_use_management_metadata_or_bulk_actions(self):
        """A tenant Q&A key never inherits its creator's console authority."""
        self.client.force_authenticate(self.admin, token={"principal_type": "tenant_api_key", "scope": "all"})
        self.assertEqual(self.client.get("/api/lens/assistants/", {"managed": "true"}).status_code, 403)
        self.assertEqual(self.client.get("/api/lens/assistants/access-subjects/").status_code, 403)
        self.assertEqual(self.post().status_code, 403)

    def test_live_model_checks_are_batched_for_a_full_page(self):
        """Adding assistants must not add a model query per row or model slot."""
        Assistant.objects.filter(pk__in=[row.pk for row in self.rows]).update(multimodal_model_ref=self.model.uuid)
        with CaptureQueriesContext(connection) as baseline:
            self.client.get("/api/lens/assistants/", {"managed": "true", "page_size": 100})
        baseline_reads = sum(LLMConfig._meta.db_table in row["sql"] for row in baseline)
        usage_query = f'FROM "{Run._meta.db_table}"'
        baseline_usage_reads = sum(usage_query in row["sql"] for row in baseline)
        self.assertEqual(baseline_usage_reads, 1)

        for index in range(12):
            Assistant.objects.create(
                name=f"Large {index}",
                slug=f"manage-large-{index}",
                tenant=self.tenant,
                agent_model_ref=self.model.uuid,
                multimodal_model_ref=self.model.uuid,
            )
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get("/api/lens/assistants/", {"managed": "true", "page_size": 100})
        self.assertEqual(response.status_code, 200)
        model_reads = [row for row in queries if LLMConfig._meta.db_table in row["sql"]]
        self.assertEqual(len(model_reads), baseline_reads)
        self.assertEqual(sum(usage_query in row["sql"] for row in queries), baseline_usage_reads)

    def test_bulk_restriction_immediately_limits_existing_public_share(self):
        """Changing assistant visibility cannot leave an old public link open."""
        Assistant.objects.filter(pk=self.rows[0].pk).update(visibility="public")
        share = SharedQA.objects.create(
            assistant=self.rows[0], token="manage-public", question="q", answer="a", require_login=False
        )
        self.assertEqual(self.post().status_code, 200)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(f"/api/lens/public/qa/{share.token}/").status_code, 403)

    def test_private_batch_bounds_total_grant_writes(self):
        """Bound the product of assistants and subjects before database work."""
        serializer = BulkVisibilitySerializer(
            data={
                "assistants": [{"uuid": str(uuid4()), "updated_at": timezone.now().isoformat()} for _ in range(1000)],
                "visibility": "private",
                "access_grants": [{"type": "user", "id": index + 1} for index in range(11)],
            }
        )
        self.assertFalse(serializer.is_valid())

    def test_readonly_delegation_can_preview_but_cannot_replace_models(self):
        """The risk banner's replacement flow enforces readonly mode on the API."""
        platform = get_user_model().objects.create_user(username="manage-model-platform")
        Profile.objects.filter(user=platform).update(platform_role="super_admin")
        delegation = TenantDelegation.objects.create(
            platform_user=platform,
            tenant=self.tenant,
            mode="readonly",
            expires_at=timezone.now() + timezone.timedelta(hours=1),
        )
        self.client.force_authenticate(platform)
        payload = {
            "model_field": "agent_model_ref",
            "target_model_ref": str(self.model.uuid),
            "assistant_models": [{"uuid": str(row.uuid), "model_ref": str(self.model.uuid)} for row in self.rows],
            "preview": False,
        }
        headers = {"HTTP_X_DELEGATION_ID": str(delegation.uuid)}
        self.assertEqual(
            self.client.post("/api/lens/assistants/replace-model/", payload, format="json", **headers).status_code, 403
        )
        payload["preview"] = True
        self.assertEqual(
            self.client.post("/api/lens/assistants/replace-model/", payload, format="json", **headers).status_code, 200
        )

    def test_inactive_admin_membership_or_tenant_cannot_manage_assistants(self):
        """Every management entry requires an active account, member and tenant."""
        updates = [
            (get_user_model().objects.filter(pk=self.admin.pk), "is_active", False, True),
            (TenantMembership.objects.filter(user=self.admin), "status", "disabled", "active"),
            (Tenant.objects.filter(pk=self.tenant.pk), "status", "suspended", "active"),
        ]
        for queryset, field, invalid, valid in updates:
            queryset.update(**{field: invalid})
            self.client.force_authenticate(get_user_model().objects.get(pk=self.admin.pk))
            self.assertEqual(self.post().status_code, 403)
            self.assertEqual(self.client.get("/api/lens/assistants/", {"managed": "true"}).status_code, 403)
            self.assertEqual(self.client.get("/api/lens/assistants/access-subjects/").status_code, 403)
            queryset.update(**{field: valid})
