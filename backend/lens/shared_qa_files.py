"""Immutable file snapshot services for shared Q&A turns."""

import logging
from pathlib import PurePosixPath

from django.core.files import File
from django.db import transaction

from .models import SharedQA, SharedQAFile

logger = logging.getLogger(__name__)


def _snapshot_source(
    share,
    source,
    kind,
    order,
    filename,
    content_type,
    byte_size,
):
    """Copy one source file into storage owned by the share."""

    snapshot = SharedQAFile(
        share=share,
        kind=kind,
        source_uuid=source.uuid,
        filename=filename,
        content_type=content_type or "",
        byte_size=byte_size,
        order=order,
    )
    try:
        with source.file.open("rb") as source_file:
            snapshot.file.save(
                filename,
                File(source_file, name=filename),
                save=False,
            )
        snapshot.save()
    except Exception:
        if snapshot.file:
            snapshot.file.delete(save=False)
        raise
    return snapshot


def _source_filename(source, preferred_name):
    """Return a stable display filename for a source file."""

    return preferred_name or PurePosixPath(source.file.name).name


def _copy_available_sources(share, strict):
    """Copy each still-available input and output file once."""

    run = share.run
    if run is None:
        return []

    created = []
    inputs = []
    if run.input_message_id:
        inputs = run.input_message.attachments.all()
    outputs = run.output_files.filter(message__isnull=False)
    existing_sources = set(
        share.files.values_list("kind", "source_uuid")
    )
    source_groups = [
        (
            SharedQAFile.Kind.INPUT,
            inputs,
            lambda item: _source_filename(item, item.original_name),
            lambda item: item.mime_type,
        ),
        (
            SharedQAFile.Kind.OUTPUT,
            outputs,
            lambda item: _source_filename(item, item.filename),
            lambda item: item.content_type,
        ),
    ]
    for kind, sources, filename_for, content_type_for in source_groups:
        for order, source in enumerate(sources):
            source_key = (kind, source.uuid)
            if source_key in existing_sources:
                continue
            try:
                snapshot = _snapshot_source(
                    share=share,
                    source=source,
                    kind=kind,
                    order=order,
                    filename=filename_for(source),
                    content_type=content_type_for(source),
                    byte_size=source.byte_size,
                )
            except Exception:
                if strict:
                    for created_snapshot in created:
                        created_snapshot.file.delete(save=False)
                    raise
                logger.warning(
                    "Skipping unavailable legacy share file %s",
                    source.uuid,
                )
                continue
            created.append(snapshot)
            existing_sources.add(source_key)
    return created


def snapshot_shared_qa_files(share, strict=True):
    """Ensure a share owns copies of all available turn files.

    New shares use strict mode so creation is all-or-nothing. Existing shares
    use best-effort mode because source files may already have been deleted.
    """

    created = []
    try:
        with transaction.atomic():
            locked_share = SharedQA.objects.select_for_update().get(
                pk=share.pk
            )
            created = _copy_available_sources(locked_share, strict)
    except Exception:
        for snapshot in created:
            snapshot.file.delete(save=False)
        raise
    return created
