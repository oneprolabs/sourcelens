import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("lens", "0064_mcp_user_identity"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RemoveField("mcpserver", "pass_user_identity"),
        migrations.RemoveField("mcpserver", "user_identity_audience"),
        migrations.AddField("mcpserver", "oauth_enabled", models.BooleanField(default=False)),
        migrations.AddField(
            "mcpserver",
            "oauth_issuer",
            models.URLField(blank=True, default="", max_length=500),
        ),
        migrations.AddField(
            "mcpserver",
            "oauth_resource",
            models.CharField(blank=True, default="", max_length=500),
        ),
        migrations.AddField(
            "mcpserver",
            "oauth_scopes",
            models.CharField(blank=True, default="", max_length=500),
        ),
        migrations.AddField(
            "mcpserver",
            "oauth_client_id",
            models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            "mcpserver",
            "oauth_client_secret_encrypted",
            models.TextField(blank=True, default=""),
        ),
        migrations.CreateModel(
            name="MCPUserOAuthGrant",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("access_token_encrypted", models.TextField()),
                ("refresh_token_encrypted", models.TextField(blank=True, default="")),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                ("scope", models.CharField(blank=True, default="", max_length=1000)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("mcp", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="lens.mcpserver")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddConstraint(
            model_name="mcpuseroauthgrant",
            constraint=models.UniqueConstraint(fields=("user", "mcp"), name="lens_mcp_user_oauth_unique"),
        ),
        migrations.CreateModel(
            name="MCPUserOAuthState",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("state_hash", models.CharField(max_length=64, unique=True)),
                ("verifier_encrypted", models.TextField()),
                ("expires_at", models.DateTimeField()),
                ("mcp", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="lens.mcpserver")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
            ],
        ),
    ]
