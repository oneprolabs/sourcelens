from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("lens", "0052_lensnode_runtime_report")]

    operations = [
        migrations.AddField(
            model_name="datasourceitem",
            name="storage_usage",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
