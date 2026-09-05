import json
import tempfile
from contextlib import contextmanager
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from lens.lensnode_auth import issue_lensnode_token
from lens.models import (
    Assistant,
    AssistantPluginBinding,
    Connection,
    ExecutionSnapshot,
    LensNode,
    PluginInvocation,
    Run,
    SecretMaterial,
    SecretVersion,
    Session,
)
from lens.services import create_execution_run
from rest_framework.test import APIClient


class PluginToolSnapshotTests(TestCase):
    """Verify Run-bound snapshots for model Plugin tool calls."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="plugin-tool-user",
        )
        self.node = LensNode.objects.create(
            name="Tool node",
            status=LensNode.Status.ONLINE,
            enrollment_status=LensNode.EnrollmentStatus.APPROVED,
            tasks=[{"name": "general_chat"}],
        )
        self.token = issue_lensnode_token(self.node)
        material = SecretMaterial.objects.create(name="GitHub PAT")
        version = SecretVersion(material=material)
        version.set_value("github-tool-secret")
        version.save()
        self.connection = Connection.objects.create(
            name="GitHub readonly",
            plugin_key="github",
            endpoint="https://github.com",
            allowed_scope={"repositories": ["HyperBDR/sourcelens"]},
            secret_version=version,
        )
        self.assistant = Assistant.objects.create(
            name="GitHub Assistant",
            slug="github-tool-assistant",
            lensnode=self.node,
            selected_task="general_chat",
            visibility=Assistant.Visibility.PUBLIC,
        )
        AssistantPluginBinding.objects.create(
            assistant=self.assistant,
            connection=self.connection,
            tools=["github_read_file", "github_search_code"],
        )
        self.session = Session.objects.create(
            assistant=self.assistant,
            user=self.user,
        )
        self.client = APIClient()

    @contextmanager
    def plugin_root(self):
        """Install one trusted GitHub Tool Provider manifest for a test."""

        manifest = {
            "key": "github",
            "version": "1.0.0",
            "protocol_version": 1,
            "handlers": {
                "runtime": "python_v1",
                "datasource": "python_v1",
            },
            "tools": [
                {
                    "key": "github_read_file",
                    "description": "Read one authorized repository file.",
                    "capability": "repository.read",
                    "side_effect": "none",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "repository": {"type": "string"},
                            "path": {"type": "string"},
                            "ref": {"type": "string"},
                        },
                        "required": ["repository", "path"],
                    },
                },
                {
                    "key": "github_search_code",
                    "description": "Search one authorized repository.",
                    "capability": "repository.read",
                    "side_effect": "none",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "repository": {"type": "string"},
                            "query": {"type": "string"},
                            "path": {"type": "string"},
                            "max_results": {"type": "integer"},
                        },
                        "required": ["repository", "query"],
                    },
                },
            ],
        }
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "github"
            path.mkdir(parents=True)
            (path / "plugin.json").write_text(json.dumps(manifest))
            (path / "control.py").write_text("PLUGIN_API_VERSION = 1\n")
            (path / "runtime.py").write_text("PLUGIN_API_VERSION = 1\n")
            with override_settings(LENS_PLUGIN_ROOTS=[root]):
                yield

    def _create_active_run(self):
        with self.plugin_root():
            run = create_execution_run(
                self.session,
                "Read the README",
                enqueue=False,
            )
        run.status = Run.Status.STREAMING
        run.save(update_fields=["status"])
        return run

    def _create_snapshot(self, run, **overrides):
        payload = {
            "run_uuid": str(run.uuid),
            "connection_uuid": str(self.connection.uuid),
            "tool_key": "github_read_file",
            "call_id": "tool-call-1",
            "arguments": {
                "repository": "HyperBDR/sourcelens",
                "path": "README.md",
                "ref": "main",
            },
        }
        payload.update(overrides)
        return self.client.post(
            "/api/lens/plugin-runtime/tool-snapshots/",
            payload,
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )

    def test_node_creates_run_bound_tool_snapshot_without_secret(self):
        run = self._create_active_run()

        response = self._create_snapshot(run)

        self.assertEqual(response.status_code, 201, response.data)
        snapshot = ExecutionSnapshot.objects.get(
            uuid=response.data["snapshot_uuid"]
        )
        self.assertEqual(snapshot.kind, ExecutionSnapshot.Kind.TOOL_INVOKE)
        self.assertEqual(snapshot.run, run)
        self.assertIsNone(snapshot.datasource)
        self.assertEqual(snapshot.tool_key, "github_read_file")
        self.assertEqual(snapshot.plugin_version, "1.0.0")
        self.assertEqual(response.data["plugin_version"], "1.0.0")
        self.assertEqual(snapshot.invocation_id, "tool-call-1")
        self.assertEqual(
            snapshot.resolved_config["arguments"]["path"],
            "README.md",
        )
        self.assertNotIn("github-tool-secret", json.dumps(response.data))
        self.assertNotIn(
            "github-tool-secret",
            json.dumps(snapshot.resolved_config),
        )
        invocation = PluginInvocation.objects.get(snapshot=snapshot)
        self.assertEqual(invocation.status, PluginInvocation.Status.AUTHORIZED)
        self.assertEqual(invocation.actor, self.user)
        self.assertEqual(invocation.lensnode, self.node)
        self.assertEqual(
            invocation.resource_summary,
            {"repository": "HyperBDR/sourcelens"},
        )
        self.assertNotIn("README.md", json.dumps(invocation.resource_summary))
        self.assertNotIn("github-tool-secret", str(invocation.__dict__))

    def test_tool_snapshot_rejects_connection_not_frozen_in_run(self):
        run = self._create_active_run()
        other = Connection.objects.create(
            name="Other GitHub",
            plugin_key="github",
            endpoint="https://github.com",
            allowed_scope={"repositories": ["HyperBDR/sourcelens"]},
            secret_version=self.connection.secret_version,
        )

        response = self._create_snapshot(
            run,
            connection_uuid=str(other.uuid),
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["detail"], "TOOL_NOT_AUTHORIZED")

    def test_material_exchange_marks_invocation_audit_materialized(self):
        run = self._create_active_run()
        snapshot_response = self._create_snapshot(run)
        lease_response = self.client.post(
            "/api/lens/plugin-runtime/leases/",
            {"snapshot_uuid": snapshot_response.data["snapshot_uuid"]},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )

        material_response = self.client.post(
            "/api/lens/plugin-runtime/leases/"
            f"{lease_response.data['lease_uuid']}/material/",
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )

        self.assertEqual(material_response.status_code, 200)
        invocation = PluginInvocation.objects.get(
            snapshot__uuid=snapshot_response.data["snapshot_uuid"]
        )
        self.assertEqual(
            invocation.status,
            PluginInvocation.Status.MATERIALIZED,
        )
        self.assertIsNotNone(invocation.materialized_at)

    def test_admin_can_list_secret_free_plugin_invocation_audit(self):
        run = self._create_active_run()
        self._create_snapshot(run)
        admin = get_user_model().objects.create_user(
            username="plugin-audit-admin",
            is_staff=True,
        )
        admin_client = APIClient()
        admin_client.force_authenticate(admin)

        response = admin_client.get(
            "/api/lens/admin/plugin-invocations/?plugin_key=github"
        )

        self.assertEqual(response.status_code, 200)
        row = response.data["results"][0]
        self.assertEqual(row["plugin_key"], "github")
        self.assertEqual(row["tool_key"], "github_read_file")
        self.assertEqual(row["capability"], "repository.read")
        self.assertNotIn("resolved_config", row)
        self.assertNotIn("secret_version", row)
        self.assertNotIn("github-tool-secret", json.dumps(response.data))

    def test_tool_snapshot_rejects_tool_not_frozen_in_run(self):
        run = self._create_active_run()

        response = self._create_snapshot(
            run,
            tool_key="github_delete_repository",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["detail"], "TOOL_NOT_AUTHORIZED")

    def test_tool_snapshot_rejects_repository_outside_connection_scope(self):
        run = self._create_active_run()

        response = self._create_snapshot(
            run,
            arguments={
                "repository": "other/repository",
                "path": "README.md",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], "TOOL_ARGUMENTS_INVALID")

    def test_tool_snapshot_accepts_repository_identity_case_insensitively(
        self,
    ):
        run = self._create_active_run()

        response = self._create_snapshot(
            run,
            arguments={
                "repository": "hyperbdr/sourcelens",
                "path": "README.md",
            },
        )

        self.assertEqual(response.status_code, 201, response.data)
        snapshot = ExecutionSnapshot.objects.get(
            uuid=response.data["snapshot_uuid"]
        )
        self.assertEqual(
            snapshot.resolved_config["arguments"]["repository"],
            "hyperbdr/sourcelens",
        )

    def test_search_tool_rejects_scope_qualifiers_in_model_query(self):
        run = self._create_active_run()

        response = self._create_snapshot(
            run,
            tool_key="github_search_code",
            arguments={
                "repository": "HyperBDR/sourcelens",
                "query": "token repo:other/private-repository",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], "TOOL_ARGUMENTS_INVALID")

    def test_search_tool_rejects_splittable_path_qualifier(self):
        run = self._create_active_run()

        response = self._create_snapshot(
            run,
            tool_key="github_search_code",
            arguments={
                "repository": "HyperBDR/sourcelens",
                "query": "token",
                "path": "src repo:other/private-repository",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], "TOOL_ARGUMENTS_INVALID")

    def test_search_tool_rejects_unsupported_ref_argument(self):
        run = self._create_active_run()

        response = self._create_snapshot(
            run,
            tool_key="github_search_code",
            arguments={
                "repository": "HyperBDR/sourcelens",
                "query": "token",
                "ref": "main",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], "TOOL_ARGUMENTS_INVALID")

    def test_tool_snapshot_rejects_run_owned_by_another_node(self):
        run = self._create_active_run()
        other = LensNode.objects.create(
            name="Other node",
            status=LensNode.Status.ONLINE,
            enrollment_status=LensNode.EnrollmentStatus.APPROVED,
            tasks=[{"name": "general_chat"}],
        )
        run.lensnode = other
        run.save(update_fields=["lensnode"])

        response = self._create_snapshot(run)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["detail"], "RUN_NODE_MISMATCH")

    def test_tool_snapshot_rejects_terminal_run(self):
        run = self._create_active_run()
        run.status = Run.Status.DONE
        run.save(update_fields=["status"])

        response = self._create_snapshot(run)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["detail"], "RUN_NOT_ACTIVE")

    def test_tool_snapshot_rejects_malformed_uuid(self):
        run = self._create_active_run()

        response = self._create_snapshot(run, run_uuid="not-a-uuid")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], "TOOL_REQUEST_INVALID")
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_tool_snapshot_rejects_non_object_payload(self):
        response = self.client.post(
            "/api/lens/plugin-runtime/tool-snapshots/",
            [],
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], "TOOL_REQUEST_INVALID")

    def test_tool_snapshot_is_idempotent_for_same_call(self):
        run = self._create_active_run()

        first = self._create_snapshot(run)
        second = self._create_snapshot(run)

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(
            first.data["snapshot_uuid"],
            second.data["snapshot_uuid"],
        )
        self.assertEqual(
            ExecutionSnapshot.objects.filter(
                run=run,
                invocation_id="tool-call-1",
            ).count(),
            1,
        )

    def test_tool_snapshot_rejects_reused_call_id_with_new_arguments(self):
        run = self._create_active_run()
        first = self._create_snapshot(run)

        second = self._create_snapshot(
            run,
            arguments={
                "repository": "HyperBDR/sourcelens",
                "path": "README.zh-CN.md",
            },
        )

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.data["detail"], "TOOL_CALL_CONFLICT")

    def test_tool_snapshot_uses_existing_lease_and_material_flow(self):
        run = self._create_active_run()
        snapshot_response = self._create_snapshot(run)
        snapshot_uuid = snapshot_response.data["snapshot_uuid"]

        config_response = self.client.get(
            f"/api/lens/plugin-runtime/snapshots/{snapshot_uuid}/",
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )
        lease_response = self.client.post(
            "/api/lens/plugin-runtime/leases/",
            {"snapshot_uuid": snapshot_uuid},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )
        material_response = self.client.post(
            "/api/lens/plugin-runtime/leases/"
            f"{lease_response.data['lease_uuid']}/material/",
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )

        self.assertEqual(config_response.status_code, 200)
        self.assertEqual(config_response.data["run_uuid"], str(run.uuid))
        self.assertEqual(
            config_response.data["connection_uuid"],
            str(self.connection.uuid),
        )
        self.assertEqual(config_response.data["tool_key"], "github_read_file")
        self.assertEqual(lease_response.status_code, 201)
        self.assertEqual(material_response.status_code, 200)
        self.assertEqual(material_response.data["value"], "github-tool-secret")

    def test_terminal_run_revokes_tool_material_access(self):
        run = self._create_active_run()
        snapshot_response = self._create_snapshot(run)
        lease_response = self.client.post(
            "/api/lens/plugin-runtime/leases/",
            {"snapshot_uuid": snapshot_response.data["snapshot_uuid"]},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )
        run.status = Run.Status.DONE
        run.save(update_fields=["status"])

        material_response = self.client.post(
            "/api/lens/plugin-runtime/leases/"
            f"{lease_response.data['lease_uuid']}/material/",
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )

        self.assertEqual(material_response.status_code, 409)
        self.assertEqual(material_response.data["detail"], "RUN_NOT_ACTIVE")

    def test_terminal_run_cannot_issue_tool_lease(self):
        run = self._create_active_run()
        snapshot_response = self._create_snapshot(run)
        run.status = Run.Status.DONE
        run.save(update_fields=["status"])

        lease_response = self.client.post(
            "/api/lens/plugin-runtime/leases/",
            {"snapshot_uuid": snapshot_response.data["snapshot_uuid"]},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )

        self.assertEqual(lease_response.status_code, 409)
        self.assertEqual(lease_response.data["detail"], "RUN_NOT_ACTIVE")

    def test_disabled_material_cannot_issue_tool_lease(self):
        run = self._create_active_run()
        snapshot_response = self._create_snapshot(run)
        self.connection.secret_version.material.status = "disabled"
        self.connection.secret_version.material.save(update_fields=["status"])

        lease_response = self.client.post(
            "/api/lens/plugin-runtime/leases/",
            {"snapshot_uuid": snapshot_response.data["snapshot_uuid"]},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )

        self.assertEqual(lease_response.status_code, 409)
        self.assertEqual(
            lease_response.data["detail"],
            "SECRET_MATERIAL_DISABLED",
        )
