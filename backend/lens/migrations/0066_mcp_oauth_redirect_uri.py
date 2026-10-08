from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("lens", "0065_mcp_oauth")]

    operations = [
        migrations.AddField(
            model_name="mcpserver",
            name="oauth_client_redirect_uri",
            field=models.CharField(blank=True, default="", max_length=1000),
        ),
        migrations.AddField(
            model_name="mcpuseroauthstate",
            name="redirect_uri",
            field=models.CharField(blank=True, default="", max_length=1000),
        ),
    ]
