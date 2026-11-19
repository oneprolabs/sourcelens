"""Per-tenant usage statistics for the platform console."""

from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Sum
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import Tenant
from accounts.permissions import HasPlatformSuperAdmin
from lens.models import Run


def _percentile(values, ratio):
    """Return the nearest-rank percentile of a numeric list."""
    if not values:
        return None
    ordered = sorted(values)
    index = int(len(ordered) * ratio + 0.9999) - 1
    return ordered[max(min(index, len(ordered) - 1), 0)]


def _duration_ms(started_at, finished_at):
    """Return one run's wall-clock duration in milliseconds."""
    if not started_at or not finished_at:
        return None
    delta = finished_at - started_at
    return max(int(delta.total_seconds() * 1000), 0)


def _usage_by_tenant(cutoff):
    """Aggregate immutable billing ownership rather than current membership."""
    result = {}
    try:
        from agentcore_metering.adapters.django.models import LLMUsage
    except Exception:
        return result
    grouped = (
        LLMUsage.objects.filter(
            created_at__gte=cutoff,
            ownership__tenant__isnull=False,
        )
        .values("ownership__tenant_id", "cost_currency")
        .annotate(
            tokens=Sum("total_tokens"),
            cost=Sum("cost"),
        )
    )
    for row in grouped:
        tenant_id = row["ownership__tenant_id"]
        if not tenant_id:
            continue
        bucket = result.setdefault(
            str(tenant_id),
            {"tokens": 0, "costs": {}},
        )
        bucket["tokens"] += int(row["tokens"] or 0)
        currency = row["cost_currency"] or "USD"
        if row["cost"] is not None:
            bucket["costs"][currency] = bucket["costs"].get(currency, Decimal("0")) + Decimal(row["cost"])
    return result


class PlatformTenantStatsView(APIView):
    """Return per-tenant usage and run-health metrics."""

    permission_classes = [HasPlatformSuperAdmin]

    def get(self, request):
        """Return one statistics row per tenant for the requested window."""
        try:
            days = int(request.query_params.get("days") or 7)
        except (TypeError, ValueError):
            days = 7
        days = min(max(days, 1), 90)
        now = timezone.now()
        cutoff = now - timedelta(days=days)

        tenants = list(
            Tenant.objects.annotate(
                member_count=Count("memberships", distinct=True),
                assistant_count=Count("assistants", distinct=True),
                datasource_count=Count("datasources", distinct=True),
            ).order_by("name", "uuid")
        )
        stats = {
            str(tenant.uuid): {
                "uuid": str(tenant.uuid),
                "name": tenant.name,
                "slug": tenant.slug,
                "status": tenant.status,
                "member_count": tenant.member_count,
                "assistant_count": tenant.assistant_count,
                "datasource_count": tenant.datasource_count,
                "active_member_count": 0,
                "run_count": 0,
                "failed_count": 0,
                "running_count": 0,
                "fail_rate": None,
                "p95_duration_ms": None,
                "avg_duration_ms": None,
                "like_rate": None,
                "tokens": 0,
                "costs": {},
                "last_active_at": None,
            }
            for tenant in tenants
        }

        runs = Run.objects.filter(created_at__gte=cutoff, created_at__lte=now, source=Run.Source.USER).values(
            "tenant_id",
            "status",
            "outcome",
            "feedback",
            "started_at",
            "finished_at",
            "created_at",
            "session__user_id",
        )
        durations = {}
        positive = {}
        negative = {}
        active_members = {}
        for run in runs:
            key = str(run["tenant_id"]) if run["tenant_id"] else None
            row = stats.get(key)
            if row is None:
                continue
            row["run_count"] += 1
            if run["status"] == Run.Status.FAILED:
                row["failed_count"] += 1
            if run["status"] in {
                Run.Status.QUEUED,
                Run.Status.RUNNING,
                Run.Status.STREAMING,
                Run.Status.AWAITING_USER_INPUT,
            }:
                row["running_count"] += 1
            if run["session__user_id"]:
                active_members.setdefault(key, set()).add(run["session__user_id"])
            if run["feedback"] == Run.Feedback.POSITIVE:
                positive[key] = positive.get(key, 0) + 1
            elif run["feedback"] == Run.Feedback.NEGATIVE:
                negative[key] = negative.get(key, 0) + 1
            duration = _duration_ms(run["started_at"], run["finished_at"])
            if duration is not None:
                durations.setdefault(key, []).append(duration)
            created_at = run["created_at"]
            if created_at and (row["last_active_at"] is None or created_at > row["last_active_at"]):
                row["last_active_at"] = created_at

        for key, row in stats.items():
            if row["run_count"]:
                row["fail_rate"] = round(
                    row["failed_count"] / row["run_count"] * 100,
                    1,
                )
            values = durations.get(key) or []
            if values:
                row["p95_duration_ms"] = _percentile(values, 0.95)
                row["avg_duration_ms"] = int(sum(values) / len(values))
            votes = positive.get(key, 0) + negative.get(key, 0)
            if votes:
                row["like_rate"] = round(
                    positive.get(key, 0) / votes * 100,
                    1,
                )
            row["active_member_count"] = len(active_members.get(key, ()))
            if row["last_active_at"] is not None:
                row["last_active_at"] = row["last_active_at"].isoformat()

        for key, usage in _usage_by_tenant(cutoff).items():
            row = stats.get(key)
            if row is None:
                continue
            row["tokens"] = usage["tokens"]
            row["costs"] = {currency: float(amount) for currency, amount in usage["costs"].items()}

        return Response(
            {
                "days": days,
                "results": list(stats.values()),
            }
        )
