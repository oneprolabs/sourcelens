"""Record ready datasource versions after successful processing."""

import uuid
from pathlib import Path

from django.conf import settings
from django.db import transaction

from .models import DataSourceVersion


@transaction.atomic
def record_datasource_versions(datasource):
    """Record a ready version for each active child after conversion."""

    versions = []
    for item in datasource.items.filter(status="active").select_for_update():
        version_name = uuid.uuid4().hex
        version_key = (
            f"datasources/{datasource.uuid}/versions/{item.uuid}/"
            f"{version_name}"
        )
        source = (Path(settings.MEDIA_ROOT) / item.storage_key).resolve()
        target = (Path(settings.MEDIA_ROOT) / version_key).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_symlink() or not source.is_dir():
            raise ValueError("DATASOURCE_SOURCE_UNAVAILABLE")
        if not target.exists():
            import shutil
            shutil.copytree(source, target)
        version = DataSourceVersion.objects.create(
            item=item,
            version=version_name,
            storage_key=version_key,
            status="ready",
        )
        item.current_version = version_name
        item.save(update_fields=["current_version", "updated_at"])
        versions.append(version)
    return versions
