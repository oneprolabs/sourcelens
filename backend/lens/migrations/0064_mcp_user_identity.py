from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("lens", "0063_assistantpluginbinding_decision_gates")]

    operations = [
        migrations.AddField(
            model_name="mcpserver",
            name="pass_user_identity",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="mcpserver",
            name="user_identity_audience",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
    ]
