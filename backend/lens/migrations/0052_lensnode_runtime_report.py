from django.db import migrations, models


class Migration(migrations.Migration):
    """Persist runtime metrics and active datasource operations per node."""

    dependencies = [
        ("lens", "0051_datasource_items_session_snapshots"),
    ]

    operations = [
        migrations.AddField(
            model_name="lensnode",
            name="last_metrics",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="lensnode",
            name="active_datasource_operations",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
