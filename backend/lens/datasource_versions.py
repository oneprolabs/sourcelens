"""Record ready datasource versions after successful processing."""

import uuid

from django.db import transaction

from .models import DataSourceVersion


@transaction.atomic
def record_datasource_versions(datasource):
    """Record a ready version for each active child after conversion."""

    versions = []
    for item in datasource.items.filter(status="active").select_for_update():
        version_name = uuid.uuid4().hex
        version = DataSourceVersion.objects.create(
            item=item,
            version=version_name,
            storage_key=item.storage_key,
            status="ready",
        )
        item.current_version = version_name
        item.save(update_fields=["current_version", "updated_at"])
        versions.append(version)
    return versions
