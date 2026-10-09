"""Regression tests for Lens-owned database migrations."""

from importlib import import_module
from types import SimpleNamespace
from unittest.mock import Mock

from django.apps import apps
from django.db import connection
from django.test import SimpleTestCase, TestCase

from lens.models import (
    Assistant,
    AssistantMCP,
    AssistantSkill,
    EnvironmentVariableSet,
    MCPServer,
    Skill,
)


class TaskExecutionDatasourceIndexMigrationTests(SimpleTestCase):
    """Verify the cross-package datasource history index contract."""

    def setUp(self):
        self.migration = import_module(
            "lens.migrations.0050_taskexecution_datasource_history_index"
        )

    def test_postgresql_index_matches_the_jsonb_history_query(self):
        schema_editor = SimpleNamespace(
            connection=SimpleNamespace(
                vendor="postgresql",
                settings_dict={"TEST": {"NAME": ""}},
                in_atomic_block=False,
            ),
            execute=Mock(),
        )

        self.migration.create_datasource_history_index(None, schema_editor)

        sql = " ".join(schema_editor.execute.call_args.args[0].split())
        self.assertIn("CREATE INDEX CONCURRENTLY IF NOT EXISTS", sql)
        self.assertIn("ON agentcore_task_execution", sql)
        self.assertIn("metadata -> 'datasource_uuid'", sql)
        self.assertIn("created_at DESC", sql)
        self.assertFalse(self.migration.Migration.atomic)

    def test_non_postgresql_database_skips_the_expression_index(self):
        schema_editor = SimpleNamespace(
            connection=SimpleNamespace(
                vendor="sqlite",
                settings_dict={"TEST": {"NAME": ""}},
                in_atomic_block=False,
            ),
            execute=Mock(),
        )

        self.migration.create_datasource_history_index(None, schema_editor)
        self.migration.drop_datasource_history_index(None, schema_editor)

        schema_editor.execute.assert_not_called()


class EnvironmentSecretMigrationTests(TestCase):
    """Protect existing declarations with an additive schema change."""

    def test_backfill_preserves_values_and_combines_resource_declarations(
        self,
    ):
        migration = import_module(
            "lens.migrations.0064_environmentvariableset_secret_keys"
        )
        variable_set = EnvironmentVariableSet.objects.create(
            name="Migration set"
        )
        variable_set.set_values(
            {"MCP_ACCESS": "fake-token", "SKILL_ACCESS": "fake-key"}
        )
        variable_set.save(update_fields=["encrypted_values"])
        encrypted = variable_set.encrypted_values
        assistant = Assistant.objects.create(
            name="Migration", slug="migration"
        )
        mcp = MCPServer.objects.create(
            name="Migration MCP",
            transport="url",
            endpoint="https://example.invalid/mcp",
            environment=[{"name": "MCP_ACCESS", "secret": True}],
        )
        skill = Skill.objects.create(
            name="Migration Skill",
            definition={
                "environment": [{"name": "SKILL_ACCESS", "secret": True}]
            },
        )
        AssistantMCP.objects.create(
            assistant=assistant,
            mcp=mcp,
            environment_variable_set=variable_set,
        )
        AssistantSkill.objects.create(
            assistant=assistant,
            skill=skill,
            environment_variable_set=variable_set,
        )
        EnvironmentVariableSet.objects.filter(pk=variable_set.pk).update(
            secret_keys=[]
        )
        with connection.schema_editor() as schema_editor:
            migration.preserve_secret_declarations(apps, schema_editor)
        variable_set.refresh_from_db()
        self.assertEqual(variable_set.encrypted_values, encrypted)
        self.assertEqual(
            set(variable_set.secret_keys), {"MCP_ACCESS", "SKILL_ACCESS"}
        )
        self.assertEqual(assistant.mcp_bindings.count(), 1)
        self.assertEqual(assistant.skill_bindings.count(), 1)

    def test_secret_keys_column_retains_a_database_default_for_old_code(self):
        migration = import_module(
            "lens.migrations.0064_environmentvariableset_secret_keys"
        )
        field = migration.Migration.operations[0].field
        self.assertEqual(field.db_default, [])
        self.assertIs(field.default, list)
