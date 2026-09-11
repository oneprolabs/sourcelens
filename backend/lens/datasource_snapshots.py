"""Immutable datasource selections captured for assistant sessions."""

from .models import AssistantDataSourceBinding, DataSourceItem, SessionDataSource


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
            storage_key = (
                item.storage_key
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
                        item.current_version if item is not None else ""
                    ),
                    mount_name=mount_name,
                    storage_key=storage_key,
                )
            )
    return SessionDataSource.objects.bulk_create(rows)
