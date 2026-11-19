"""Verify pausing assistants without archiving configuration or history."""

from agentcore_metering.adapters.django.models import LLMConfig
from django.contrib.auth import get_user_model
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient

from accounts.models import AuditLog, Tenant, TenantMembership
from lens.assistant_lifecycle import AssistantNotRunnableError, create_assistant_session, smart_collaboration_assistants
from lens.models import Assistant, AssistantSkill, LensNode, LLMConfigScope, Session, SharedQA, Skill


class AssistantEnablementTests(TestCase):
    """Only tenant administrators can atomically change runtime availability."""

    def setUp(self):
        """Create a configured assistant with an existing conversation."""
        self.tenant = Tenant.objects.create(name="Enable", slug="enable-a")
        self.user = get_user_model().objects.create_user(username="enable-admin")
        TenantMembership.objects.create(user=self.user, tenant=self.tenant, role="tenant_admin")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.model = LLMConfig.objects.create(scope=LLMConfig.Scope.GLOBAL, provider="openai", config={"model": "chat"})
        LLMConfigScope.objects.create(config=self.model, tenant=self.tenant)
        self.node = LensNode.objects.create(
            name="Enable node", status="online", enrollment_status="approved", tasks=[{"name": "general_chat"}]
        )
        self.assistant = Assistant.objects.create(
            name="Enable assistant",
            slug="enable-assistant",
            tenant=self.tenant,
            lensnode=self.node,
            agent_model_ref=self.model.uuid,
            visibility="tenant",
        )
        self.skill = Skill.objects.create(tenant=self.tenant, name="Enable skill", definition={})
        AssistantSkill.objects.create(assistant=self.assistant, skill=self.skill)
        self.session = Session.objects.create(assistant=self.assistant, user=self.user, tenant=self.tenant)

    def payload(self, rows=None, **extra):
        """Pin the latest observed configuration versions."""
        return {
            "assistants": [
                {"uuid": str(row.uuid), "updated_at": row.updated_at.isoformat()} for row in rows or [self.assistant]
            ],
            "enabled": False,
            **extra,
        }

    def apply(self, rows=None, **extra):
        """Submit the same operation used by single and batch controls."""
        return self.client.post("/api/lens/assistants/bulk-enablement/", self.payload(rows, **extra), format="json")

    def test_disable_preserves_configuration_sessions_and_published_shares(self):
        """Pause future execution while leaving historical resources readable."""
        share = SharedQA.objects.create(assistant=self.assistant, token="enabled-history", question="q", answer="a")
        response = self.apply()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["updated"], 1)
        self.assistant.refresh_from_db()
        self.assertFalse(self.assistant.is_enabled)
        self.assertEqual(self.assistant.status, "active")
        self.assertEqual(self.assistant.agent_model_ref, self.model.uuid)
        self.assertTrue(Session.objects.filter(pk=self.session.pk).exists())
        self.assertEqual(self.client.get(f"/api/lens/public/qa/{share.token}/").status_code, 200)
        self.assertEqual(self.client.get("/api/lens/assistants/").data["count"], 0)
        self.assertEqual(self.client.get("/api/lens/assistants/", {"managed": "true"}).data["count"], 1)
        self.assertEqual(AuditLog.objects.get(action="assistant.bulk_enablement").tenant, self.tenant)

    def test_disabled_assistants_reject_sessions_questions_uploads_and_collaboration(self):
        """No execution entry can bypass the runtime switch."""
        self.assertEqual(self.apply().status_code, 200)
        self.assistant.refresh_from_db()
        self.assertFalse(self.assistant.is_runnable_by(self.user))
        with self.assertRaises(AssistantNotRunnableError):
            create_assistant_session(self.assistant.uuid, self.user)
        self.assertEqual(
            self.client.post(
                f"/api/lens/sessions/{self.session.uuid}/runs/", {"question": "q"}, format="json"
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(f"/api/lens/sessions/{self.session.uuid}/attachments/", {}, format="json").status_code, 403
        )
        self.assertEqual(self.client.get(f"/api/lens/sessions/{self.session.uuid}/").status_code, 200)
        with self.assertRaises(AssistantNotRunnableError):
            smart_collaboration_assistants(self.user, [str(self.assistant.uuid)])
        self.assertEqual(self.client.get(f"/api/lens/public/assistants/{self.assistant.slug}/").status_code, 404)

    def test_valid_configuration_can_be_reenabled(self):
        """Enable a paused assistant only after current configuration checks."""
        self.assertEqual(self.apply().status_code, 200)
        self.assistant.refresh_from_db()
        response = self.apply(enabled=True)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["updated"], 1)
        self.assistant.refresh_from_db()
        self.assertTrue(self.assistant.is_enabled)
        self.assertTrue(self.assistant.is_runnable_by(self.user))

    def test_unavailable_models_nodes_and_tools_are_skipped_on_enable(self):
        """Known configuration failures are reported without enabling the row."""
        self.assertEqual(self.apply().status_code, 200)
        self.assistant.refresh_from_db()
        for queryset, field, bad, good in [
            (LLMConfig.objects.filter(pk=self.model.pk), "is_active", False, True),
            (LensNode.objects.filter(pk=self.node.pk), "status", "offline", "online"),
            (Skill.objects.filter(pk=self.skill.pk), "enabled", False, True),
        ]:
            queryset.update(**{field: bad})
            response = self.apply(enabled=True)
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data["updated"], 0)
            self.assertEqual(response.data["skipped_items"][0]["reason"], "configuration")
            self.assistant.refresh_from_db()
            self.assertFalse(self.assistant.is_enabled)
            queryset.update(**{field: good})

    def test_archival_and_restoration_preserve_the_runtime_switch(self):
        """Restore cannot reactivate a deliberately disabled assistant."""
        self.assertEqual(self.apply().status_code, 200)
        self.assertEqual(self.client.post(f"/api/lens/assistants/{self.assistant.uuid}/archive/").status_code, 200)
        self.assertEqual(self.client.post(f"/api/lens/assistants/{self.assistant.uuid}/restore/").status_code, 200)
        self.assistant.refresh_from_db()
        self.assertFalse(self.assistant.is_enabled)
        self.assertEqual(self.assistant.status, "active")

    def test_is_enabled_is_readonly_in_the_generic_editor(self):
        """An ordinary PATCH cannot bypass the explicit enablement checks."""
        response = self.client.patch(
            f"/api/lens/assistants/{self.assistant.uuid}/", {"is_enabled": False}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assistant.refresh_from_db()
        self.assertTrue(self.assistant.is_enabled)

    def test_stale_or_foreign_batch_never_partially_disables_rows(self):
        """Authorization and version validation precede all batch writes."""
        other = Assistant.objects.create(name="Other", slug="enable-other", tenant=self.tenant)
        payload = self.payload([self.assistant, other])
        other.save()
        response = self.client.post("/api/lens/assistants/bulk-enablement/", payload, format="json")
        self.assertEqual(response.status_code, 409)
        self.assistant.refresh_from_db()
        self.assertTrue(self.assistant.is_enabled)
        foreign = Tenant.objects.create(name="Foreign", slug="enable-foreign")
        other.tenant = foreign
        other.save()
        response = self.apply([self.assistant, other])
        self.assertEqual(response.status_code, 400)
        self.assistant.refresh_from_db()
        self.assertTrue(self.assistant.is_enabled)

    def test_archived_and_unchanged_rows_return_distinct_skip_reasons(self):
        """The result explains every inapplicable operation."""
        self.assistant.status = "archived"
        self.assistant.save()
        response = self.apply()
        self.assertEqual(response.data["skipped_items"][0]["reason"], "archived")
        self.assistant.status = "active"
        self.assistant.save()
        response = self.apply(enabled=True)
        self.assertEqual(response.data["skipped_items"][0]["reason"], "unchanged")

    def test_queued_dispatch_rechecks_the_runtime_switch(self):
        """A queued run must not dispatch after its assistant is disabled."""
        from lens.services import LensNodeDispatchError, create_execution_run, validate_run_dispatch

        run = create_execution_run(self.session, "Queued question", enqueue=False)
        self.assertEqual(self.apply().status_code, 200)
        run = type(run).objects.select_related("session__assistant", "execution", "lensnode").get(pk=run.pk)
        with self.assertRaisesMessage(LensNodeDispatchError, "ASSISTANT_DISABLED"):
            validate_run_dispatch(run)

    def test_enable_checks_required_environment_without_exposing_values(self):
        """Missing required values keep an assistant paused with a safe issue."""
        self.assertEqual(self.apply().status_code, 200)
        self.assistant.refresh_from_db()
        self.skill.definition = {"environment": [{"name": "PRIVATE_VALUE", "required": True}]}
        self.skill.save()
        response = self.apply(enabled=True)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            response.data["skipped_items"][0]["issues"], [{"code": "configuration_invalid", "field": "environment"}]
        )
        self.assertNotIn("PRIVATE_VALUE", str(response.data))

    def test_enable_checks_all_bound_plugin_connections(self):
        """A healthy skill cannot hide an unavailable enabled Plugin binding."""
        from lens.models import AssistantPluginBinding, Connection

        connection = Connection.objects.create(
            tenant=self.tenant, name="Offline connection", plugin_key="github", status="disabled"
        )
        AssistantPluginBinding.objects.create(assistant=self.assistant, connection=connection)
        self.assertEqual(self.apply().status_code, 200)
        self.assistant.refresh_from_db()
        response = self.apply(enabled=True)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["updated"], 0)
        self.assertEqual(response.data["skipped_items"][0]["reason"], "configuration")

    def test_disabled_rows_are_available_only_in_explicit_history_list(self):
        """History loading still uses normal visibility and tenancy permissions."""
        self.assertEqual(self.apply().status_code, 200)
        response = self.client.get("/api/lens/assistants/", {"include_disabled": "true"})
        self.assertEqual(response.data["count"], 1)
        self.assertFalse(response.data["results"][0]["is_enabled"])
        foreign = Tenant.objects.create(name="Foreign history", slug="enable-history-other")
        member = get_user_model().objects.create_user(username="enable-history-member")
        TenantMembership.objects.create(user=member, tenant=foreign)
        self.client.force_authenticate(member)
        self.assertEqual(self.client.get("/api/lens/assistants/", {"include_disabled": "true"}).data["count"], 0)

    def test_member_api_keys_and_suspended_tenants_cannot_change_enablement(self):
        """Q&A credentials and ordinary membership never gain console writes."""
        membership = TenantMembership.objects.get(user=self.user)
        membership.role = "member"
        membership.save()
        self.client.force_authenticate(get_user_model().objects.get(pk=self.user.pk))
        self.assertEqual(self.apply().status_code, 403)
        membership.role = "tenant_admin"
        membership.save()
        self.client.force_authenticate(self.user, token={"principal_type": "tenant_api_key", "scope": "all"})
        self.assertEqual(self.apply().status_code, 403)
        self.client.force_authenticate(self.user)
        self.tenant.status = "disabled"
        self.tenant.save()
        self.client.force_authenticate(get_user_model().objects.get(pk=self.user.pk))
        self.assertEqual(self.apply().status_code, 403)

    def test_delegation_writes_require_operational_mode_and_record_real_actor(self):
        """Only explicitly operational delegation may alter the tenant switch."""
        from django.utils import timezone

        from accounts.models import Profile, TenantDelegation

        platform = get_user_model().objects.create_user(username="enable-platform")
        Profile.objects.filter(user=platform).update(platform_role="super_admin")
        self.client.force_authenticate(platform)
        self.assertEqual(self.apply().status_code, 403)
        delegation = TenantDelegation.objects.create(
            platform_user=platform,
            tenant=self.tenant,
            mode="readonly",
            expires_at=timezone.now() + timezone.timedelta(hours=1),
        )
        headers = {"HTTP_X_DELEGATION_ID": str(delegation.uuid)}
        url = "/api/lens/assistants/bulk-enablement/"
        self.assertEqual(self.client.post(url, self.payload(), format="json", **headers).status_code, 403)
        delegation.mode = "operational"
        delegation.save()
        response = self.client.post(url, self.payload(), format="json", **headers)
        self.assertEqual(response.status_code, 200, response.data)
        audit = AuditLog.objects.get(action="assistant.bulk_enablement")
        self.assertEqual(audit.actor_user, platform)
        self.assertEqual(audit.delegation, delegation)

    def test_queued_preparation_rechecks_cached_assistant_before_provider_calls(self):
        """A stale worker object cannot preprocess a paused assistant's question."""
        from lens.execution import _lensnode_dispatch
        from lens.services import LensNodeDispatchError, create_execution_run

        run = create_execution_run(self.session, "Queued question", enqueue=False)
        self.assertEqual(self.apply().status_code, 200)
        with self.assertRaisesMessage(LensNodeDispatchError, "ASSISTANT_DISABLED"):
            _lensnode_dispatch({"run": run})

    def test_disabled_collaboration_member_prevents_reenabling_the_team(self):
        """The team remains paused until every configured member is available."""
        team = Assistant.objects.create(
            tenant=self.tenant,
            name="Team",
            slug="enable-team",
            routing_mode="smart",
            agent_model_ref=self.model.uuid,
            is_enabled=False,
        )
        team.collaboration_members.add(self.assistant)
        self.assertEqual(self.apply().status_code, 200)
        response = self.apply([team], enabled=True)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["updated"], 0)
        self.assertEqual(response.data["skipped_items"][0]["reason"], "configuration")

    def test_enablement_batch_applies_only_ready_rows_and_explains_invalid_rows(self):
        """One unavailable configuration cannot prevent unrelated valid enablement."""
        self.assertEqual(self.apply().status_code, 200)
        self.assistant.refresh_from_db()
        invalid = Assistant.objects.create(
            tenant=self.tenant, name="Needs configuration", slug="enable-invalid-batch", is_enabled=False
        )
        response = self.apply([self.assistant, invalid], enabled=True)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual((response.data["updated"], response.data["skipped"]), (1, 1))
        self.assertEqual(response.data["skipped_items"][0]["uuid"], str(invalid.uuid))
        self.assertEqual(response.data["skipped_items"][0]["reason"], "configuration")
        self.assistant.refresh_from_db()
        invalid.refresh_from_db()
        self.assertTrue(self.assistant.is_enabled)
        self.assertFalse(invalid.is_enabled)

    def test_uninstalled_plugin_is_a_safe_configuration_skip(self):
        """Missing catalog packages must not crash or expose stored configuration."""
        from lens.models import AssistantPluginBinding, Connection

        connection = Connection.objects.create(
            tenant=self.tenant, name="Uninstalled", plugin_key="uninstalled-plugin", status="active"
        )
        AssistantPluginBinding.objects.create(assistant=self.assistant, connection=connection)
        self.assertEqual(self.apply().status_code, 200)
        self.assistant.refresh_from_db()
        response = self.apply(enabled=True)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["updated"], 0)
        self.assertEqual(response.data["skipped_items"][0]["reason"], "configuration")


