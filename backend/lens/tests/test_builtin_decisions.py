"""Authorization, metering, snapshots and legacy decision configuration migration."""

import importlib
from types import SimpleNamespace
from unittest.mock import patch

from agentcore_metering.adapters.django.models import LLMConfig
from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import serializers
from rest_framework.test import APIClient

from lens.decisions import build_assistant_decision_model, validate_assistant_decisions
from lens.lensnode_auth import hash_lensnode_token
from lens.models import (
    Assistant,
    AssistantPluginBinding,
    Connection,
    LensNode,
    Message,
    Run,
    RunExecution,
    SecretMaterial,
    SecretVersion,
    Session,
)


class BuiltinDecisionTests(TestCase):
    """Decisions spend only the model authorized in a frozen Run."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="decision-admin", is_staff=True)
        self.model = LLMConfig.objects.create(
            scope="global",
            model_type="decision",
            provider="typesafe",
            config={"model": "jev-latest", "api_key": "test-only-secret"},
        )
        self.assistant = Assistant.objects.create(
            name="Decision assistant",
            slug="decision-assistant",
            capability="general_chat",
            decision_model_ref=self.model.uuid,
        )
        self.node = LensNode.objects.create(
            name="Decision node", enrollment_status="approved", auth_token_hash=hash_lensnode_token("test-node-token")
        )
        self.session = Session.objects.create(user=self.user, assistant=self.assistant)
        self.message = Message.objects.create(session=self.session, role="user", sequence=1, content="Question")
        self.run = Run.objects.create(
            session=self.session, lensnode=self.node, input_message=self.message, status="running"
        )
        self.execution = RunExecution.objects.create(
            run=self.run,
            lensnode=self.node,
            task="general_chat",
            token_budget_max_tokens=0,
            runtime_snapshot={"decision_model": build_assistant_decision_model(self.assistant)},
        )
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION="Bearer test-node-token")
        self.payload = {
            "run_uuid": str(self.run.uuid),
            "model_ref": str(self.model.uuid),
            "input": "Evidence",
            "questions": [{"type": "predicate", "name": "evidence_sufficient", "instructions": "Is it sufficient?"}],
            "source": "decision_gate",
            "call_id": "gate:evidence_sufficient:1",
        }

    def test_authorized_decision_is_metered_with_run_correlation(self):
        with patch(
            "lens.views.decisions.DecisionTracker.call_with_config",
            return_value=(
                [{"type": "predicate", "name": "evidence_sufficient", "probability": 0.8}],
                {"total_tokens": 3},
            ),
        ) as tracker:
            response = self.client.post("/api/lens/lensnode/decision-gateway/", self.payload, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.json()["usage"]["total_tokens"], 3)
        self.assertEqual(response.json()["answers"][0]["probability"], 0.8)
        kwargs = tracker.call_args.kwargs
        self.assertEqual(kwargs["config_uuid"], str(self.model.uuid))
        self.assertEqual(kwargs["state"]["metadata"]["run_uuid"], str(self.run.uuid))
        self.assertNotIn("input", kwargs["state"]["metadata"])
        self.assertNotIn("test-only-secret", str(self.execution.runtime_snapshot))

    def test_gateway_rejects_other_models_before_spending(self):
        self.payload["model_ref"] = "00000000-0000-0000-0000-000000000001"
        with patch("lens.views.decisions.DecisionTracker.call_with_config") as tracker:
            response = self.client.post("/api/lens/lensnode/decision-gateway/", self.payload, format="json")
        self.assertEqual(response.status_code, 403)
        tracker.assert_not_called()

    def test_gateway_rejects_another_nodes_run(self):
        self.run.lensnode = LensNode.objects.create(name="Other node")
        self.run.save(update_fields=["lensnode"])
        response = self.client.post("/api/lens/lensnode/decision-gateway/", self.payload, format="json")
        self.assertEqual(response.status_code, 404)

    def test_gateway_rejects_undeclared_decisions(self):
        self.payload["questions"][0]["name"] = "unconfigured"
        response = self.client.post("/api/lens/lensnode/decision-gateway/", self.payload, format="json")
        self.assertEqual(response.status_code, 403)

    def test_gateway_rejects_completed_runs(self):
        self.run.status = "done"
        self.run.save(update_fields=["status"])
        with patch("lens.views.decisions.DecisionTracker.call_with_config") as tracker:
            response = self.client.post("/api/lens/lensnode/decision-gateway/", self.payload, format="json")
        self.assertEqual(response.status_code, 409)
        tracker.assert_not_called()

    def test_gateway_rejects_an_exhausted_parent_budget_before_spending(self):
        with (
            patch("lens.views.decisions.LensNodeAIGatewayView._acquire_parent_budget_lock", return_value="exhausted"),
            patch("lens.views.decisions.DecisionTracker.call_with_config") as tracker,
        ):
            response = self.client.post("/api/lens/lensnode/decision-gateway/", self.payload, format="json")
        self.assertEqual(response.status_code, 429)
        tracker.assert_not_called()

    def test_gateway_requires_node_authentication(self):
        self.client.credentials()
        response = self.client.post("/api/lens/lensnode/decision-gateway/", self.payload, format="json")
        self.assertEqual(response.status_code, 401)

    def test_frozen_model_does_not_follow_later_assistant_changes(self):
        self.assistant.decision_model_ref = None
        self.assistant.save(update_fields=["decision_model_ref"])
        self.execution.refresh_from_db()
        self.assertEqual(self.execution.runtime_snapshot["decision_model"]["model_ref"], str(self.model.uuid))

    def test_unconfigured_assistant_has_no_decision_policy_or_gateway_spending(self):
        from lens.services import _build_run_runtime_snapshot

        self.assistant.decision_model_ref = None
        self.assistant.decision_analyses = {"plan_quality": {}}
        snapshot = _build_run_runtime_snapshot(self.assistant, self.node, "en", self.session)
        self.assertEqual(snapshot["decision_model"], {})
        self.assertEqual(snapshot["model_refs"]["decision"], "")
        self.execution.runtime_snapshot = snapshot
        self.execution.save(update_fields=["runtime_snapshot"])
        with patch("lens.views.decisions.DecisionTracker.call_with_config") as tracker:
            response = self.client.post("/api/lens/lensnode/decision-gateway/", self.payload, format="json")
        self.assertEqual(response.status_code, 403)
        tracker.assert_not_called()

    def test_validates_model_type_scope_and_active_state(self):
        for change in ({"model_type": "llm"}, {"scope": "user", "user": self.user}, {"is_active": False}):
            with self.subTest(change=change):
                row = LLMConfig.objects.create(
                    provider="typesafe", config={"model": "jev-latest"}, scope="global", model_type="decision"
                )
                for key, value in change.items():
                    setattr(row, key, value)
                row.save()
                with self.assertRaises(serializers.ValidationError):
                    validate_assistant_decisions({"decision_model_ref": row.uuid}, None, "general_chat")

    def test_attaching_a_model_normalizes_preexisting_manual_gate_settings(self):
        self.assistant.capability = "knowledge_qa"
        self.assistant.decision_model_ref = None
        self.assistant.decision_auto = False
        self.assistant.decision_gates = {"search_needed": {}}
        attrs = {"decision_model_ref": self.model.uuid}

        validate_assistant_decisions(attrs, self.assistant, self.assistant.capability)
        for field, value in attrs.items():
            setattr(self.assistant, field, value)
        gate = build_assistant_decision_model(self.assistant)["gates"]["search_needed"]

        self.assertEqual(gate["threshold"], 0.5)
        self.assertEqual(gate["margin"], 0.1)
        self.assertEqual(gate["max_state_chars"], 4000)

    def test_clearing_a_migrated_model_does_not_reactivate_legacy_decisions(self):
        from lens.serializers import AssistantSerializer
        from lens.services import build_loaded_plugins

        connection = Connection.objects.create(name="Legacy decisions", plugin_key="typesafe")
        binding = AssistantPluginBinding.objects.create(assistant=self.assistant, connection=connection)
        assistant = AssistantSerializer().update(self.assistant, {"decision_model_ref": None})
        binding.refresh_from_db()
        self.assertTrue(binding.enabled)
        self.assertEqual(build_assistant_decision_model(assistant), {})
        self.assertEqual(build_loaded_plugins(assistant), [])

    def test_typesafe_is_retired_from_plugin_and_connection_management(self):
        self.client.force_authenticate(self.user)
        connection = Connection.objects.create(name="Retired TypeSafe", plugin_key="typesafe")
        plugins = self.client.get("/api/lens/admin/plugins/")
        self.assertEqual(plugins.status_code, 200)
        self.assertNotIn("typesafe", [item["key"] for item in plugins.data])
        connections = self.client.get("/api/lens/admin/connections/")
        self.assertEqual(connections.status_code, 200)
        rows = connections.data.get("results", []) if isinstance(connections.data, dict) else connections.data
        self.assertNotIn(str(connection.uuid), [str(item["uuid"]) for item in rows])
        response = self.client.post(
            "/api/lens/admin/connections/", {"name": "New TypeSafe", "plugin_key": "typesafe"}, format="json"
        )
        self.assertEqual(response.status_code, 400)

    def test_auto_gates_apply_to_the_assistant_capability(self):
        frozen = build_assistant_decision_model(self.assistant)
        self.assertIn("evidence_requirement", frozen["gates"])
        self.assertNotIn("search_needed", frozen["gates"])
        self.assistant.capability = "knowledge_qa"
        frozen = build_assistant_decision_model(self.assistant)
        self.assertIn("search_needed", frozen["gates"])
        self.assertNotIn("evidence_requirement", frozen["gates"])

    def test_legacy_migration_is_idempotent_and_keeps_bindings_for_old_color(self):
        self.assistant.decision_model_ref = None
        self.assistant.save(update_fields=["decision_model_ref"])
        material = SecretMaterial.objects.create(name="Legacy secret")
        version = SecretVersion(material=material)
        version.set_value("legacy-test-secret")
        version.save()
        connection = Connection.objects.create(
            name="Legacy TypeSafe",
            plugin_key="typesafe",
            endpoint="https://api.typesafe.ai",
            config={"model": "jev-1.13.0"},
            secret_version=version,
        )
        binding = AssistantPluginBinding.objects.create(
            assistant=self.assistant,
            connection=connection,
            decision_auto=False,
            decision_gates={"evidence_sufficient": {"threshold": 0.8, "margin": 0.1, "max_state_chars": 4000}},
            decision_analyses={"plan_quality": {}},
        )
        migration = importlib.import_module("lens.migrations.0064_builtin_decisions")
        from django.db import connection as database_connection

        editor = SimpleNamespace(connection=database_connection)
        migration.migrate_typesafe_bindings(apps, editor)
        migration.migrate_typesafe_bindings(apps, editor)
        self.assistant.refresh_from_db()
        row = LLMConfig.objects.get(uuid=self.assistant.decision_model_ref)
        self.assertEqual(row.config["model"], "jev-1.13")
        self.assertEqual(row.config["api_key"], "legacy-test-secret")
        self.assertFalse(self.assistant.decision_auto)
        self.assertEqual(self.assistant.decision_gates, binding.decision_gates)
        self.assertEqual(self.assistant.decision_analyses, binding.decision_analyses)
        self.assertTrue(AssistantPluginBinding.objects.get(pk=binding.pk).enabled)
        self.assertEqual(LLMConfig.objects.filter(uuid=row.uuid).count(), 1)
