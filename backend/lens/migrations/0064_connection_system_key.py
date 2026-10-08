"""Identify reusable system-owned anonymous repository connections."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("lens", "0063_assistantpluginbinding_decision_gates")]

    operations = [
        migrations.AddField(
            model_name="connection",
            name="system_key",
            field=models.CharField(blank=True, editable=False, max_length=80, null=True, unique=True),
        ),
    ]
