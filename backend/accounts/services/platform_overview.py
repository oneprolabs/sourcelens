"""Aggregate platform health without exposing tenant conversations or credentials."""

from datetime import timedelta
from datetime import timezone as datetime_timezone

from agentcore_metering.adapters.django.models import LLMUsage
from agentcore_notifier.adapters.django.models import NotificationRecord
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from accounts.models import Tenant, TenantMembership
from lens.models import Assistant, DataSource, LensNode, Run


def platform_overview():
    """Build bounded dashboard summaries from immutable billing and user requests."""

    now = timezone.now()
    cutoff = now - timedelta(days=7)
    previous = cutoff - timedelta(days=7)
    today = now.astimezone(datetime_timezone.utc).date()
    first_day = today - timedelta(days=29)
    first_start = now.astimezone(datetime_timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
        days=29
    )
    questions = Run.objects.filter(source=Run.Source.USER, created_at__lte=now)
    recent_questions = questions.filter(created_at__gte=cutoff)
    current_count = recent_questions.count()
    previous_count = questions.filter(created_at__gte=previous, created_at__lt=cutoff).count()
    daily = {
        row["day"]: row["count"]
        for row in questions.filter(created_at__gte=first_start)
        .annotate(day=TruncDate("created_at", tzinfo=datetime_timezone.utc))
        .values("day")
        .annotate(count=Count("pk"))
    }
    ranking = Tenant.objects.annotate(
        run_count=Count(
            "runs",
            filter=Q(runs__source=Run.Source.USER, runs__created_at__gte=cutoff, runs__created_at__lte=now),
        )
    ).order_by("-run_count", "name", "uuid")[:5]
    usage = LLMUsage.objects.filter(created_at__gte=cutoff, created_at__lte=now, ownership__isnull=False)
    costs = {
        row["cost_currency"] or "UNKNOWN": float(row["amount"])
        for row in usage.filter(cost__isnull=False).values("cost_currency").annotate(amount=Sum("cost"))
    }
    model_totals = usage.values("model").annotate(tokens=Sum("total_tokens")).filter(tokens__gt=0)
    models = list(model_totals.order_by("-tokens", "model")[:5])
    remaining = model_totals.exclude(model__in=[row["model"] for row in models]).aggregate(tokens=Sum("total_tokens"))
    if remaining["tokens"]:
        models.append({"model": "", "tokens": remaining["tokens"], "other": True})
    nodes = LensNode.objects.filter(enrollment_status=LensNode.EnrollmentStatus.APPROVED, token_revoked=False)
    tenants = Tenant.objects.all()
    return {
        "generated_at": now.isoformat(),
        "timezone": "UTC",
        "tenant_count": tenants.count(),
        "active_tenant_count": tenants.filter(status=Tenant.Status.ACTIVE).count(),
        "engaged_tenant_count_7d": recent_questions.filter(tenant__isnull=False).values("tenant_id").distinct().count(),
        "pending_tenant_count": tenants.filter(status=Tenant.Status.PENDING).count(),
        "suspended_tenant_count": tenants.filter(status=Tenant.Status.SUSPENDED).count(),
        "member_count": TenantMembership.objects.filter(status=TenantMembership.Status.ACTIVE).count(),
        "assistant_count": Assistant.objects.count(),
        "datasource_count": DataSource.objects.count(),
        "run_count_7d": current_count,
        "run_count_previous_7d": previous_count,
        "run_change_percent": (
            round((current_count - previous_count) * 100 / previous_count, 1) if previous_count else None
        ),
        "question_series_30d": [
            {
                "date": (first_day + timedelta(days=offset)).isoformat(),
                "count": daily.get(first_day + timedelta(days=offset), 0),
            }
            for offset in range(30)
        ],
        "top_tenants_7d": [
            {"uuid": str(tenant.uuid), "name": tenant.name, "run_count": tenant.run_count} for tenant in ranking
        ],
        "tokens_7d": usage.aggregate(value=Sum("total_tokens"))["value"] or 0,
        "costs_7d": costs,
        "unpriced_calls_7d": usage.filter(cost__isnull=True).count(),
        "model_usage_7d": models,
        "node_online_count": nodes.filter(status=LensNode.Status.ONLINE).count(),
        "node_total_count": nodes.count(),
        "attention": {
            "notification_start_at": cutoff.isoformat(),
            "notification_end_at": now.isoformat(),
            "offline_node_count": nodes.filter(
                status__in=[LensNode.Status.OFFLINE, LensNode.Status.UNRESPONSIVE]
            ).count(),
            "notification_failure_count_7d": NotificationRecord.objects.filter(
                created_at__gte=cutoff,
                created_at__lte=now,
                status="failed",
                ownership__isnull=False,
                ownership__tenant__isnull=True,
            ).count(),
            "stale_pending_tenant_count": tenants.filter(status=Tenant.Status.PENDING, created_at__lte=cutoff).count(),
        },
    }
