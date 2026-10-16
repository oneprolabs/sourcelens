from django.db import migrations


class Migration(migrations.Migration):
    """Join the independent alert-rule and built-in decision migrations."""

    dependencies = [
        ("lens", "0064_alert_rules"),
        ("lens", "0064_builtin_decisions"),
    ]

    operations = []
