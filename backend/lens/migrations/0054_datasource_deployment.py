from django.db import migrations, models
import uuid

import django.db.models.deletion


def seed_deployments(apps, schema_editor):
    DataSource = apps.get_model("lens", "DataSource")
    Deployment = apps.get_model("lens", "DataSourceDeployment")
    for source in DataSource.objects.exclude(lensnode_id=None):
        Deployment.objects.get_or_create(
            datasource_id=source.pk,
            lensnode_id=source.lensnode_id,
            defaults={
                "target_path": source.target_path,
                "status": "active" if source.status == "active" else "disabled",
                "last_synced_at": source.last_synced_at,
                "last_error": source.last_error,
            },
        )


class Migration(migrations.Migration):
    dependencies = [("lens", "0053_datasource_item_storage_usage")]

    operations = [
        migrations.CreateModel(
            name="DataSourceDeployment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("uuid", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("target_path", models.CharField(blank=True, default="", max_length=500)),
                ("status", models.CharField(choices=[("active", "Active"), ("disabled", "Disabled")], default="active", max_length=16)),
                ("last_synced_at", models.DateTimeField(blank=True, null=True)),
                ("last_error", models.TextField(blank=True, default="")),
                ("datasource", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="deployments", to="lens.datasource")),
                ("lensnode", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="datasource_deployments", to="lens.lensnode")),
            ],
            options={
                "indexes": [
                    models.Index(fields=["datasource"], name="lens_ds_deploy_datasource_idx"),
                    models.Index(fields=["lensnode"], name="lens_ds_deploy_lensnode_idx"),
                ],
            },
        ),
        migrations.AddConstraint(
            model_name="datasourcedeployment",
            constraint=models.UniqueConstraint(fields=("datasource", "lensnode"), name="lens_datasource_deploy_unique_node"),
        ),
        migrations.RunPython(seed_deployments, migrations.RunPython.noop),
    ]
