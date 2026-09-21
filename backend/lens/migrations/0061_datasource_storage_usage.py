from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("lens", "0060_connection_history_set_null"),
    ]

    operations = [
        migrations.AddField(
            model_name="datasource",
            name="storage_usage",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
