"""Export persisted LensNode observations to Langfuse ingestion."""

import base64
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from urllib import error as urlerror
from urllib import parse, request

from .models import Run
from .trace_context import root_observation_id_for_run, trace_id_for_run

logger = logging.getLogger(__name__)

OBSERVATION_ID_PATTERN = re.compile(r"^(?:[0-9a-f]{16}|[0-9a-f]{32})$")
OBSERVATION_NAME_PATTERN = re.compile(
    r"^(?:model|tool)\.[A-Za-z0-9._-]{1,96}$"
)
ERROR_TYPE_PATTERN = re.compile(r"^[A-Za-z0-9_.]{1,128}$")
SUMMARY_ENABLED_VALUES = {"1", "on", "true", "yes"}
MODEL_OBSERVATION_SUMMARIES = {
    "model.control": "Select and coordinate the next workflow action.",
    "model.agent": "Run one agent model reasoning and response round.",
    "model.final_synthesis": (
        "Synthesize the final response from the completed workflow."
    ),
    "model.summarization": "Summarize prior context for the next model round.",
}
ROOT_OBSERVATION_SUMMARY = (
    "Run the SourceLens agent workflow for this request."
)


class TraceExportError(RuntimeError):
    """Classify retryable trace export failures without storing details."""

    def __init__(self, category):
        super().__init__(category)
        self.category = category


def build_ingestion_batch(
    run,
    root_observation_id,
    include_summaries=False,
):
    """Build one privacy-bounded Langfuse ingestion batch for a run."""

    from .services import build_run_history_metadata

    trace_id = trace_id_for_run(run.uuid)
    started_at = _timestamp(run.started_at or run.created_at)
    finished_at = _timestamp(
        run.finished_at or run.updated_at or run.created_at
    )
    run_metadata = {
        "runUuid": str(run.uuid),
        "apiKeyUuid": str(run.session.api_key_id) if run.session.api_key_id else None,
        "status": run.status,
        "outcome": run.outcome,
        "retryOfRunUuid": (
            str(run.retry_of_run.uuid) if run.retry_of_run_id else None
        ),
        "explicitRetry": bool(run.retry_of_run_id),
    }
    history_metadata = build_run_history_metadata(run)
    run_metadata.update(
        {
            "historyRunsBeforeFiltering": history_metadata[
                "history_runs_before_filtering"
            ],
            "historyRunsAfterFiltering": history_metadata[
                "history_runs_after_filtering"
            ],
            "supersededRetryAttemptsRemoved": history_metadata[
                "superseded_retry_attempts_removed"
            ],
            "nonCompletedAssistantOutputsExcluded": history_metadata[
                "non_completed_assistant_outputs_excluded"
            ],
        }
    )
    root_metadata = dict(run_metadata)
    if include_summaries:
        root_metadata["comment"] = ROOT_OBSERVATION_SUMMARY
    batch = [
        _event(
            "trace-create",
            started_at,
            {
                "id": trace_id,
                "timestamp": started_at,
                "name": "sourcelens.run",
                "sessionId": str(run.session.uuid),
                "userId": str(run.session.user_id) if run.session.user_id is not None else None,
                "metadata": run_metadata,
            },
        ),
        _event(
            "span-create",
            started_at,
            {
                "id": root_observation_id,
                "traceId": trace_id,
                "name": "run.agent",
                "startTime": started_at,
                "metadata": root_metadata,
            },
        ),
    ]

    for start_event, end_event in _paired_observations(run):
        if start_event["parent_observation_id"] != root_observation_id:
            continue
        observation_id = start_event["id"]
        observation_started_at = _validated_timestamp(
            start_event.get("started_at"),
            started_at,
        )
        create_body = {
            "id": observation_id,
            "traceId": trace_id,
            "parentObservationId": root_observation_id,
            "name": start_event["name"],
            "startTime": observation_started_at,
        }
        if include_summaries:
            create_body["metadata"] = {
                "comment": _observation_summary(start_event["name"]),
            }
        batch.append(
            _event(
                "span-create",
                observation_started_at,
                create_body,
            )
        )
        status = "failed"
        error_type = "ObservationNotClosed"
        observation_finished_at = finished_at
        if end_event is not None:
            status = "done" if end_event.get("status") == "done" else "failed"
            error_type = end_event.get("error_type") or "ObservationFailed"
            observation_finished_at = _validated_timestamp(
                end_event.get("ended_at"),
                finished_at,
            )
        metadata = {"status": status}
        update_body = {
            "id": observation_id,
            "traceId": trace_id,
            "endTime": observation_finished_at,
            "metadata": metadata,
        }
        if status == "failed":
            update_body["level"] = "ERROR"
            if isinstance(error_type, str) and ERROR_TYPE_PATTERN.fullmatch(
                error_type
            ):
                metadata["errorType"] = error_type
        batch.append(
            _event(
                "span-update",
                observation_finished_at,
                update_body,
            )
        )

    root_update = {
        "id": root_observation_id,
        "traceId": trace_id,
        "endTime": finished_at,
        "metadata": {"status": run.status},
    }
    if run.status != Run.Status.DONE:
        root_update["level"] = "ERROR"
    batch.append(_event("span-update", finished_at, root_update))
    for index, event in enumerate(batch):
        event["id"] = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"sourcelens:{trace_id}:{index}:{event['type']}",
            )
        )
    return batch


