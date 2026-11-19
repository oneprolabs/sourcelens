"""Usage queries operate on immutable SourceLens ownership, never global series."""

from collections import defaultdict

from agentcore_metering.adapters.django.models import LLMUsage
from agentcore_metering.adapters.django.services.usage_aggregation import (
    _bucket_key_for_fill,
    _build_expected_buckets,
    _parse_date,
    _parse_end_date,
)
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate, TruncHour, TruncMonth
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from lens.model_scope import HasModelAccess, model_tenant

TOKEN_FIELDS = ("prompt_tokens", "completion_tokens", "total_tokens", "cached_tokens", "reasoning_tokens")


def usage_queryset(request):
    """Validate filters before applying them to the authorized collection."""

    query = LLMUsage.objects.filter(ownership__isnull=False, ownership__tenant=model_tenant(request))
    params = request.query_params
    for key, parser, lookup in (
        ("start_date", _parse_date, "created_at__gte"),
        ("end_date", _parse_end_date, "created_at__lte"),
    ):
        if params.get(key):
            parsed = parser(params[key])
            if parsed is None:
                raise serializers.ValidationError({key: "Use a valid ISO date."})
            query = query.filter(**{lookup: parsed})
    if params.get("user_id"):
        try:
            user_id = int(params["user_id"])
        except (ValueError, TypeError):
            raise serializers.ValidationError({"user_id": "Use a valid user ID."}) from None
        query = query.filter(user_id=user_id)
    if params.get("model"):
        query = query.filter(model__icontains=params["model"])
    if params.get("success") in {"true", "false"}:
        query = query.filter(success=params["success"] == "true")
    return query


def aggregate_values():
    """Share accounting definitions across totals, models and chart buckets."""

    return {
        **{f"total_{field}" if field != "total_tokens" else field: Sum(field) for field in TOKEN_FIELDS},
        "total_calls": Count("id"),
        "successful_calls": Count("id", filter=Q(success=True)),
        "failed_calls": Count("id", filter=Q(success=False)),
        "total_cost": Sum("cost"),
        "priced_calls": Count("id", filter=Q(cost__isnull=False)),
        "unpriced_calls": Count("id", filter=Q(cost__isnull=True)),
    }


def cost_totals(query):
    """Currencies cannot be added together into a fictitious USD amount."""

    return {
        row["cost_currency"] or "USD": float(row["amount"])
        for row in query.filter(cost__isnull=False).values("cost_currency").annotate(amount=Sum("cost"))
    }


def normalize(row, costs):
    """Keep unknown pricing distinct from a known zero charge."""

    priced, unpriced = row.get("priced_calls", 0), row.get("unpriced_calls", 0)
    row["cost_status"] = (
        "partial" if priced and unpriced else "priced" if priced else "unavailable" if unpriced else "empty"
    )
    row["cost_totals"] = costs
    row["total_cost_currency"] = next(iter(costs), "USD") if len(costs) <= 1 else "MIXED"
    row["total_cost"] = next(iter(costs.values()), 0) if len(costs) <= 1 else None
    for key in aggregate_values():
        if key != "total_cost":
            row[key] = row.get(key) or 0
    return row


def performance_series(query, trunc, granularity):
    """Average measured timings per bucket and model using only authorized rows."""

    groups = defaultdict(lambda: defaultdict(lambda: [0.0, 0]))
    rows = query.annotate(bucket=trunc("created_at")).values(
        "bucket", "model", "created_at", "started_at", "first_chunk_at", "is_streaming", "completion_tokens"
    )
    for row in rows.iterator(chunk_size=2000):
        if not row["started_at"]:
            continue
        key = (_bucket_key_for_fill(row["bucket"], granularity), row["model"])
        duration = max(0.0, (row["created_at"] - row["started_at"]).total_seconds())
        metrics = {"avg_e2e_latency_sec": duration}
        if row["is_streaming"] and row["first_chunk_at"]:
            metrics["avg_ttft_sec"] = max(0.0, (row["first_chunk_at"] - row["started_at"]).total_seconds())
        if duration > 0:
            metrics["avg_output_tps"] = (row["completion_tokens"] or 0) / duration
        for metric, value in metrics.items():
            groups[key][metric][0] += value
            groups[key][metric][1] += 1
    return {
        key: {metric: round(total / count, 4) for metric, (total, count) in metrics.items()}
        for key, metrics in groups.items()
    }


