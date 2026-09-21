from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("lens", "0061_datasource_storage_usage"),
    ]

    operations = [
        migrations.AlterField(
            model_name="session",
            name="allowed_assistant_uuids",
            field=models.JSONField(blank=True, db_default=[], default=list),
        ),
        migrations.AlterField(
            model_name="session",
            name="routing_mode",
            field=models.CharField(
                choices=[
                    ("direct", "Direct"),
                    ("smart", "Smart Collaboration"),
                ],
                db_default="direct",
                default="direct",
                max_length=16,
            ),
        ),
    ]
