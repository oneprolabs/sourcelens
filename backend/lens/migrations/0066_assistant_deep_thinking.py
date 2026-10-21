from django.db import migrations, models


def preserve_deep_thinking(apps, schema_editor):
    """Keep stronger thinking enabled for assistants previously using deep tiers."""

    apps.get_model("lens", "Assistant").objects.using(schema_editor.connection.alias).filter(
        agent_rounds__in=["deep", "max"],
    ).update(deep_thinking=True)


class Migration(migrations.Migration):
    dependencies = [("lens", "0065_alert_rules")]

    operations = [
        migrations.AddField(
            model_name="assistant",
            name="deep_thinking",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(preserve_deep_thinking, migrations.RunPython.noop),
    ]
