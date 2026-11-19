"""Prototype risk summaries and content-free API key invocation accounting."""

import uuid
from datetime import timedelta

from django.db import transaction
from django.db.models import Count, OuterRef, Q, Subquery
from django.db.models.functions import Coalesce
from django.utils import timezone

from accounts.models import TenantApiKeyCall


def key_risks(record):
    """Use the same rolling boundaries as the prototype's list warning."""
    now = timezone.now()
    active = record.status == "active" and (record.expires_at is None or record.expires_at > now)
    return {
        "expiring_soon": bool(active and record.expires_at and record.expires_at <= now + timedelta(days=7)),
        "unused": bool(active and (record.last_used_at is None or record.last_used_at < now - timedelta(days=30))),
    }


def risk_summary(queryset, now=None):
    """Count active risks before search, status filtering or pagination."""
    now = now or timezone.now()
    active = queryset.filter(status="active").filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
    return active.aggregate(
        expiring_soon=Count("pk", filter=Q(expires_at__lte=now + timedelta(days=7))),
        unused=Count("pk", filter=Q(last_used_at__isnull=True) | Q(last_used_at__lt=now - timedelta(days=30))),
    )


def annotate_call_counts(queryset):
    """Count real execution attempts without multiplying keys or per-row queries."""
    calls = (
        TenantApiKeyCall.objects.filter(
            key_id=OuterRef("pk"), tenant_id=OuterRef("tenant_id"), created_at__gte=timezone.now() - timedelta(days=7)
        )
        .order_by()
        .values("key_id")
        .annotate(total=Count("pk"))
        .values("total")
    )
    return queryset.annotate(calls_7d=Coalesce(Subquery(calls), 0))


@transaction.atomic
def record_invocation(response, identity, session_uuid, elapsed):
    """Persist only trusted credential identity, tenant-local references and HTTP outcome."""
    from lens.models import Run, Session

    try:
        session_uuid = uuid.UUID(session_uuid)
    except ValueError:
        return
    session = (
        Session.objects.filter(
            uuid=session_uuid,
            tenant_id=identity["tenant_id"],
            api_key_id=identity["key_id"],
            user__isnull=True,
            api_key__tenant_id=identity["tenant_id"],
            assistant__tenant_id=identity["tenant_id"],
        )
        .select_related("assistant")
        .first()
    )
    assistant_uuid = session.assistant.uuid if session else None
    if identity["scope"] == "selected" and str(assistant_uuid) not in set(identity["assistant_uuids"] or []):
        assistant_uuid = None
    run_uuid = None
    if session and 200 <= response.status_code < 300:
        value = getattr(response, "data", {}).get("uuid")
        if value:
            try:
                run_uuid = (
                    Run.objects.filter(uuid=uuid.UUID(str(value)), tenant_id=identity["tenant_id"], session=session)
                    .values_list("uuid", flat=True)
                    .first()
                )
            except (ValueError, TypeError):
                pass
    TenantApiKeyCall.objects.create(
        key_id=identity["key_id"],
        tenant_id=identity["tenant_id"],
        key_version=identity["key_version"],
        assistant_uuid=assistant_uuid,
        run_uuid=run_uuid,
        status_code=response.status_code,
        duration_ms=min(max(round(elapsed * 1000), 0), 2**31 - 1),
    )


def detail_insights(record):
    """Return tenant-scoped labels and the six latest execution attempts without content."""
    from lens.models import Assistant, Run

    calls = list(TenantApiKeyCall.objects.filter(key=record, tenant_id=record.tenant_id)[:6])
    selected = set(str(value) for value in record.assistant_uuids or []) if record.scope == "selected" else set()
    ids = selected | {str(call.assistant_uuid) for call in calls if call.assistant_uuid}
    assistants = {
        str(item.uuid): item.name for item in Assistant.objects.filter(tenant_id=record.tenant_id, uuid__in=ids)
    }
    runs = {
        str(item.uuid): item
        for item in Run.objects.filter(
            Q(session__api_key=record) | Q(session__api_key__isnull=True),
            uuid__in=[call.run_uuid for call in calls if call.run_uuid],
            tenant_id=record.tenant_id,
            session__tenant_id=record.tenant_id,
            session__assistant__tenant_id=record.tenant_id,
        ).only("uuid", "status", "started_at", "finished_at")
    }
    recent = []
    for call in calls:
        run = runs.get(str(call.run_uuid))
        duration = None
        if run and run.started_at and run.finished_at:
            duration = max(0, round((run.finished_at - run.started_at).total_seconds() * 1000))
        recent.append(
            {
                "id": call.pk,
                "assistant_name": assistants.get(str(call.assistant_uuid), ""),
                "method": "POST",
                "route": "/api/lens/sessions/{session_uuid}/runs/",
                "status_code": call.status_code,
                "duration_ms": call.duration_ms,
                "execution_duration_ms": duration,
                "run_status": run.status if run else "",
                "key_version": call.key_version,
                "created_at": call.created_at.isoformat(),
            }
        )
    return {
        "assistants": [{"uuid": value, "name": assistants[value]} for value in sorted(selected) if value in assistants],
        "calls_7d": annotate_call_counts(type(record).objects.filter(pk=record.pk))
        .values_list("calls_7d", flat=True)
        .get(),
        "recent_calls": recent,
    }
