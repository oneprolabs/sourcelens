"""Tenant dashboard summaries and configuration progress."""

from datetime import timedelta
from datetime import timezone as datetime_timezone

from django.db.models import Case, CharField, Count, Exists, F, OuterRef, Q, Subquery, Value, When
from django.db.models.functions import TruncDate
from django.utils import timezone

from accounts.models import TenantMembership
from lens.model_scope import model_queryset
from lens.models import Assistant, AssistantDataSourceBinding, Connection, DataSource, Run, ScheduledTask, SharedQA
from lens.plugins.public_connections import ANONYMOUS_CONNECTION_FILTER
from lens.plugins.registry import RETIRED_PLUGIN_KEYS
from lens.public_share import scope_shares_for_moderation


def tenant_overview(tenant):
    """Aggregate only explicitly owned resources without credentials or conversation text."""

    now = timezone.now()
    cutoff = now - timedelta(days=7)
    midnight = now.astimezone(datetime_timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    first_start = midnight - timedelta(days=6)
    questions = Run.objects.filter(tenant=tenant, source=Run.Source.USER, created_at__lte=now)
    current = questions.filter(created_at__gte=cutoff).count()
    previous = questions.filter(created_at__gte=cutoff - timedelta(days=7), created_at__lt=cutoff).count()
    daily = {
        row["day"]: row["count"]
        for row in questions.filter(created_at__gte=first_start)
        .annotate(day=TruncDate("created_at", tzinfo=datetime_timezone.utc))
        .values("day")
        .annotate(count=Count("pk"))
    }
    assistants = Assistant.objects.filter(tenant=tenant, is_system=False)
    active_assistants = assistants.filter(status=Assistant.Status.ACTIVE, is_enabled=True)
    sources = DataSource.objects.filter(tenant=tenant)
    connections = (
        Connection.objects.filter(tenant=tenant)
        .exclude(ANONYMOUS_CONNECTION_FILTER)
        .exclude(plugin_key__in=RETIRED_PLUGIN_KEYS)
    )
    sync_status = ScheduledTask.objects.filter(
        task_type="source_sync", target_type="datasource", target_id=OuterRef("uuid")
    ).order_by("-updated_at", "-uuid")
    source_states = sources.annotate(sync_status=Subquery(sync_status.values("last_status")[:1]))
    health = {"synced": 0, "running": 0, "failed": 0, "waiting": 0, "disabled": 0}
    source_states = source_states.annotate(
        health_state=Case(
            When(status="disabled", then=Value("disabled")),
            When(sync_status="running", then=Value("running")),
            When(Q(sync_status="failed") | ~Q(last_error=""), then=Value("failed")),
            When(last_synced_at__isnull=False, then=Value("synced")),
            default=Value("waiting"),
            output_field=CharField(),
        )
    )
    for row in source_states.order_by().values("health_state").annotate(count=Count("pk")):
        health[row["health_state"]] = row["count"]
    models = model_queryset(tenant).filter(is_active=True, model_type="llm")
    members = TenantMembership.objects.filter(tenant=tenant)
    knowledge_required = active_assistants.filter(capability__in=["knowledge_qa", "code_analysis"]).exists()
    model_configured = models.exists()
    source_configured = sources.filter(status="active").exists()
    model_bound_assistants = active_assistants.filter(agent_model_ref__in=models.values("uuid"))
    bound_sources = AssistantDataSourceBinding.objects.filter(
        assistant_id=OuterRef("pk"), datasource__tenant=tenant, datasource__status="active"
    )
    missing_knowledge = (
        active_assistants.filter(capability__in=["knowledge_qa", "code_analysis"])
        .exclude(Exists(bound_sources))
        .exists()
    )
    assistant_attention = active_assistants.filter(
        ~Q(agent_model_ref__in=models.values("uuid"))
        | Q(agent_model_ref__isnull=True)
        | Q(lensnode__status__in=["offline", "unresponsive"])
        | Q(lensnode__token_revoked=True)
    ).count()
    assistant_configured = model_bound_assistants.exists() and not missing_knowledge and not assistant_attention
    events = []
    for queryset, kind in ((assistants, "assistant_created"), (sources, "datasource_created")):
        events.extend(
            {"kind": kind, "name": row["name"], "at": row["created_at"]}
            for row in queryset.filter(created_at__lte=now).order_by("-created_at").values("name", "created_at")[:8]
        )
    events.extend(
        {"kind": "datasource_synced", "name": row["name"], "at": row["last_synced_at"]}
        for row in sources.filter(last_synced_at__lte=now)
        .order_by("-last_synced_at")
        .values("name", "last_synced_at")[:8]
    )
    events.extend(
        {"kind": "member_joined", "name": "", "at": joined_at}
        for joined_at in members.filter(joined_at__lte=now)
        .order_by("-joined_at")
        .values_list("joined_at", flat=True)[:8]
    )
    return {
        "tenant": {"uuid": str(tenant.uuid), "name": tenant.name},
        "generated_at": now.isoformat(),
        "timezone": "UTC",
        "member_count": members.count(),
        "assistant_count": assistants.exclude(status=Assistant.Status.ARCHIVED).count(),
        "datasource_count": sources.count(),
        "run_count_7d": current,
        "run_count_previous_7d": previous,
        "run_change_percent": round((current - previous) * 100 / previous, 1) if previous else None,
        "question_series_7d": [
            {
                "date": (first_start.date() + timedelta(days=offset)).isoformat(),
                "count": daily.get(first_start.date() + timedelta(days=offset), 0),
            }
            for offset in range(7)
        ],
        "datasource_health": health,
        "attention": {
            "failed_datasource_count": health["failed"],
            "assistant_attention_count": assistant_attention,
            "pending_share_count": scope_shares_for_moderation(SharedQA.objects.all(), tenant)
            .filter(status=SharedQA.Status.PUBLISHED, is_listed=False)
            .count(),
        },
        "recent_activity": [
            {**row, "at": row["at"].isoformat()} for row in sorted(events, key=lambda row: row["at"], reverse=True)[:8]
        ],
        "setup": {
            "model_configured": model_configured,
            "model_count": models.count(),
            "knowledge_required": knowledge_required,
            "datasource_configured": source_configured,
            "assistant_configured": assistant_configured,
            "complete": model_configured and assistant_configured and not missing_knowledge and not assistant_attention,
            "models": list(
                models.order_by("created_at", "uuid").values("uuid", "provider", name=F("config__model"))[:5]
            ),
            "datasources": list(sources.order_by("name", "uuid").values("uuid", "name")[:5]),
            "connection_count": connections.count(),
            "connections": list(
                connections.order_by("name", "uuid").values("uuid", "name", "plugin_key", "status")[:5]
            ),
            "assistants": list(
                assistants.exclude(status=Assistant.Status.ARCHIVED).order_by("name", "uuid").values("uuid", "name")[:5]
            ),
        },
    }
