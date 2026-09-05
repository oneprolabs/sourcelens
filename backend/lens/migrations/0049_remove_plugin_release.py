"""Remove persisted Plugin release lifecycle state."""

from django.db import migrations


class Migration(migrations.Migration):
    """Drop PluginRelease metadata; manifests are filesystem-owned."""

    dependencies = [
        ("lens", "0048_sharedqa_content_language"),
    ]

    operations = [
        migrations.DeleteModel(
            name="PluginRelease",
        ),
    ]
