from django.db import migrations


def retire_mcp_oauth(apps, schema_editor):
    database = schema_editor.connection.alias
    MCPServer = apps.get_model("lens", "MCPServer")
    MCPUserOAuthGrant = apps.get_model("lens", "MCPUserOAuthGrant")
    MCPUserOAuthState = apps.get_model("lens", "MCPUserOAuthState")

    MCPServer.objects.using(database).update(
        oauth_enabled=False,
        oauth_issuer="",
        oauth_resource="",
        oauth_scopes="",
        oauth_client_id="",
        oauth_client_secret_encrypted="",
        oauth_client_redirect_uri="",
    )
    MCPUserOAuthGrant.objects.using(database).all().delete()
    MCPUserOAuthState.objects.using(database).all().delete()


class Migration(migrations.Migration):
    dependencies = [("lens", "0066_mcp_oauth_redirect_uri")]

    operations = [
        migrations.RunPython(retire_mcp_oauth, migrations.RunPython.noop),
    ]
