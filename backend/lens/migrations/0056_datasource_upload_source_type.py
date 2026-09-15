from django.db import migrations


def split_upload_source_type(apps, schema_editor):
    """Move file upload datasources onto their dedicated source type."""

    DataSource = apps.get_model("lens", "DataSource")
    rows = DataSource.objects.filter(
        source_type="managed_workspace",
        plugin_key="file_upload",
    ).select_related("lensnode")
    for datasource in rows:
        update_fields = ["source_type"]
        datasource.source_type = "upload"
        workspace_path = ""
        if datasource.lensnode_id:
            workspace_path = str(
                datasource.lensnode.workspace_path or ""
            ).strip().rstrip("/")
        if workspace_path:
            datasource.target_path = (
                f"{workspace_path}/datasources/{datasource.uuid}"
            )
            update_fields.append("target_path")
        datasource.save(update_fields=update_fields)


def restore_managed_workspace_source_type(apps, schema_editor):
    """Move upload datasources back onto the managed workspace type."""

    DataSource = apps.get_model("lens", "DataSource")
    DataSource.objects.filter(
        source_type="upload",
        plugin_key="file_upload",
    ).update(source_type="managed_workspace")


class Migration(migrations.Migration):
    dependencies = [
        ("lens", "0055_sessioncleanupoperation"),
    ]

    operations = [
        migrations.RunPython(
            split_upload_source_type,
            restore_managed_workspace_source_type,
        ),
    ]
