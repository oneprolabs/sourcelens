from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies = [('lens','0052_datasource_versions')]
    operations = [migrations.AddField(
        model_name='sessiondatasource', name='version',
        field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='session_snapshots', to='lens.datasourceversion'),
    )]
