from django.db import migrations, models


def preserve_secret_declarations(apps, schema_editor):
    """Backfill secret keys without changing encrypted values or bindings."""

    database = schema_editor.connection.alias
    variable_sets = apps.get_model("lens", "EnvironmentVariableSet")
    names_by_set = {}
    for model_name, resource_name in (
        ("AssistantMCP", "mcp"),
        ("AssistantSkill", "skill"),
    ):
        bindings = apps.get_model("lens", model_name).objects.using(database)
        for binding in bindings.exclude(
            environment_variable_set_id=None
        ).select_related(resource_name):
            resource = getattr(binding, resource_name)
            declarations = (
                resource.environment
                if resource_name == "mcp"
                else (resource.definition or {}).get("environment") or []
            )
            names = names_by_set.setdefault(
                binding.environment_variable_set_id, set()
            )
            names.update(
                item["name"]
                for item in declarations or []
                if isinstance(item, dict)
                and item.get("secret")
                and item.get("name")
            )
    for pk, names in names_by_set.items():
        variable_sets.objects.using(database).filter(pk=pk).update(
            secret_keys=sorted(names)
        )


class Migration(migrations.Migration):
    dependencies = [("lens", "0063_assistantpluginbinding_decision_gates")]

    operations = [
        migrations.AddField(
            model_name="environmentvariableset",
            name="secret_keys",
            field=models.JSONField(blank=True, default=list, db_default=[]),
        ),
        migrations.RunPython(
            preserve_secret_declarations, migrations.RunPython.noop
        ),
    ]
