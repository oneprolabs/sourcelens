"""Validated assistant datasource configuration."""

from rest_framework import serializers

from ..models import AssistantDataSourceBinding, DataSource, DataSourceItem


class BindingInput(serializers.Serializer):
    """Resolve public resource IDs without accepting filesystem paths."""

    datasource_uuid = serializers.SlugRelatedField(
        source="datasource", slug_field="uuid",
        queryset=DataSource.objects.filter(status="active"),
    )
    item_uuid = serializers.SlugRelatedField(
        source="item", slug_field="uuid", required=False, allow_null=True,
        queryset=DataSourceItem.objects.filter(status="active"),
    )
    mount_name = serializers.RegexField(
        r"^[A-Za-z0-9][A-Za-z0-9_-]*$", max_length=120
    )
    required = serializers.BooleanField(default=True)

    def validate(self, attrs):
        """Reject resources belonging to another datasource."""
        item = attrs.get("item")
        if item and item.datasource_id != attrs["datasource"].pk:
            raise serializers.ValidationError("DATASOURCE_ITEM_MISMATCH")
        return attrs


class DatasourceBindingsField(serializers.Field):
    """Read and replace a complete set of assistant bindings."""

    def to_internal_value(self, data):
        """Validate the full selection before any database mutation."""
        serializer = BindingInput(data=data, many=True)
        serializer.is_valid(raise_exception=True)
        rows = serializer.validated_data
        mounts = [row["mount_name"] for row in rows]
        resources = [(row["datasource"].pk, getattr(row.get("item"), "pk", None))
                     for row in rows]
        if len(set(mounts)) != len(rows) or len(set(resources)) != len(rows):
            raise serializers.ValidationError("DUPLICATE_DATASOURCE_BINDING")
        return rows

    def to_representation(self, manager):
        """Expose names for display and UUIDs for stable selection."""
        return [{
            "uuid": str(row.uuid),
            "datasource_uuid": str(row.datasource.uuid),
            "datasource_name": row.datasource.name,
            "source_type": row.datasource.source_type,
            "plugin_key": row.datasource.plugin_key,
            "item_uuid": str(row.item.uuid) if row.item else None,
            "item_name": row.item.name if row.item else None,
            "item_names": (
                list(row.datasource.items.filter(status="active").values_list(
                    "name", flat=True
                ))
                if row.item is None else []
            ),
            "mount_name": row.mount_name,
            "required": row.required,
        } for row in manager.select_related("datasource", "item")]


def replace_datasource_bindings(assistant, rows):
    """Replace validated bindings inside the caller's transaction."""
    if rows is None:
        return
    assistant.datasource_bindings.all().delete()
    AssistantDataSourceBinding.objects.bulk_create([
        AssistantDataSourceBinding(assistant=assistant, **row) for row in rows
    ])
