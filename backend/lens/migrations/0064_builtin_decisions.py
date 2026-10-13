"""Add built-in decisions while retaining legacy bindings for rolling upgrades."""

import base64
import hashlib
import uuid

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import migrations, models


def migrate_typesafe_bindings(apps, schema_editor):
    """Reuse each active TypeSafe connection as one shared decision model."""

    database = schema_editor.connection.alias
    Binding = apps.get_model("lens", "AssistantPluginBinding")
    Config = apps.get_model("agentcore_metering", "LLMConfig")
    key = base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest())
    fernet = Fernet(key)
    bindings = (
        Binding.objects.using(database)
        .filter(
            enabled=True,
            connection__plugin_key="typesafe",
            connection__status="active",
        )
        .select_related("assistant", "connection__secret_version__material")
        .order_by("pk")
    )
    for binding in bindings:
        assistant = binding.assistant
        if assistant.decision_model_ref:
            continue
        connection = binding.connection
        version = connection.secret_version
        if version is None or version.status != "active" or version.material.status != "active":
            continue
        try:
            secret = fernet.decrypt(version.encrypted_value.encode("utf-8")).decode("utf-8")
        except InvalidToken:
            continue
        if not secret.strip():
            continue
        model = connection.config.get("model")
        if not isinstance(model, str) or not model.strip():
            continue
        config, _ = Config.objects.using(database).get_or_create(
            uuid=uuid.uuid5(connection.uuid, "sourcelens.builtin.decision"),
            defaults={
                "scope": "global",
                "model_type": "decision",
                "provider": "typesafe",
                "is_active": True,
                "config": {
                    "api_base": connection.endpoint,
                    "api_key": secret,
                    "model": model,
                    "request_timeout_seconds": 3,
                    "num_retries": 0,
                },
            },
        )
        assistant.decision_model_ref = config.uuid
        assistant.decision_auto = binding.decision_auto
        assistant.decision_gates = binding.decision_gates
        assistant.decision_analyses = binding.decision_analyses
        assistant.save(
            using=database, update_fields=["decision_model_ref", "decision_auto", "decision_gates", "decision_analyses"]
        )


class Migration(migrations.Migration):
    """Expand the Assistant schema and migrate usable legacy configurations."""

    dependencies = [
        ("lens", "0063_assistantpluginbinding_decision_gates"),
        ("agentcore_metering", "0019_increase_cost_precision"),
    ]
    operations = [
        migrations.AddField(
            model_name="assistant", name="decision_model_ref", field=models.UUIDField(blank=True, null=True)
        ),
        migrations.AddField(model_name="assistant", name="decision_auto", field=models.BooleanField(default=True)),
        migrations.AddField(
            model_name="assistant", name="decision_gates", field=models.JSONField(blank=True, default=dict)
        ),
        migrations.AddField(
            model_name="assistant", name="decision_analyses", field=models.JSONField(blank=True, default=dict)
        ),
        migrations.RunPython(migrate_typesafe_bindings, migrations.RunPython.noop),
    ]
