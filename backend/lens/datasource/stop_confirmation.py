"""Executor stop evidence is independent of a conversion's task outcome."""

from django.utils import timezone
from django.utils.dateparse import parse_datetime


def describe_conversion_stop(task):
    """Expose only explicit, persistent stop evidence for the exact task."""

    metadata = task.metadata or {}
    confirmed = metadata.get("stop_confirmation_state") == "confirmed"
    return {
        "state": "confirmed" if confirmed else "unconfirmed",
        "task_id": task.task_id,
        "lensnode_uuid": (
            metadata.get("stop_confirmation_lensnode_uuid") if confirmed else metadata.get("lensnode_uuid")
        ),
        "connection_id": metadata.get("stop_confirmation_connection_id") if confirmed else None,
        "executor_instance_id": metadata.get("stop_confirmation_executor_instance_id") if confirmed else None,
        "source": metadata.get("stop_confirmation_source") if confirmed else None,
        "confirmed_at": metadata.get("stop_confirmed_at") if confirmed else None,
    }


def conversion_stop_evidence(task, result, connection_id, lensnode_uuid):
    """Validate explicit worker exit or durable fencing from the owning socket."""

    metadata = task.metadata or {}
    if metadata.get("stop_confirmation_state") == "confirmed":
        return {}
    if result.get("executor_stopped") is not True or not connection_id or not lensnode_uuid:
        return {}
    if str(metadata.get("lensnode_uuid") or "") != str(lensnode_uuid):
        return {}
    if metadata.get("lensnode_connection_id") != str(connection_id):
        return {}
    if str(result.get("datasource_uuid") or "") != str(metadata.get("datasource_uuid") or ""):
        return {}
    if not result.get("executor_instance_id"):
        return {}
    source = result.get("stop_confirmation_source")
    if source not in {"lensnode_callback", "lensnode_absent_fenced"}:
        return {}
    try:
        stopped_at = parse_datetime(str(result.get("stop_confirmed_at") or ""))
    except ValueError:
        return {}
    if stopped_at is None or timezone.is_naive(stopped_at):
        return {}
    return {
        "stop_confirmation_state": "confirmed",
        "stop_confirmation_task_id": task.task_id,
        "stop_confirmation_lensnode_uuid": str(lensnode_uuid),
        "stop_confirmation_connection_id": str(connection_id),
        "stop_confirmation_executor_instance_id": str(result["executor_instance_id"]),
        "stop_confirmation_source": source,
        "stop_confirmed_at": timezone.now().isoformat(),
        "executor_stopped_at": stopped_at.isoformat(),
    }
