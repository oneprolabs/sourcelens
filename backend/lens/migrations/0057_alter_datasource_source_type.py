from django.db import migrations, models


class Migration(migrations.Migration):
    """Record the manual upload datasource source type choices."""

    dependencies = [
        ("lens", "0056_datasource_upload_source_type"),
    ]

    operations = [
        migrations.AlterField(
            model_name="datasource",
            name="source_type",
            field=models.CharField(
                choices=[
                    ("git", "Git"),
                    ("feishu", "Feishu"),
                    ("jira", "Jira"),
                    ("managed_workspace", "Managed Workspace"),
                    ("upload", "Manual Upload"),
                ],
                max_length=32,
            ),
        ),
    ]
