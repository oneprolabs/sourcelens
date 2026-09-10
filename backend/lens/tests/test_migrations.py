"""Regression tests for Lens-owned database migrations."""

from importlib import import_module
from types import SimpleNamespace
from unittest.mock import Mock

from django.test import SimpleTestCase


class TaskExecutionDatasourceIndexMigrationTests(SimpleTestCase):
    """Verify the cross-package datasource history index contract."""

    def setUp(self):
        self.migration = import_module(
            "lens.migrations.0050_taskexecution_datasource_history_index"
        )

    def test_postgresql_index_matches_the_jsonb_history_query(self):
        schema_editor = SimpleNamespace(
            connection=SimpleNamespace(vendor="postgresql"),
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
            connection=SimpleNamespace(vendor="sqlite"),
            execute=Mock(),
        )

        self.migration.create_datasource_history_index(None, schema_editor)
        self.migration.drop_datasource_history_index(None, schema_editor)

        schema_editor.execute.assert_not_called()
