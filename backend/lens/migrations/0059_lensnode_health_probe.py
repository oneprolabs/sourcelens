from django.db import migrations, models


class Migration(migrations.Migration):
    """Persist command-channel health probes and the unresponsive status."""

    dependencies = [
        ("lens", "0058_sessiondatasource_required"),
    ]

    operations = [
        migrations.AddField(
            model_name="lensnode",
            name="last_health_probe_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="lensnode",
            name="last_health_probe_success_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="lensnode",
            name="status",
            field=models.CharField(
                choices=[
                    ("online", "Online"),
                    ("unresponsive", "Unresponsive"),
                    ("offline", "Offline"),
                    ("draining", "Draining"),
                ],
                default="offline",
                max_length=16,
            ),
        ),
    ]
