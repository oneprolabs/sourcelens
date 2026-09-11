from django.db import migrations, models
import django.db.models.deletion
import uuid

class Migration(migrations.Migration):
    dependencies = [('lens', '0050_taskexecution_datasource_history_index')]
    operations = [
        migrations.CreateModel(
            name='DataSourceItem',
            fields=[
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=160)),
                ('source_type', models.CharField(max_length=32)),
                ('config', models.JSONField(blank=True, default=dict)),
                ('storage_key', models.CharField(max_length=500)),
                ('status', models.CharField(default='active', max_length=16)),
                ('current_version', models.CharField(blank=True, default='', max_length=64)),
                ('datasource', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='items', to='lens.datasource')),
            ],
            options={'constraints': [models.UniqueConstraint(fields=('datasource', 'storage_key'), name='lens_ds_item_storage_key_unique')]},
        ),
        migrations.CreateModel(
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
            options={'constraints': [models.UniqueConstraint(fields=('item', 'version'), name='lens_ds_item_version_unique')]},
        ),
        migrations.CreateModel(
            name='AssistantDataSourceBinding',
            fields=[
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('mount_name', models.CharField(max_length=120)),
                ('required', models.BooleanField(default=True)),
                ('assistant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='datasource_bindings', to='lens.assistant')),
                ('datasource', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='lens.datasource')),
                ('item', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='lens.datasourceitem')),
            ],
            options={'constraints': [models.UniqueConstraint(fields=('assistant', 'mount_name'), name='lens_assistant_datasource_mount_unique')]},
        ),
        migrations.CreateModel(
            name='SessionDataSource',
            fields=[
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('datasource_version', models.CharField(blank=True, default='', max_length=64)),
                ('mount_name', models.CharField(max_length=120)),
                ('storage_key', models.CharField(max_length=500)),
                ('datasource', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='lens.datasource')),
                ('item', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='lens.datasourceitem')),
                ('session', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='datasource_snapshots', to='lens.session')),
                ('version', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='session_snapshots', to='lens.datasourceversion')),
            ],
        ),
    ]
