"""Immutable datasource selections captured for assistant sessions."""

from .models import (
    AssistantDataSourceBinding,
    DataSourceItem,
    SessionDataSource,
)


class DatasourceSnapshotError(RuntimeError):
    """Raised when a required datasource has no ready version."""


def capture_session_datasources(session, assistant):
    """Expand assistant bindings into a reproducible session snapshot."""

    bindings = AssistantDataSourceBinding.objects.filter(
        assistant=assistant
    ).select_related("datasource", "item")
    rows = []
    for binding in bindings:
        items = [binding.item] if binding.item_id else list(
            DataSourceItem.objects.filter(
                datasource=binding.datasource,
                status="active",
            ).order_by("uuid")
        )
        if not items:
            items = [None]
        for item in items:
            version = (
                item.versions.filter(status="ready").order_by("-created_at").first()
                if item is not None
                else None
            )
            if item is not None and version is None and binding.required:
                raise DatasourceSnapshotError("DATASOURCE_VERSION_NOT_READY")
            storage_key = (
                version.storage_key if version is not None else item.storage_key
                if item is not None
                else f"datasources/{binding.datasource.uuid}"
            )
            mount_name = binding.mount_name
            if len(items) > 1 and item is not None:
                mount_name = f"{mount_name}_{item.uuid.hex[:8]}"
            rows.append(
                SessionDataSource(
                    session=session,
                    datasource=binding.datasource,
                    item=item,
                    datasource_version=(
                        version.version if version is not None else ""
                    ),
                    version=version,
                    mount_name=mount_name,
                    storage_key=storage_key,
                )
            )
    return SessionDataSource.objects.bulk_create(rows)
