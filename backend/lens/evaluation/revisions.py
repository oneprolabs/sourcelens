"""Capture dataset revisions and safe historical Run provenance."""

import hashlib
import json

from lens.evaluation.models import EvaluationDatasetRevision


def _content_hash(metadata, cases):
    """Return a stable digest of one revision payload."""

    payload = json.dumps(
        {"metadata": metadata, "cases": cases},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def record_dataset_revision(dataset):
    """Capture all examples; callers must hold the dataset row lock.

    An edit that leaves both metadata and cases unchanged does not create a new
    revision, so repeated saves cannot multiply full case snapshots.
    """

    cases = [
        {
            "uuid": str(case.uuid),
            "input": case.input,
            "expected_answer": case.expected_answer,
            "expected_facts": case.expected_facts,
            "expected_sources": case.expected_sources,
            "tags": case.tags,
            "config": case.config,
        }
        for case in dataset.cases.order_by("created_at", "uuid")
    ]
    metadata = {
        "name": dataset.name,
        "status": dataset.status,
        "version": dataset.version,
        "assistant_uuid": str(dataset.assistant.uuid),
        "config_snapshot": dataset.config_snapshot,
    }
    content_hash = _content_hash(metadata, cases)
    previous = dataset.revisions.order_by("-revision").first()
    if previous is not None and previous.content_hash == content_hash:
        return previous
    revision = (previous.revision if previous is not None else 0) + 1
    return EvaluationDatasetRevision.objects.create(
        dataset=dataset,
        revision=revision,
        metadata=metadata,
        cases=cases,
        case_count=len(cases),
        content_hash=content_hash,
    )


def baseline_metadata(run):
    """Select historical provenance without copying runtime secrets or live configuration."""

    execution = getattr(run, "execution", None)
    snapshot = (execution.runtime_snapshot if execution else {}) or {}
    rows = run.session.datasource_snapshots.select_related("datasource", "item", "version").order_by("uuid")
    sources = [
        {
            "datasource_uuid": str(row.datasource.uuid),
            "item_uuid": str(row.item.uuid) if row.item_id else None,
            "version": row.datasource_version or None,
            "version_uuid": str(row.version.uuid) if row.version_id else None,
            "checksum": row.version.checksum or None if row.version_id else None,
            "mount_name": row.mount_name,
        }
        for row in rows
    ]
    models = sorted(
        {
            value
            for payload in run.trace_events.filter(event_type="model.started").values_list("payload", flat=True)
            if isinstance(payload, dict)
            for value in [payload.get("model")]
            if isinstance(value, str) and value and len(value) <= 255
        }
    )
    return {
        "models": models or None,
        "platform_version": snapshot.get("platform_version") or None,
        "model_refs": snapshot.get("model_refs") or None,
        "model_config_hashes": snapshot.get("model_config_hashes") or None,
        "lensnode_uuid": snapshot.get("lensnode_uuid") or None,
        "lensnode_version": snapshot.get("lensnode_agent_version") or None,
        "assistant_uuid": snapshot.get("assistant_uuid") or None,
        "assistant_updated_at": snapshot.get("assistant_updated_at") or None,
        "datasources": sources if sources else None,
    }
