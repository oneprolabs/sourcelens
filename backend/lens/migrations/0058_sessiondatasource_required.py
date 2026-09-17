from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("lens", "0057_alter_datasource_source_type")]

    operations = [
        migrations.AddField(
            model_name="sessiondatasource",
            name="required",
            field=models.BooleanField(default=True),
        ),
    ]
