"""Immutable datasource selections captured for assistant sessions."""

import re

from ..models import (
    AssistantDataSourceBinding,
    DataSourceItem,
    SessionDataSource,
)

MOUNT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
MOUNT_NAME_MAX_LENGTH = 120


class DatasourceSnapshotError(RuntimeError):
    """Raised when a required datasource has no ready version."""


def _validate_mount_names(rows):
    """Reject Session mount names that are unsafe or ambiguous.

    The LensNode materializes each snapshot under ``sources/<mount_name>``,
    so a duplicate or path-like name would collide at run time. Validate the
    fully expanded names here so a bad selection fails before dispatch.
    """

    seen = set()
    for row in rows:
        name = str(row.mount_name or "")
        if (
            not name
            or len(name) > MOUNT_NAME_MAX_LENGTH
            or not MOUNT_NAME_PATTERN.fullmatch(name)
            or name in seen
        ):
            raise DatasourceSnapshotError("SESSION_MOUNT_NAME_CONFLICT")
        seen.add(name)


def capture_session_datasources(session, assistant, bindings=None):
    """Expand assistant bindings into a reproducible session snapshot."""

    if bindings is None:
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
    _validate_mount_names(rows)
    return SessionDataSource.objects.bulk_create(rows)