class AssistantEnablementConcurrencyTests(TransactionTestCase):
    """Serialize the switch with creation of new sessions."""

    def test_disable_waits_for_an_admitted_session_and_blocks_the_next(self):
        """An operation holding the assistant lock completes before disable returns."""
        import threading
        from unittest.mock import patch

        from django.db import close_old_connections

        tenant = Tenant.objects.create(name="Race", slug="enable-race")
        user = get_user_model().objects.create_user(username="enable-race-admin")
        TenantMembership.objects.create(user=user, tenant=tenant, role="tenant_admin")
        assistant = Assistant.objects.create(
            tenant=tenant, name="Race", slug="enable-race-assistant", visibility="tenant"
        )
        entered = threading.Event()
        release = threading.Event()
        disabling = threading.Event()
        finished = threading.Event()
        errors = []
        responses = []
        check = Assistant.is_runnable_by

        def pause(locked, caller):
            """Pause while the new session owns the assistant row lock."""
            result = check(locked, caller)
            entered.set()
            if not release.wait(5):
                raise TimeoutError("Timed out waiting for the session lock")
            return result

        def create():
            """Create new work using a separate database connection."""
            close_old_connections()
            try:
                create_assistant_session(assistant.uuid, user)
            except Exception as exc:
                errors.append(exc)
            finally:
                close_old_connections()

        def disable():
            """Submit the runtime change from an independent request."""
            close_old_connections()
            try:
                client = APIClient()
                client.force_authenticate(get_user_model().objects.get(pk=user.pk))
                disabling.set()
                responses.append(
                    client.post(
                        "/api/lens/assistants/bulk-enablement/",
                        {
                            "assistants": [
                                {"uuid": str(assistant.uuid), "updated_at": assistant.updated_at.isoformat()}
                            ],
                            "enabled": False,
                        },
                        format="json",
                    )
                )
            except Exception as exc:
                errors.append(exc)
            finally:
                finished.set()
                close_old_connections()

        with patch.object(Assistant, "is_runnable_by", pause):
            creator = threading.Thread(target=create)
            disabler = threading.Thread(target=disable)
            creator.start()
            try:
                self.assertTrue(entered.wait(5))
                disabler.start()
                self.assertTrue(disabling.wait(5))
                self.assertFalse(finished.wait(0.3))
            finally:
                release.set()
                creator.join(5)
                if disabler.ident is not None:
                    disabler.join(5)
        self.assertFalse(creator.is_alive())
        self.assertFalse(disabler.is_alive())
        if errors:
            raise errors[0]
        self.assertEqual(responses[0].status_code, 200, responses[0].data)
        self.assertEqual(Session.objects.filter(assistant=assistant).count(), 1)
        with self.assertRaises(AssistantNotRunnableError):
            create_assistant_session(assistant.uuid, user)
