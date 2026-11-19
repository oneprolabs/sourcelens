"""Validation and append-only persistence for LensNode run trajectories."""

import json
import re
import uuid
from dataclasses import dataclass

from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from lens.models import Run, RunTraceEvent

MAX_BATCH_EVENTS = 100
MAX_BATCH_BYTES = 8 * 1024 * 1024
MAX_EVENT_PAYLOAD_BYTES = 2 * 1024 * 1024
EVENT_TYPE_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")


class RunTraceValidationError(ValueError):
    """Raised when an external trace batch violates the event contract."""


@dataclass(frozen=True)
class RunTraceAppendResult:
    """Describe the outcome of one idempotent append operation."""

    inserted_count: int
    duplicate_count: int
    last_sequence: int


def _positive_int(value, field, *, maximum=None):
    if isinstance(value, bool):
        raise RunTraceValidationError(f"{field} must be a positive integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise RunTraceValidationError(
            f"{field} must be a positive integer"
        ) from exc
    if parsed < 1 or (maximum is not None and parsed > maximum):
        raise RunTraceValidationError(f"{field} is outside the allowed range")
    return parsed


def _optional_positive_int(value, field):
    if value is None:
        return None
    return _positive_int(value, field)


def _bounded_text(value, field):
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > 128:
        raise RunTraceValidationError(
            f"{field} must be at most 128 characters"
        )
    return value


def _normalize_event(item, index):
    if not isinstance(item, dict):
        raise RunTraceValidationError(f"events[{index}] must be an object")
    try:
        event_id = uuid.UUID(str(item.get("event_id")))
    except (TypeError, ValueError) as exc:
        raise RunTraceValidationError(
            f"events[{index}].event_id must be a UUID"
        ) from exc
    sequence = _positive_int(item.get("sequence"), "sequence")
    attempt = _positive_int(
        item.get("attempt", 1),
        "attempt",
        maximum=1000,
    )
    event_type = item.get("event_type")
    if not isinstance(event_type, str) or not EVENT_TYPE_PATTERN.fullmatch(
        event_type
    ):
        raise RunTraceValidationError(f"events[{index}].event_type is invalid")
    timestamp = item.get("timestamp")
    if not isinstance(timestamp, str) or len(timestamp) > 64:
        raise RunTraceValidationError(
            f"events[{index}].timestamp must be an ISO-8601 string"
        )
    parsed_timestamp = parse_datetime(timestamp)
    if parsed_timestamp is None or timezone.is_naive(parsed_timestamp):
        raise RunTraceValidationError(
            f"events[{index}].timestamp must include a timezone"
        )
    payload = item.get("payload", {})
    if not isinstance(payload, dict):
        raise RunTraceValidationError(
            f"events[{index}].payload must be an object"
        )
    try:
        payload_bytes = len(
            json.dumps(
                payload,
                allow_nan=False,
                cls=DjangoJSONEncoder,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
    except (OverflowError, RecursionError, TypeError, ValueError) as exc:
        raise RunTraceValidationError(
            f"events[{index}].payload must contain JSON values"
        ) from exc
    if payload_bytes > MAX_EVENT_PAYLOAD_BYTES:
        raise RunTraceValidationError(
            f"events[{index}].payload exceeds the size limit"
        )
    return {
        "event_id": event_id,
        "sequence": sequence,
        "attempt": attempt,
        "event_type": event_type,
        "timestamp": parsed_timestamp,
        "checkpoint_id": _bounded_text(
            item.get("checkpoint_id"),
            f"events[{index}].checkpoint_id",
        ),
        "turn": _optional_positive_int(item.get("turn"), "turn"),
        "step": _optional_positive_int(item.get("step"), "step"),
        "call_id": _bounded_text(
            item.get("call_id"),
            f"events[{index}].call_id",
        ),
        "parent_call_id": _bounded_text(
            item.get("parent_call_id"),
            f"events[{index}].parent_call_id",
        ),
        "payload": payload,
        "payload_bytes": payload_bytes,
    }


def _event_matches(existing, normalized):
    return all(
        getattr(existing, field) == normalized[field]
        for field in (
            "event_id",
            "sequence",
            "attempt",
            "event_type",
            "timestamp",
            "checkpoint_id",
            "turn",
            "step",
            "call_id",
            "parent_call_id",
            "payload",
        )
    )


@transaction.atomic
def append_run_trace_events(run_uuid, lensnode_uuid, events):
    """Validate and append one at-least-once trace event batch."""

    if not isinstance(events, list) or not events:
        raise RunTraceValidationError("events must be a non-empty list")
    if len(events) > MAX_BATCH_EVENTS:
        raise RunTraceValidationError("events exceeds the batch size limit")

    normalized = [
        _normalize_event(item, index) for index, item in enumerate(events)
    ]
    if sum(item["payload_bytes"] for item in normalized) > MAX_BATCH_BYTES:
        raise RunTraceValidationError("events exceeds the batch byte limit")
    sequences = [item["sequence"] for item in normalized]
    event_ids = [item["event_id"] for item in normalized]
    if len(set(sequences)) != len(sequences):
        raise RunTraceValidationError("events contains duplicate sequences")
    if len(set(event_ids)) != len(event_ids):
        raise RunTraceValidationError("events contains duplicate event IDs")
    if sequences != sorted(sequences):
        raise RunTraceValidationError("events must be ordered by sequence")

    try:
        run = Run.objects.select_for_update().get(uuid=run_uuid)
    except Run.DoesNotExist as exc:
        raise RunTraceValidationError("run does not exist") from exc
    if run.lensnode_id is None or str(run.lensnode.uuid) != str(lensnode_uuid):
        raise RunTraceValidationError("run is not assigned to this LensNode")

    existing = list(
        RunTraceEvent.objects.filter(run=run).filter(
            Q(event_id__in=event_ids) | Q(sequence__in=sequences)
        )
    )
    by_event_id = {item.event_id: item for item in existing}
    by_sequence = {item.sequence: item for item in existing}
    last_persisted_sequence = (
        RunTraceEvent.objects.filter(run=run)
        .order_by("-sequence")
        .values_list("sequence", flat=True)
        .first()
        or 0
    )
    duplicate_count = 0
    pending = []
    for item in normalized:
        matched_by_id = by_event_id.get(item["event_id"])
        matched_by_sequence = by_sequence.get(item["sequence"])
        if matched_by_id is not None or matched_by_sequence is not None:
            if (
                matched_by_id is None
                or matched_by_sequence is None
                or matched_by_id.pk != matched_by_sequence.pk
                or not _event_matches(matched_by_id, item)
            ):
                raise RunTraceValidationError(
                    "event ID or sequence conflicts with persisted trajectory"
                )
            duplicate_count += 1
            continue
        pending.append(
            RunTraceEvent(
                tenant_id=run.tenant_id,
                run=run,
                **{
                    key: value
                    for key, value in item.items()
                    if key != "payload_bytes"
                },
            )
        )
    pending_sequences = [item.sequence for item in pending]
    expected_sequences = list(
        range(
            last_persisted_sequence + 1,
            last_persisted_sequence + len(pending_sequences) + 1,
        )
    )
    if pending_sequences != expected_sequences:
        raise RunTraceValidationError(
            "new events must continue the persisted sequence cursor"
        )
    RunTraceEvent.objects.bulk_create(pending)
    last_sequence = max(sequences)
    return RunTraceAppendResult(
        inserted_count=len(pending),
        duplicate_count=duplicate_count,
        last_sequence=last_sequence,
    )