def export_run_trace(run_pk, *, raise_on_failure=False):
    """Export one completed run, returning whether a batch was sent."""

    config = _langfuse_config()
    if config is None:
        return False
    run = (
        Run.objects.select_related("session")
        .prefetch_related("steps")
        .get(pk=run_pk)
    )
    root_observation_id = root_observation_id_for_run(run.uuid)
    batch = build_ingestion_batch(
        run,
        root_observation_id,
        include_summaries=_observation_summaries_enabled(),
    )
    if sum(event["type"] == "span-create" for event in batch) <= 1:
        return False

    public_key, secret_key, ingestion_url = config
    auth = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
    payload = json.dumps({"batch": batch}).encode()
    http_request = request.Request(
        ingestion_url,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/json",
        },
    )
    try:
        with request.urlopen(http_request, timeout=10) as response:
            if response.status not in {200, 207}:
                logger.warning(
                    "Langfuse trace export returned HTTP %s for run %s.",
                    response.status,
                    run.uuid,
                )
                if raise_on_failure:
                    raise TraceExportError("http_status")
                return False
    except (OSError, urlerror.URLError) as exc:
        logger.warning(
            "Langfuse trace export failed for run %s: %s.",
            run.uuid,
            type(exc).__name__,
        )
        if raise_on_failure:
            raise TraceExportError("network") from exc
        return False
    return True


def _paired_observations(run):
    """Return valid start events paired with their last valid end event."""

    observations = {}
    for step in run.steps.all():
        for event in (step.detail or {}).get("events", []):
            observation = event.get("observation")
            if not isinstance(observation, dict):
                continue
            observation_id = observation.get("id")
            if not isinstance(observation_id, str) or not (
                OBSERVATION_ID_PATTERN.fullmatch(observation_id)
            ):
                continue
            action = observation.get("action")
            if action == "start" and observation_id not in observations:
                parent_id = observation.get("parent_observation_id")
                name = observation.get("name")
                if not isinstance(parent_id, str) or not (
                    OBSERVATION_ID_PATTERN.fullmatch(parent_id)
                ):
                    continue
                if not isinstance(name, str) or not (
                    OBSERVATION_NAME_PATTERN.fullmatch(name)
                ):
                    continue
                observations[observation_id] = [observation, None]
            elif action == "end" and observation_id in observations:
                observations[observation_id][1] = observation
    return list(observations.values())


def _observation_summary(name):
    """Return a privacy-bounded summary for an observation name."""

    if name.startswith("tool."):
        tool_name = name.removeprefix("tool.")
        return f"Execute the {tool_name} tool."
    return MODEL_OBSERVATION_SUMMARIES.get(
        name,
        "Run one model workflow step.",
    )


def _observation_summaries_enabled():
    """Return whether Langfuse observation summaries are enabled."""

    value = os.getenv("LANGFUSE_OBSERVATION_SUMMARY_ENABLED", "")
    return value.strip().lower() in SUMMARY_ENABLED_VALUES


def _langfuse_config():
    """Return configured credentials and normalized ingestion URL."""

    public_key = os.getenv("LANGFUSE_PUBLIC_KEY", "").strip()
    secret_key = os.getenv("LANGFUSE_SECRET_KEY", "").strip()
    host = (
        os.getenv("LANGFUSE_OTEL_HOST", "").strip()
        or os.getenv("LANGFUSE_HOST", "").strip()
    )
    if not public_key or not secret_key or not host:
        return None
    base_url = host.split("/api/public/otel", 1)[0].rstrip("/")
    parsed = parse.urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return (
        public_key,
        secret_key,
        f"{base_url}/api/public/ingestion",
    )


def _event(event_type, timestamp, body):
    """Build one Langfuse ingestion event."""

    return {
        "id": str(uuid.uuid4()),
        "type": event_type,
        "timestamp": timestamp,
        "body": body,
    }


def _validated_timestamp(value, fallback):
    """Return a normalized timestamp or a trusted fallback."""

    if not isinstance(value, str):
        return fallback
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return fallback
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return _timestamp(parsed)


def _timestamp(value):
    """Return one UTC ISO-8601 timestamp."""

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return (
        value.astimezone(timezone.utc)
        .isoformat()
        .replace(
            "+00:00",
            "Z",
        )
    )
