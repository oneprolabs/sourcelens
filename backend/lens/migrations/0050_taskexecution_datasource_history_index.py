"""Index datasource task history stored in agentcore metadata."""

from django.db import migrations

INDEX_NAME = "agentcore_ds_uuid_created_idx"
TABLE_NAME = "agentcore_task_execution"


def create_datasource_history_index(apps, schema_editor):
    """Create the PostgreSQL JSONB expression index without blocking writes."""

    del apps
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(f"""
        CREATE INDEX CONCURRENTLY IF NOT EXISTS {INDEX_NAME}
        ON {TABLE_NAME}
        (module, ((metadata -> 'datasource_uuid')), created_at DESC)
        """)


def drop_datasource_history_index(apps, schema_editor):
    """Drop the datasource history index without blocking writes."""

    del apps
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX_NAME}")


class Migration(migrations.Migration):
    """Add an index owned by Lens to the packaged task execution table."""

    atomic = False

    dependencies = [
        ("lens", "0049_remove_plugin_release"),
    ]

    operations = [
        migrations.RunPython(
            create_datasource_history_index,
            drop_datasource_history_index,
            atomic=False,
        ),
    ]
