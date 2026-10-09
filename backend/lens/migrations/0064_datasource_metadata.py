from django.db import migrations, models


class Migration(migrations.Migration):
    """Store system-generated datasource context without changing user configuration."""

    dependencies = [("lens", "0063_assistantpluginbinding_decision_gates")]

    operations = [
        migrations.AddField(
            model_name="datasource",
            name="metadata",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
