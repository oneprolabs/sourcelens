from django.db import migrations, models
import django.db.models.deletion
import uuid

class Migration(migrations.Migration):
    dependencies = [('lens','0051_datasource_items_session_snapshots')]
    operations = [migrations.CreateModel(
        name='DataSourceVersion',
        fields=[
            ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('updated_at', models.DateTimeField(auto_now=True)),
            ('version', models.CharField(max_length=64)),
            ('storage_key', models.CharField(max_length=500)),
            ('status', models.CharField(default='ready', max_length=16)),
            ('checksum', models.CharField(blank=True, default='', max_length=128)),
            ('item', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='versions', to='lens.datasourceitem')),
        ],
        options={'constraints':[models.UniqueConstraint(fields=('item','version'), name='lens_ds_item_version_unique')]},
    )]
