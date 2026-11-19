"""Model management, provider spending and immutable billing isolation."""

from datetime import timedelta
from unittest.mock import patch

from agentcore_metering.adapters.django.models import LLMConfig, LLMUsage
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import AuditLog, Profile, Tenant, TenantDelegation, TenantMembership
from lens.lensnode_auth import hash_lensnode_token
from lens.model_scope import model_call_scope
from lens.models import Assistant, LensNode, LLMConfigScope, LLMUsageScope, Message, Run, RunExecution, Session

ROOT = "/api/v1/admin/"


class ModelTenancyTests(TestCase):
    """All legacy URL shapes must enforce the same boundary."""

    def setUp(self):
        self.a = Tenant.objects.create(name="Model A", slug="model-a")
        self.b = Tenant.objects.create(name="Model B", slug="model-b")
        users = get_user_model()
        self.admin = users.objects.create_user(username="model-a")
        self.staff = users.objects.create_user(username="model-b", is_staff=True)
        self.platform = users.objects.create_user(username="model-platform")
        Profile.objects.update_or_create(user=self.platform, defaults={"platform_role": "super_admin"})
        for user, tenant in ((self.admin, self.a), (self.staff, self.b)):
            TenantMembership.objects.create(user=user, tenant=tenant, role="tenant_admin")
        self.ca = self.model(self.a)
        self.cb = self.model(self.b)
        self.cp = self.model(None)
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def model(self, tenant, **kwargs):
        """Fixtures explicitly own their configurations."""

        config = LLMConfig.objects.create(
            provider="openai", config={"api_key": "private-provider-key", "model": "gpt-4o-mini"}, **kwargs
        )
        LLMConfigScope.objects.create(config=config, tenant=tenant)
        return config

    def test_nonstaff_tenant_admin_sees_only_owned_models(self):
        for suffix in ("llm-config/", "llm-config/all/"):
            response = self.client.get(ROOT + suffix)
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual([row["uuid"] for row in response.data], [str(self.ca.uuid)])
            self.assertNotIn("private-provider-key", str(response.data))

    def test_platform_and_tenant_are_separate_and_unowned_models_stay_hidden(self):
        LLMConfig.objects.create(provider="openai")
        self.client.force_authenticate(self.platform)
        response = self.client.get(ROOT + "llm-config/all/")
        self.assertEqual([row["uuid"] for row in response.data], [str(self.cp.uuid)])
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.get(ROOT + "llm-config/", HTTP_X_MODEL_SCOPE="platform").status_code, 403)

    def test_cross_scope_detail_update_delete_are_not_found(self):
        for ref in (str(self.cb.uuid), self.cb.pk, str(self.cp.uuid)):
            url = ROOT + f"llm-config/{ref}/"
            self.assertEqual(self.client.get(url).status_code, 404)
            self.assertEqual(self.client.put(url, {"is_active": False}, format="json").status_code, 404)
            self.assertEqual(self.client.delete(url).status_code, 404)

    def test_cross_scope_tests_never_contact_provider(self):
        with patch("agentcore_metering.adapters.django.views.config_validation.validate_llm_config") as validate, patch(
            "agentcore_metering.adapters.django.views.config_validation.run_test_call"
        ) as call:
            for ref in (self.cb, self.cp):
                payload = {"config_uuid": str(ref.uuid), "provider": "openai", "config": {"api_key": "***"}}
                self.assertEqual(self.client.post(ROOT + "llm-config/test/", payload, format="json").status_code, 404)
                payload["prompt"] = "test"
                self.assertEqual(
                    self.client.post(ROOT + "llm-config/test-call/", payload, format="json").status_code, 404
                )
            validate.assert_not_called()
            call.assert_not_called()

    def test_legacy_selector_pair_cannot_authorize_another_id(self):
        payload = {"config_uuid": str(self.ca.uuid), "config_id": self.cb.pk, "prompt": "test"}
        self.assertEqual(self.client.post(ROOT + "llm-config/test-call/", payload, format="json").status_code, 404)

    def test_create_default_and_update_do_not_modify_other_scopes(self):
        self.cb.is_default = True
        self.cb.save()
        self.cp.is_default = True
        self.cp.save()
        response = self.client.post(
            ROOT + "llm-config/",
            {
                "provider": "openai",
                "config": {"model": "gpt-4o-mini", "api_key": "new-secret"},
                "is_default": True,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        obj = LLMConfig.objects.get(uuid=response.data["uuid"])
        self.assertEqual(obj.ownership.tenant, self.a)
        response = self.client.put(
            ROOT + f"llm-config/{obj.uuid}/",
            {
                "config": {"model": "gpt-4o-mini", "api_key": "***"},
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        obj.refresh_from_db()
        self.assertEqual(obj.config["api_key"], "new-secret")
        for config in (self.cb, self.cp):
            config.refresh_from_db()
            self.assertTrue(config.is_default)

    def test_bulk_foreign_selection_is_atomic(self):
        response = self.client.post(
            ROOT + "llm-config/bulk/",
            {
                "config_ids": [str(self.ca.uuid), str(self.cb.uuid)],
                "action": "disable",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.ca.refresh_from_db()
        self.assertTrue(self.ca.is_active)

    def test_disabling_default_clears_only_its_scope_default(self):
        self.ca.is_default = True
        self.ca.save()
        response = self.client.put(ROOT + f"llm-config/{self.ca.uuid}/", {"is_active": False}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.ca.refresh_from_db()
        self.assertFalse(self.ca.is_active)
        self.assertFalse(self.ca.is_default)

    def test_nested_masked_credentials_survive_read_edit_roundtrip(self):
        self.ca.config.update({"auth": {"token": "nested-token"}, "headers": [{"secret": "nested-secret"}]})
        self.ca.save()
        url = ROOT + f"llm-config/{self.ca.uuid}/"
        data = self.client.get(url).data
        self.assertNotIn("nested-token", str(data))
        self.assertNotIn("nested-secret", str(data))
        response = self.client.put(url, {"config": data["config"], "is_default": True}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.ca.refresh_from_db()
        self.assertEqual(self.ca.config["auth"]["token"], "nested-token")
        self.assertEqual(self.ca.config["headers"][0]["secret"], "nested-secret")

    def test_vision_fallback_must_support_images(self):
        backup = self.model(self.a)
        backup.config["vision"] = False
        backup.save()
        response = self.client.put(
            ROOT + f"llm-config/{self.ca.uuid}/",
            {"config": {"vision": True, "fallback_model_uuids": [str(backup.uuid)]}},
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.ca.refresh_from_db()
        self.assertNotIn("fallback_model_uuids", self.ca.config)

    def test_defaults_are_independent_per_model_type(self):
        embedding = self.model(self.a, model_type="embedding")
        embedding.config["model"] = "text-embedding-3-small"
        embedding.save()
        self.ca.is_default = True
        self.ca.save()
        response = self.client.put(ROOT + f"llm-config/{embedding.uuid}/", {"is_default": True}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.ca.refresh_from_db()
        embedding.refresh_from_db()
        self.assertTrue(self.ca.is_default)
        self.assertTrue(embedding.is_default)

    def test_fallbacks_reject_foreign_duplicate_and_cycles(self):
        local = self.model(self.a)
        for refs in ([str(self.cb.uuid)], [str(local.uuid), str(local.uuid)], [str(self.ca.uuid)]):
            response = self.client.put(
                ROOT + f"llm-config/{self.ca.uuid}/",
                {
                    "config": {"fallback_model_uuids": refs},
                },
                format="json",
            )
            self.assertEqual(response.status_code, 400, response.data)
        local.config["fallback_model_uuids"] = [str(self.ca.uuid)]
        local.save()
        response = self.client.put(
            ROOT + f"llm-config/{self.ca.uuid}/",
            {
                "config": {"fallback_model_uuids": [str(local.uuid)]},
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.data)

    def test_usage_ownership_survives_membership_move(self):
        with model_call_scope(self.a):
            usage = LLMUsage.objects.create(user=self.admin, model="example", total_tokens=12)
        TenantMembership.objects.filter(user=self.admin).update(tenant=self.b)
        usage.refresh_from_db()
        self.assertEqual(usage.ownership.tenant, self.a)

    def test_member_and_staff_without_membership_cannot_manage_models(self):
        """Staff flags do not substitute for model-administrator ownership."""

        for role in ("member", None):
            user = get_user_model().objects.create_user(username=f"model-{role}", is_staff=True)
            if role:
                TenantMembership.objects.create(user=user, tenant=self.a, role=role)
            self.client.force_authenticate(user)
            self.assertEqual(self.client.get(ROOT + "llm-config/all/").status_code, 403)

    def test_delegation_selects_tenant_and_readonly_blocks_spending(self):
        """Platform operators must explicitly enter a tenant to manage its models."""

        delegation = TenantDelegation.objects.create(
            platform_user=self.platform,
            tenant=self.a,
            reason="Model acceptance",
            expires_at=timezone.now() + timedelta(minutes=10),
            mode="readonly",
        )
        self.client.force_authenticate(self.platform)
        headers = {"HTTP_X_DELEGATION_ID": str(delegation.uuid)}
        response = self.client.get(ROOT + "llm-config/all/", **headers)
        self.assertEqual([row["uuid"] for row in response.data], [str(self.ca.uuid)])
        for suffix, body in (
            ("llm-config/", {"provider": "openai"}),
            ("llm-config/test-call/", {"config_uuid": str(self.ca.uuid), "prompt": "test"}),
            ("llm-config/test/", {"provider": "openai"}),
        ):
            self.assertEqual(self.client.post(ROOT + suffix, body, format="json", **headers).status_code, 403)
        delegation.mode = "operational"
        delegation.save()
        response = self.client.post(ROOT + "llm-config/", {"provider": "openai"}, format="json", **headers)
        self.assertEqual(response.status_code, 201, response.data)
        config = LLMConfig.objects.get(uuid=response.data["uuid"])
        self.assertEqual(config.ownership.tenant, self.a)
        self.assertTrue(AuditLog.objects.filter(action="llm_config.create", tenant=self.a).exists())

    def test_stream_usage_is_owned_during_iteration(self):
        """Streaming runs after the HTTP handler returns and must retain billing scope."""

        def stream(**kwargs):
            """Record inside lazy iteration just like the vendor tracker."""

            LLMUsage.objects.create(user=self.platform, model="stream", total_tokens=3)
            yield "content", "ok"
            return {"total_tokens": 3}

        with patch("agentcore_metering.adapters.django.views.config_validation.run_test_call_stream", stream):
            response = self.client.post(
                ROOT + "llm-config/test-call/",
                {
                    "config_uuid": str(self.ca.uuid),
                    "prompt": "test",
                    "stream": True,
                },
                format="json",
            )
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"ok", b"".join(response.streaming_content))
        self.assertEqual(LLMUsage.objects.get(model="stream").ownership.tenant, self.a)
        unknown = LLMUsage.objects.create(user=self.admin, model="unknown")
        self.assertFalse(LLMUsageScope.objects.filter(usage=unknown).exists())

    def test_usage_and_stats_only_include_owned_rows_and_separate_currencies(self):
        """Global series, arbitrary user filters and provider errors cannot leak data."""

        for tenant, tokens, cost, currency in (
            (self.a, 10, 1, "USD"),
            (self.a, 20, 2, "CNY"),
            (self.a, 1, None, "USD"),
            (self.b, 200, 500, "USD"),
            (None, 900, 1000, "USD"),
        ):
            with model_call_scope(tenant):
                LLMUsage.objects.create(
                    user=self.admin,
                    model="scoped",
                    total_tokens=tokens,
                    cost=cost,
                    cost_currency=currency,
                    success=False,
                    error="private-provider-key",
                    metadata={"prompt": "private prompt"},
                )
        response = self.client.get(
            ROOT + "token-stats/",
            {
                "granularity": "day",
                "use_series": 1,
                "start_date": timezone.now().date().isoformat(),
                "end_date": timezone.now().date().isoformat(),
            },
        )
        self.assertEqual(response.status_code, 200, response.data)
        summary = response.data["summary"]
        self.assertEqual(summary["total_tokens"], 31)
        self.assertEqual(summary["cost_totals"], {"USD": 1.0, "CNY": 2.0})
        self.assertIsNone(summary["total_cost"])
        self.assertEqual(summary["unpriced_calls"], 1)
        self.assertEqual(sum(row["total_tokens"] for row in response.data["series"]["items"]), 31)
        response = self.client.get(ROOT + "llm-usage/")
        self.assertEqual(response.data["total"], 3)
        self.assertNotIn("private", str(response.data))
        for suffix in ("token-stats/", "llm-usage/"):
            self.assertEqual(self.client.get(ROOT + suffix, {"start_date": "2026-99-99"}).status_code, 400)

    def test_timing_series_is_scoped_and_uses_recorded_durations(self):
        now = timezone.now()
        for tenant, duration, tokens in ((self.a, 4, 20), (self.a, 8, 80), (self.b, 100, 10000)):
            with model_call_scope(tenant):
                usage = LLMUsage.objects.create(
                    model="timed",
                    started_at=now - timedelta(seconds=duration),
                    first_chunk_at=now - timedelta(seconds=duration - 1),
                    is_streaming=True,
                    completion_tokens=tokens,
                )
                LLMUsage.objects.filter(pk=usage.pk).update(created_at=now)
        response = self.client.get(
            ROOT + "token-stats/",
            {"granularity": "day", "start_date": now.date().isoformat(), "end_date": now.date().isoformat()},
        )
        self.assertEqual(response.status_code, 200, response.data)
        row = response.data["series_by_model"][0]
        self.assertEqual(row["avg_e2e_latency_sec"], 6)
        self.assertEqual(row["avg_ttft_sec"], 1)
        self.assertEqual(row["avg_output_tps"], 7.5)

    def run_fixture(self):
        """Freeze an actual model into an authenticated node's execution."""

        node = LensNode.objects.create(
            name="Model node", enrollment_status="approved", auth_token_hash=hash_lensnode_token("model-node-token")
        )
        assistant = Assistant.objects.create(tenant=self.a, name="A", slug="model-a", agent_model_ref=self.ca.uuid)
        session = Session.objects.create(tenant=self.a, assistant=assistant, user=self.admin)
        message = Message.objects.create(session=session, role="user", sequence=1, content="Question")
        run = Run.objects.create(tenant=self.a, session=session, lensnode=node, input_message=message, status="running")
        RunExecution.objects.create(
            run=run,
            lensnode=node,
            task="general_chat",
            runtime_snapshot={"model_refs": {"agent": str(self.ca.uuid)}},
        )
        return run

    def test_runtime_primary_and_fallback_cannot_spend_foreign_credentials(self):
        """Authorization runs before provider I/O for sync and lazy stream paths."""

        from lens.llm_resilience import call_and_track_with_fallback, fallback_model_refs

        self.ca.config["fallback_model_uuids"] = [str(self.cb.uuid), str(self.cp.uuid)]
        self.ca.save()
        self.assertEqual(fallback_model_refs(self.ca.uuid, user_id=self.admin.pk), [])
        with patch("agentcore_metering.adapters.django.LLMTracker.call_and_track") as provider:
            for model in (self.cb, self.cp):
                for stream in (False, True):
                    with self.assertRaises(ValueError):
                        result = call_and_track_with_fallback(
                            model_uuid=str(model.uuid),
                            messages=[{"role": "user", "content": "private"}],
                            state={"user_id": self.admin.pk},
                            stream=stream,
                        )
                        if stream:
                            list(result)
            provider.assert_not_called()

    def test_gateway_requires_run_and_frozen_same_tenant_model(self):
        """A valid shared-node token is not permission to spend arbitrary credentials."""

        run = self.run_fixture()
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION="Bearer model-node-token")
        payload = {"model_ref": str(self.ca.uuid), "messages": [{"role": "user", "content": "test"}]}
        local_unbound = self.model(self.a)
        with patch("lens.views.gateway.call_and_track_with_fallback", return_value=("ok", {})) as provider:
            url = "/api/lens/lensnode/ai-gateway/"
            self.assertEqual(client.post(url, payload, format="json").status_code, 400)
            payload["run_uuid"] = str(run.uuid)
            for model in (self.cb, self.cp, local_unbound):
                payload["model_ref"] = str(model.uuid)
                self.assertEqual(client.post(url, payload, format="json").status_code, 403)
            provider.assert_not_called()
            payload["model_ref"] = str(self.ca.uuid)
            self.assertEqual(client.post(url, payload, format="json").status_code, 200)
            self.assertEqual(provider.call_args.kwargs["state"]["metadata"]["run_uuid"], str(run.uuid))

    def test_run_provenance_wins_over_current_membership_for_usage(self):
        """Background completion cannot move billing when its actor changes tenant."""

        run = self.run_fixture()
        TenantMembership.objects.filter(user=self.admin).update(tenant=self.b)
        usage = LLMUsage.objects.create(user=self.admin, model="run", metadata={"run_uuid": str(run.uuid)})
        self.assertEqual(usage.ownership.tenant, self.a)

    def test_tenant_cannot_manage_global_retention_and_policy_save_is_atomic(self):
        """Malformed cron must not partially save retention before returning 400."""

        from agentcore_metering.adapters.django.models import MeteringConfig
        from django_celery_beat.models import PeriodicTask

        url = ROOT + "metering-config/"
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_authenticate(self.platform)
        self.assertEqual(
            self.client.patch(
                url,
                {
                    "retention_days": 91,
                    "aggregation_crontab": "99 99 * * *",
                },
                format="json",
            ).status_code,
            400,
        )
        self.assertFalse(MeteringConfig.objects.filter(key="retention_days", value=91).exists())
        response = self.client.patch(
            url,
            {
                "retention_days": 91,
                "cleanup_crontab": "12 3 * * *",
                "cleanup_enabled": True,
                "aggregation_crontab": "5 * * * *",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        task = PeriodicTask.objects.get(name="agentcore-metering-cleanup-old-usage")
        self.assertTrue(task.enabled)
        self.assertEqual(task.crontab.minute, "12")
        self.assertEqual(self.client.patch(url, {"cleanup_enabled": False}, format="json").status_code, 200)
        task.refresh_from_db()
        self.assertFalse(task.enabled)
        self.assertEqual(task.crontab.minute, "12")
