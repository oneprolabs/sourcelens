from django.db import migrations, models
import uuid

class Migration(migrations.Migration):
    dependencies = [("lens", "0054_datasource_deployment")]
    operations = [migrations.CreateModel(
        name="SessionCleanupOperation",
        fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("uuid", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
            ("created_at", models.DateTimeField(auto_now_add=True)),
            ("updated_at", models.DateTimeField(auto_now=True)),
            ("session_uuid", models.UUIDField(db_index=True)),
            ("lensnode_uuid", models.UUIDField(db_index=True)),
            ("status", models.CharField(choices=[("pending", "Pending"), ("sent", "Sent"), ("completed", "Completed"), ("failed", "Failed")], default="pending", max_length=16)),
            ("attempts", models.PositiveIntegerField(default=0)),
            ("last_error", models.TextField(blank=True, default="")),
            ("next_retry_at", models.DateTimeField(blank=True, db_index=True, null=True)),
            ("completed_at", models.DateTimeField(blank=True, null=True)),
        ],
        options={"constraints": [models.UniqueConstraint(fields=("session_uuid", "lensnode_uuid"), name="lens_session_cleanup_unique_target")]},
    )]
