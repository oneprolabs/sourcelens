"""Datasource conversion spending must retain trusted task and tenant provenance."""

from datetime import timedelta
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import patch

from agentcore_metering.adapters.django.models import LLMConfig, LLMUsage
from agentcore_task.adapters.django.models import TaskExecution
from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Profile, Tenant, TenantDelegation, TenantMembership
from lens.lensnode_auth import hash_lensnode_token
from lens.models import DataSource, GlobalSetting, LensNode, LLMConfigScope


class ConversionModelTenancyTests(TestCase):
    """Exercise actual datasource APIs, dispatch and authenticated gateway calls."""

    def setUp(self):
        self.a = Tenant.objects.create(name="Convert A", slug="convert-a")
        self.b = Tenant.objects.create(name="Convert B", slug="convert-b")
        self.user = get_user_model().objects.create_user(username="converter")
        TenantMembership.objects.create(user=self.user, tenant=self.a, role="tenant_admin")
        self.node = LensNode.objects.create(
            name="Conversion node",
            status="online",
            workspace_path="/workspace",
            enrollment_status="approved",
            auth_token_hash=hash_lensnode_token("conversion-node-token"),
        )
        self.source = DataSource.objects.create(
            tenant=self.a, name="Upload", source_type="upload", plugin_key="file_upload", lensnode=self.node
        )
        self.ca = self.model(self.a)
        self.cb = self.model(self.b)
        self.cp = self.model(None)
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.node_client = APIClient()
        self.node_client.credentials(HTTP_AUTHORIZATION="Bearer conversion-node-token")

    def model(self, tenant):
        """Own each vision-capable fixture explicitly."""

        model = LLMConfig.objects.create(
            provider="openai", config={"model": "gpt-4o", "supports_vision": True, "api_key": "test-conversion-key"}
        )
        LLMConfigScope.objects.create(config=model, tenant=tenant)
        return model

    def dispatch(self, conversion=None):
        """Capture the real server-issued command for a registered task."""

        from lens.datasource.services import dispatch_datasource_conversion_async
        from lens.tasks import register_datasource_conversion_task

        conversion = conversion or {"image": True, "vision_model_ref": str(self.ca.uuid)}
        task = register_datasource_conversion_task(self.source, "conversion-task", conversion, created_by=self.user)
        with patch("lens.datasource.services._send_lensnode_command") as send:
            dispatch_datasource_conversion_async(self.source, task.task_id, conversion)
        return task, send.call_args.args[1]

    def call(self, command, **overrides):
        """Redeem conversion authorization without inventing a Run."""

        payload = {
            "model_ref": command["conversion"]["vision_model_ref"],
            "conversion_token": command["conversion_token"],
            "messages": [{"role": "user", "content": "Describe this image"}],
        }
        payload.update(overrides)
        return self.node_client.post("/api/lens/lensnode/ai-gateway/", payload, format="json")

    def test_datasource_policy_rejects_foreign_and_platform_model_overrides(self):
        url = f"/api/lens/admin/datasources/{self.source.uuid}/"
        for field in ("vision_model_ref", "document_model_ref"):
            for model in (self.cb, self.cp):
                response = self.client.patch(
                    url, {"sync_policy": {"conversion": {"image": True, field: str(model.uuid)}}}, format="json"
                )
                self.assertEqual(response.status_code, 400, response.data)

    def test_explicit_conversion_rejects_foreign_model_before_queueing(self):
        with patch("lens.views.datasources.datasource_conversion_task.apply_async") as enqueue:
            response = self.client.post(
                f"/api/lens/admin/datasources/{self.source.uuid}/convert/",
                {"conversion": {"image": True, "vision_model_ref": str(self.cb.uuid)}},
                format="json",
            )
        self.assertEqual(response.status_code, 400, response.data)
        enqueue.assert_not_called()
        self.assertFalse(TaskExecution.objects.filter(metadata__datasource_uuid=str(self.source.uuid)).exists())

    def test_task_authorizes_only_frozen_model_and_bills_source_tenant(self):
        task, command = self.dispatch()
        TenantMembership.objects.filter(user=self.user).update(tenant=self.b)

        def provider(**kwargs):
            """Keep the real scope wrapper and persist the resulting usage."""

            state = kwargs["state"]
            LLMUsage.objects.create(user=self.user, model="conversion", metadata=state["metadata"])
            return "Image description", {"total_tokens": 3}

        with patch("agentcore_metering.adapters.django.LLMTracker.call_and_track", side_effect=provider) as call:
            for model in (self.cb, self.cp, self.model(self.a)):
                self.assertEqual(self.call(command, model_ref=str(model.uuid)).status_code, 403)
            call.assert_not_called()
            response = self.call(command)
        self.assertEqual(response.status_code, 200, response.data)
        usage = LLMUsage.objects.get(model="conversion")
        self.assertEqual(usage.ownership.tenant, self.a)
        self.assertEqual(usage.metadata["datasource_uuid"], str(self.source.uuid))
        self.assertEqual(usage.metadata["task_id"], task.task_id)

    def test_platform_default_is_server_selected_and_keeps_business_billing(self):
        GlobalSetting.objects.create(key="lens.datasource_conversion.vision_model_ref", value=str(self.cp.uuid))
        task, command = self.dispatch({"image": True})
        self.assertEqual(command["conversion"]["vision_model_ref"], str(self.cp.uuid))

        def provider(**kwargs):
            """Persist usage under the real metering context."""

            LLMUsage.objects.create(model="platform-conversion", metadata=kwargs["state"]["metadata"])
            return "ok", {}

        with patch("agentcore_metering.adapters.django.LLMTracker.call_and_track", side_effect=provider):
            response = self.call(command, _platform_model=False, tenant_uuid=str(self.b.uuid))
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(LLMUsage.objects.get(model="platform-conversion").ownership.tenant, self.a)

    def test_completed_cancelled_or_missing_task_cannot_redeem_grant(self):
        task, command = self.dispatch()
        with patch("lens.views.gateway.call_and_track_with_fallback") as provider:
            for state in ("SUCCESS", "FAILURE", "REVOKED", "CANCELLING"):
                TaskExecution.objects.filter(pk=task.pk).update(status=state)
                self.assertEqual(self.call(command).status_code, 403, state)
            task.delete()
            self.assertEqual(self.call(command).status_code, 403)
        provider.assert_not_called()

    def test_tampered_token_wrong_node_and_expiry_are_rejected(self):
        task, command = self.dispatch()
        LensNode.objects.create(
            name="Other", enrollment_status="approved", auth_token_hash=hash_lensnode_token("other-conversion-token")
        )
        with patch("lens.views.gateway.call_and_track_with_fallback") as provider:
            self.assertEqual(self.call(command, conversion_token=command["conversion_token"] + "x").status_code, 403)
            self.node_client.credentials(HTTP_AUTHORIZATION="Bearer other-conversion-token")
            self.assertEqual(self.call(command).status_code, 403)
            self.node_client.credentials(HTTP_AUTHORIZATION="Bearer conversion-node-token")
            with patch("django.core.signing.time.time", return_value=9999999999):
                self.assertEqual(self.call(command).status_code, 403)
        provider.assert_not_called()

    def test_runtime_rechecks_source_tenant_and_model_status(self):
        task, command = self.dispatch()
        with patch("lens.views.gateway.call_and_track_with_fallback") as provider:
            self.source.tenant = self.b
            self.source.save(update_fields=["tenant"])
            self.assertEqual(self.call(command).status_code, 403)
            self.source.tenant = self.a
            self.source.status = "disabled"
            self.source.save(update_fields=["tenant", "status"])
            self.assertEqual(self.call(command).status_code, 403)
            self.source.status = "active"
            self.source.save(update_fields=["status"])
            self.ca.is_active = False
            self.ca.save()
            self.assertEqual(self.call(command).status_code, 403)
        provider.assert_not_called()

    def test_legacy_default_backfill_preserves_only_owner_datasources(self):
        """Never turn migrated tenant credentials into shared platform defaults."""

        other = DataSource.objects.create(tenant=self.b, name="Other", source_type="upload")
        GlobalSetting.objects.create(key="lens.datasource_conversion.vision_model_ref", value=str(self.ca.uuid))
        GlobalSetting.objects.create(key="lens.datasource_conversion.document_model_ref", value=str(self.cp.uuid))
        backfill = import_module("lens.migrations.0070_multitenancy").backfill_scope_conversion_defaults
        for _ in range(2):
            backfill(apps, SimpleNamespace(connection=SimpleNamespace(alias="default")))
        self.source.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(self.source.sync_policy["conversion"]["vision_model_ref"], str(self.ca.uuid))
        self.assertFalse(other.sync_policy.get("conversion"))
        self.assertEqual(GlobalSetting.objects.get(key="lens.datasource_conversion.vision_model_ref").value, "")
        self.assertEqual(
            GlobalSetting.objects.get(key="lens.datasource_conversion.document_model_ref").value, str(self.cp.uuid)
        )

    def test_dispatch_rejects_foreign_background_override_before_sending(self):
        """Scheduled or resumed jobs do not rely on earlier HTTP validation."""

        from lens.datasource.services import DataSourceDispatchError, dispatch_datasource_conversion_async

        for model in (self.cb, self.cp):
            with patch("lens.datasource.services._send_lensnode_command") as send:
                with self.assertRaises(DataSourceDispatchError):
                    dispatch_datasource_conversion_async(
                        self.source, "background-job", {"image": True, "vision_model_ref": str(model.uuid)}
                    )
            send.assert_not_called()

    def test_grants_are_not_interchangeable_with_run_stream_or_tools(self):
        task, command = self.dispatch()
        with patch("lens.views.gateway.call_and_track_with_fallback") as provider:
            for fields in ({"run_uuid": "fake-run"}, {"stream": True}, {"tools": [{"type": "function"}]}):
                self.assertEqual(self.call(command, **fields).status_code, 400)
        provider.assert_not_called()

    def test_inactive_tenant_or_changed_node_cannot_redeem_grant(self):
        task, command = self.dispatch()
        with patch("lens.views.gateway.call_and_track_with_fallback") as provider:
            self.a.status = "suspended"
            self.a.save(update_fields=["status"])
            self.assertEqual(self.call(command).status_code, 403)
            self.a.status = "active"
            self.a.save(update_fields=["status"])
            replacement = LensNode.objects.create(name="Replacement")
            self.source.lensnode = replacement
            self.source.save(update_fields=["lensnode"])
            self.assertEqual(self.call(command).status_code, 403)
        provider.assert_not_called()

    def test_dispatch_and_runtime_require_image_capability(self):
        """A text-only or subsequently downgraded model cannot process images."""

        from lens.datasource.services import DataSourceDispatchError

        task, command = self.dispatch()
        self.ca.config["supports_vision"] = False
        self.ca.save()
        with patch("lens.views.gateway.call_and_track_with_fallback") as provider:
            self.assertEqual(self.call(command).status_code, 403)
        provider.assert_not_called()
        with self.assertRaises(DataSourceDispatchError):
            self.dispatch()

    def test_upload_and_legacy_sync_use_the_same_grant_contract(self):
        """Both automatic conversion entry points retain task and source attribution."""

        from lens.datasource.services import dispatch_datasource_sync_async, dispatch_datasource_upload_begin
        from lens.tasks import register_datasource_sync_task, register_datasource_upload_task

        self.source.sync_policy = {"conversion": {"image": True, "vision_model_ref": str(self.ca.uuid)}}
        self.source.save(update_fields=["sync_policy"])
        register_datasource_upload_task(self.source, "upload-task", "image.png", created_by=self.user)
        with patch("lens.datasource.services._send_lensnode_command") as send:
            dispatch_datasource_upload_begin(self.source, "upload-task", "image.png", 7, "digest")
        upload = send.call_args.args[1]
        with patch("lens.views.gateway.call_and_track_with_fallback", return_value=("ok", {})):
            self.assertEqual(self.call(upload).status_code, 200)
        self.source.source_type = "git"
        self.source.plugin_key = ""
        self.source.save(update_fields=["source_type", "plugin_key"])
        register_datasource_sync_task(self.source, "sync-task", "timer")
        with patch("lens.datasource.services._send_lensnode_command") as send:
            dispatch_datasource_sync_async(self.source, "sync-task")
        sync = send.call_args.args[1]
        with patch("lens.views.gateway.call_and_track_with_fallback", return_value=("ok", {})):
            self.assertEqual(self.call(sync).status_code, 200)

    def test_platform_default_rejects_text_only_and_foreign_configs(self):
        from lens.serializers import GlobalSettingSerializer

        self.cp.config["supports_vision"] = False
        self.cp.save()
        for model in (self.cp, self.ca):
            serializer = GlobalSettingSerializer(
                data={"key": "lens.datasource_conversion.vision_model_ref", "value": str(model.uuid)}
            )
            self.assertFalse(serializer.is_valid())

    def test_readonly_delegation_cannot_start_conversion(self):
        """The independent grant path does not circumvent delegated write controls."""

        platform = get_user_model().objects.create_user(username="conversion-platform")
        Profile.objects.update_or_create(user=platform, defaults={"platform_role": "super_admin"})
        grant = TenantDelegation.objects.create(
            platform_user=platform,
            tenant=self.a,
            reason="Inspect conversion",
            mode="readonly",
            expires_at=timezone.now() + timedelta(minutes=5),
        )
        self.client.force_authenticate(platform)
        with patch("lens.views.datasources.datasource_conversion_task.apply_async") as enqueue:
            response = self.client.post(
                f"/api/lens/admin/datasources/{self.source.uuid}/convert/",
                {"conversion": {"image": True, "vision_model_ref": str(self.ca.uuid)}},
                format="json",
                HTTP_X_DELEGATION_ID=str(grant.uuid),
            )
        self.assertEqual(response.status_code, 403)
        enqueue.assert_not_called()
        headers = {"HTTP_X_DELEGATION_ID": str(grant.uuid)}
        url = f"/api/lens/admin/datasources/{self.source.uuid}/"
        self.assertEqual(self.client.get(url, **headers).status_code, 200)
        self.assertEqual(self.client.patch(url, {"name": "Changed"}, format="json", **headers).status_code, 403)
        self.assertEqual(self.client.post(url + "uploads/chunked/init/", {}, format="json", **headers).status_code, 403)
        grant.mode = "operational"
        grant.save(update_fields=["mode"])
        with patch("lens.views.datasources.datasource_conversion_task.apply_async") as enqueue:
            response = self.client.post(
                url + "convert/",
                {"conversion": {"image": True, "vision_model_ref": str(self.ca.uuid)}},
                format="json",
                **headers,
            )
        self.assertEqual(response.status_code, 202, response.data)
        enqueue.assert_called_once()

    def test_partial_conversion_cannot_hide_invalid_stored_override(self):
        self.source.sync_policy = {"conversion": {"image": True, "vision_model_ref": str(self.cb.uuid)}}
        self.source.save(update_fields=["sync_policy"])
        with patch("lens.views.datasources.datasource_conversion_task.apply_async") as enqueue:
            response = self.client.post(
                f"/api/lens/admin/datasources/{self.source.uuid}/convert/",
                {"conversion": {"document": True}},
                format="json",
            )
        self.assertEqual(response.status_code, 400, response.data)
        enqueue.assert_not_called()
