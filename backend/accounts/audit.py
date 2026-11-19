"""Write append-only audit records with conservative redaction."""

from accounts.models import AuditLog

_SENSITIVE_MARKERS = (
    "secret",
    "password",
    "token",
    "credential",
    "private_key",
    "api_key",
)


def _redact(value):
    """Recursively remove values whose keys look secret-bearing."""
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if any(marker in str(key).lower() for marker in _SENSITIVE_MARKERS) else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def record_audit(
    *,
    action,
    request=None,
    tenant=None,
    actor=None,
    actor_type=AuditLog.ActorType.USER,
    delegation=None,
    result=AuditLog.Result.SUCCESS,
    resource_type="",
    resource_id="",
    metadata=None,
):
    """Persist one audit record without failing the caller."""
    try:
        ip_address = None
        request_id = ""
        if request is not None:
            ip_address = request.META.get("REMOTE_ADDR") or None
            request_id = request.headers.get("X-Request-ID") or request.META.get("HTTP_X_REQUEST_ID") or ""
        return AuditLog.objects.create(
            tenant=tenant,
            actor_user=actor,
            actor_type=actor_type,
            delegation=delegation,
            action=str(action)[:150],
            resource_type=str(resource_type)[:50],
            resource_id=str(resource_id)[:64],
            result=result,
            ip_address=ip_address,
            request_id=str(request_id)[:100],
            metadata=_redact(metadata or {}),
        )
    except Exception:
        return None
