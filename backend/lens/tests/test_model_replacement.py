"""Regression coverage for atomic assistant model replacement."""

import uuid
from unittest.mock import patch

from agentcore_metering.adapters.django.models import LLMConfig
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from lens.models import Assistant


class AssistantModelReplacementTests(TestCase):
    """Verify preview, validation, permissions, and concurrency checks."""

    def setUp(self):
        """Create two assistants sharing an old model."""

        self.client = APIClient()
        self.admin = get_user_model().objects.create_user(username="model-admin", is_staff=True)
        self.client.force_authenticate(self.admin)
        self.old = self.model("gpt-4o-mini")
        self.target = self.model("gpt-4o")
        self.rows = [
            Assistant.objects.create(name=f"Assistant {index}", slug=f"replace-{index}", agent_model_ref=self.old.uuid)
            for index in range(2)
        ]
        self.payload = {
            "model_field": "agent_model_ref",
            "target_model_ref": str(self.target.uuid),
            "assistant_models": [{"uuid": str(row.uuid), "model_ref": str(self.old.uuid)} for row in self.rows],
            "preview": True,
        }

    def model(self, name, **kwargs):
        """Create a global model configuration."""

        return LLMConfig.objects.create(
            scope=LLMConfig.Scope.GLOBAL, provider="openai", config={"model": name, "api_key": "test-key"}, **kwargs
        )

    def post(self, **changes):
        """Submit a preview or replacement request."""

        return self.client.post("/api/lens/assistants/replace-model/", {**self.payload, **changes}, format="json")

    def test_preview_does_not_write_and_apply_updates_only_requested_field(self):
        """Preview is read-only and applying preserves other configuration."""

        response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 2)
        self.rows[0].refresh_from_db()
        self.assertEqual(self.rows[0].agent_model_ref, self.old.uuid)
        response = self.post(preview=False)
        self.assertEqual(response.status_code, 200)
        for row in self.rows:
            row.refresh_from_db()
            self.assertEqual(row.agent_model_ref, self.target.uuid)
            self.assertIsNone(row.multimodal_model_ref)
            self.assertEqual(row.settings["_model_check"]["agent_model_ref"]["status"], "ok")

    def test_changed_model_rejects_whole_batch(self):
        """A stale preview never partially updates the remaining assistants."""

        self.rows[1].agent_model_ref = None
        self.rows[1].save()
        response = self.post(preview=False)
        self.assertEqual(response.status_code, 409)
        self.rows[0].refresh_from_db()
        self.assertEqual(self.rows[0].agent_model_ref, self.old.uuid)

    def test_missing_archived_and_system_assistants_reject_whole_batch(self):
        """Only existing active non-system assistants may be replaced."""

        for changes in ({"status": "archived"}, {"is_system": True}):
            Assistant.objects.filter(pk=self.rows[1].pk).update(**changes)
            self.assertEqual(self.post(preview=False).status_code, 400)
            Assistant.objects.filter(pk=self.rows[1].pk).update(status="active", is_system=False)
        missing = [*self.payload["assistant_models"], {"uuid": str(uuid.uuid4()), "model_ref": None}]
        self.assertEqual(self.post(assistant_models=missing, preview=False).status_code, 400)
        self.rows[0].refresh_from_db()
        self.assertEqual(self.rows[0].agent_model_ref, self.old.uuid)

    def test_invalid_target_and_field_are_rejected(self):
        """Disabled, private, embedding and missing targets cannot be assigned."""

        inactive = self.model("inactive", is_active=False)
        embedding = self.model("embedding", model_type="embedding")
        private = self.model("private")
        private.scope = LLMConfig.Scope.USER
        private.user = self.admin
        private.save()
        for target in (inactive.uuid, embedding.uuid, private.uuid, uuid.uuid4()):
            self.assertEqual(self.post(target_model_ref=str(target)).status_code, 400)
        self.assertEqual(self.post(model_field="settings").status_code, 400)
        self.assertEqual(self.post(assistant_models=[]).status_code, 400)
        self.assertEqual(self.post(assistant_models=self.payload["assistant_models"] * 2).status_code, 400)

    @patch("agentcore_metering.adapters.django.services.runtime_config.get_litellm_params")
    def test_vision_replacement_requires_vision_capability(self, mock_params):
        """The multimodal slot accepts only enabled vision models."""

        items = [{"uuid": str(row.uuid), "model_ref": None} for row in self.rows]
        mock_params.return_value = {"model": "custom"}
        self.target.config = {"model": "custom", "supports_vision": False, "api_key": "test-key"}
        self.target.save()
        self.assertEqual(self.post(model_field="multimodal_model_ref", assistant_models=items).status_code, 400)
        vision = LLMConfig.objects.create(
            scope=LLMConfig.Scope.GLOBAL,
            provider="openai",
            config={"model": "gpt-4o", "supports_vision": True, "api_key": "test-key"},
        )
        response = self.post(
            model_field="multimodal_model_ref", assistant_models=items, target_model_ref=str(vision.uuid), preview=False
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.rows[0].refresh_from_db()
        self.assertEqual(self.rows[0].multimodal_model_ref, vision.uuid)
        self.assertEqual(self.rows[0].agent_model_ref, self.old.uuid)

    def test_unset_model_and_noop_replacement(self):
        """Replace default references and include rows already using the target."""

        Assistant.objects.filter(pk=self.rows[0].pk).update(agent_model_ref=None, settings={"custom": "keep"})
        items = [{"uuid": str(self.rows[0].uuid), "model_ref": None}]
        self.assertEqual(self.post(assistant_models=items, preview=False).data["count"], 1)
        self.rows[0].refresh_from_db()
        self.assertEqual(self.rows[0].settings["custom"], "keep")
        items[0]["model_ref"] = str(self.target.uuid)
        self.assertEqual(self.post(assistant_models=items, preview=False).data["count"], 1)

    def test_smart_assistants_cannot_receive_multimodal_models(self):
        """Reject unused vision assignments on collaboration coordinators."""

        Assistant.objects.filter(pk=self.rows[0].pk).update(routing_mode=Assistant.RoutingMode.SMART)
        items = [{"uuid": str(self.rows[0].uuid), "model_ref": None}]
        with patch("lens.model_replacement.validate_vision_model_ref", return_value=None):
            response = self.post(model_field="multimodal_model_ref", assistant_models=items, preview=False)
        self.assertEqual(response.status_code, 400)

    def test_batch_size_is_bounded(self):
        """Prevent oversized requests before querying any assistant."""

        items = [{"uuid": str(uuid.uuid4()), "model_ref": None} for _ in range(1001)]
        self.assertEqual(self.post(assistant_models=items).status_code, 400)

    def test_shared_model_checks_do_not_query_for_every_assistant(self):
        """Validate shared references once when replacing a larger batch."""

        rows = [
            Assistant.objects.create(name=f"Batch {index}", slug=f"batch-{index}", agent_model_ref=self.old.uuid)
            for index in range(20)
        ]
        items = [{"uuid": str(row.uuid), "model_ref": str(self.old.uuid)} for row in rows]
        with CaptureQueriesContext(connection) as queries:
            response = self.post(assistant_models=items, preview=False)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 20)
        model_queries = [query for query in queries if LLMConfig._meta.db_table in query["sql"]]
        self.assertLessEqual(len(model_queries), 3)

    def test_admin_feature_is_required_even_for_preview(self):
        """Authenticated ordinary users cannot preview or modify the batch."""

        user = get_user_model().objects.create_user(username="model-reader")
        self.client.force_authenticate(user)
        self.assertEqual(self.post().status_code, 403)
        self.assertEqual(self.post(preview=False).status_code, 403)
        self.client.force_authenticate(None)
        self.assertEqual(self.post().status_code, 401)

    def test_list_exposes_model_refs_for_filtering(self):
        """The compact assistant list includes both replacement slots."""

        response = self.client.get("/api/lens/assistants/")
        rows = response.data.get("results", response.data) if isinstance(response.data, dict) else response.data
        self.assertEqual(rows[0]["agent_model_ref"], str(self.old.uuid))
        self.assertIsNone(rows[0]["multimodal_model_ref"])