class ModelUsageView(APIView):
    """Paginate safe usage fields inside a single billing scope."""

    permission_classes = [HasModelAccess]

    def get(self, request):
        """Do not expose raw provider errors, prompts or arbitrary tracker metadata."""

        query = usage_queryset(request).select_related("user").order_by("-created_at", "pk")
        try:
            page = max(1, int(request.query_params.get("page", 1)))
            size = min(100, max(1, int(request.query_params.get("page_size", 20))))
        except (ValueError, TypeError):
            raise serializers.ValidationError("Use valid pagination numbers.") from None
        items = []
        offset = (page - 1) * size
        for usage in query[offset : offset + size]:
            latency = max(0, (usage.created_at - usage.started_at).total_seconds()) if usage.started_at else None
            metadata = usage.metadata or {}
            safe_keys = {
                "run_uuid",
                "datasource_uuid",
                "task_id",
                "model_call_id",
                "primary_model_ref",
                "selected_model_ref",
                "fallback_attempt",
                "node_name",
            }
            item = {
                "id": str(usage.pk),
                "user_id": usage.user_id,
                "username": usage.user.username if usage.user else None,
                "model": usage.model,
                **{key: getattr(usage, key) for key in TOKEN_FIELDS},
                "cost": float(usage.cost) if usage.cost is not None else None,
                "cost_currency": usage.cost_currency or "USD",
                "success": usage.success,
                "error": "" if usage.success else "Model call failed.",
                "created_at": usage.created_at.isoformat(),
                "started_at": usage.started_at.isoformat() if usage.started_at else None,
                "latency_sec": latency,
                "e2e_latency_sec": latency,
                "output_tps": round(usage.completion_tokens / latency, 2) if latency else None,
                "metadata": {key: value for key, value in metadata.items() if key in safe_keys},
            }
            if usage.is_streaming and usage.first_chunk_at and usage.started_at:
                item["ttft_sec"] = max(0, (usage.first_chunk_at - usage.started_at).total_seconds())
            items.append(item)
        return Response({"results": items, "total": query.count(), "page": page, "page_size": size})


class ModelStatsView(APIView):
    """Aggregate authorized raw rows; the vendor's global series has no tenant key."""

    permission_classes = [HasModelAccess]

    def get(self, request):
        """Return scope-local summary, per-model totals and chart buckets."""

        query = usage_queryset(request)
        values = aggregate_values()
        summary = normalize(query.aggregate(**values), cost_totals(query))
        models = list(query.values("model").annotate(**values).order_by("-total_tokens"))
        model_costs = defaultdict(dict)
        for row in query.filter(cost__isnull=False).values("model", "cost_currency").annotate(amount=Sum("cost")):
            model_costs[row["model"]][row["cost_currency"] or "USD"] = float(row["amount"])
        for row in models:
            normalize(row, model_costs[row["model"]])
        data = {"summary": summary, "by_model": models, "series": None, "expected_buckets": [], "series_by_model": []}
        granularity = request.query_params.get("granularity")
        if granularity:
            trunc = {"day": TruncHour, "month": TruncDate, "year": TruncMonth}.get(granularity)
            if trunc is None:
                raise serializers.ValidationError({"granularity": "Use day, month or year."})
            start = _parse_date(request.query_params.get("start_date"))
            end = _parse_end_date(request.query_params.get("end_date"))
            if start and end:
                if end < start or (end - start).days > 366:
                    raise serializers.ValidationError("Choose an ordered date range of at most 366 days.")
                expected = [
                    _bucket_key_for_fill(bucket, granularity)
                    for bucket in _build_expected_buckets(granularity, start, end)
                ]
                data["expected_buckets"] = expected
                rows = list(
                    query.annotate(bucket=trunc("created_at"))
                    .values("bucket", "model", "cost_currency")
                    .annotate(**values)
                    .order_by("bucket")
                )
                buckets = defaultdict(list)
                by_model = defaultdict(list)
                for row in rows:
                    row["bucket"] = _bucket_key_for_fill(row["bucket"], granularity)
                    buckets[row["bucket"]].append(row)
                    by_model[(row["bucket"], row["model"])].append(row)

                def merge(rows):
                    """Merge currency partitions without losing currency identity."""

                    totals = {key: sum(row[key] or 0 for row in rows) for key in values if key != "total_cost"}
                    costs = defaultdict(float)
                    for row in rows:
                        if row["total_cost"] is not None:
                            costs[row["cost_currency"] or "USD"] += float(row["total_cost"])
                    return normalize(totals, dict(costs))

                data["series"] = {
                    "granularity": granularity,
                    "items": [{"bucket": bucket, **merge(buckets[bucket])} for bucket in expected],
                }
                timings = performance_series(query, trunc, granularity)
                data["series_by_model"] = [
                    {"bucket": bucket, "model": model, **merge(rows), **timings.get((bucket, model), {})}
                    for (bucket, model), rows in by_model.items()
                ]
        return Response(data)


class ModelUsersView(APIView):
    """List only actors with usage in the selected billing scope."""

    permission_classes = [HasModelAccess]

    def get(self, request):
        """Keep user-filter discovery from revealing other tenants."""

        rows = usage_queryset(request).exclude(user_id=None).values("user_id", "user__username").distinct()
        return Response([{"id": row["user_id"], "username": row["user__username"]} for row in rows])
