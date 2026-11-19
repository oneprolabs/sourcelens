"""Keep evaluation answers separate from configuration authority over runtime evidence."""

from collections import defaultdict
from uuid import UUID

from lens.models import DataSource, DataSourceItem, DataSourceVersion, EvaluationResult, Run
from lens.runtime_events import public_run_error

_METRIC_KEYS = {
    "status",
    "outcome",
    "outcome_reference",
    "duration_seconds",
    "model_calls",
    "model_failed",
    "tool_calls",
    "tools_completed",
    "tools_failed",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "cached_tokens",
    "ttft_ms_avg",
    "model_duration_ms",
    "trace_events",
    "last_sequence",
    "platform_version",
    "lensnode_agent_version",
    "scoring_version",
}


def run_evidence_scope(run_ids, tenant_id):
    """Batch-check connected families, including parents and siblings, within one tenant."""
    rows = {}
    pending = set(run_ids)
    while pending:
        found = list(
            Run.objects.filter(pk__in=pending, tenant_id=tenant_id, session__tenant_id=tenant_id).values(
                "pk", "parent_run_id", "session__assistant__tenant_id", "configuration_evidence_restricted"
            )
        )
        rows.update({row["pk"]: row for row in found})
        parents = {row["parent_run_id"] for row in found if row["parent_run_id"]}
        children = (
            set(
                Run.objects.filter(
                    parent_run_id__in=[row["pk"] for row in found], tenant_id=tenant_id, session__tenant_id=tenant_id
                ).values_list("pk", flat=True)
            )
            if found
            else set()
        )
        pending = (parents | children) - rows.keys() - pending
    neighbors = defaultdict(set)
    for pk, row in rows.items():
        parent = row["parent_run_id"]
        if parent in rows:
            neighbors[pk].add(parent)
            neighbors[parent].add(pk)
    restrictions = {}
    for pk in rows:
        if pk in restrictions:
            continue
        component = set()
        frontier = {pk}
        while frontier:
            component.update(frontier)
            frontier = {peer for key in frontier for peer in neighbors[key]} - component
        restricted = any(
            rows[key]["configuration_evidence_restricted"] or rows[key]["session__assistant__tenant_id"] != tenant_id
            for key in component
        )
        restrictions.update(dict.fromkeys(component, restricted))
    return restrictions


def prepare_evaluation_evidence(results):
    """Resolve a complete page together rather than traversing each result's family."""
    groups = defaultdict(list)
    for result in results:
        groups[result.tenant_id].append(result)
    for tenant_id, items in groups.items():
        scope = run_evidence_scope([item.source_run_id for item in items if item.source_run_id], tenant_id)
        for item in items:
            item._evidence_restricted = item.evidence_restricted is True or (
                scope.get(item.source_run_id, True) if item.source_run_id else item.evidence_restricted is not False
            )
    return results


def result_evidence_restricted(result):
    """Unknown retained history cannot establish configuration ownership."""
    if not hasattr(result, "_evidence_restricted"):
        prepare_evaluation_evidence([result])
    return result._evidence_restricted


def preserve_evaluation_evidence(run_ids):
    """Seal access facts before deletion removes a parent, sibling or collaborator link."""
    groups = defaultdict(list)
    for pk, tenant_id in Run.objects.filter(pk__in=run_ids).values_list("pk", "tenant_id"):
        groups[tenant_id].append(pk)
    for tenant_id, ids in groups.items():
        scope = run_evidence_scope(ids, tenant_id)
        restricted = [pk for pk, value in scope.items() if value]
        if restricted:
            Run.objects.filter(pk__in=restricted).update(configuration_evidence_restricted=True)
            EvaluationResult.objects.filter(source_run_id__in=restricted).update(evidence_restricted=True)
        owned = [pk for pk, value in scope.items() if not value]
        EvaluationResult.objects.filter(source_run_id__in=owned, evidence_restricted__isnull=True).update(
            evidence_restricted=False
        )


def public_scores(scores):
    """Preserve numerical verdicts without copying arbitrary runtime detail dictionaries."""
    return {
        name: (
            {
                **{
                    key: value
                    for key, value in row.items()
                    if key in {"score", "passed", "weight", "threshold", "category"}
                },
                "detail": {},
            }
            if isinstance(row, dict)
            else row
        )
        for name, row in (scores or {}).items()
    }


def project_result_evidence(result, data):
    """Use one projection for API results and comparisons while retaining user output."""
    if not result_evidence_restricted(result):
        return data
    projected = dict(data)
    if "trace_summary" in projected:
        projected["trace_summary"] = {}
    if "scores" in projected:
        projected["scores"] = public_scores(projected["scores"])
    if "issues" in projected:
        projected["issues"] = [
            {"code": row.get("code"), "metric": row.get("metric"), "detail": {}}
            for row in projected["issues"] or []
            if isinstance(row, dict)
        ]
    if "execution_metrics" in projected:
        projected["execution_metrics"] = {
            key: value for key, value in (projected["execution_metrics"] or {}).items() if key in _METRIC_KEYS
        }
    if "error" in projected:
        projected["error"] = public_run_error(projected["error"])
    return projected


def _baseline_datasource_scope(baselines, tenant_id):
    """Validate historical resource identifiers together without substituting live versions."""
    rows = []
    for baseline in baselines:
        metadata = baseline.get("metadata")
        if isinstance(metadata, dict) and isinstance(metadata.get("datasources"), list):
            rows.extend(row for row in metadata["datasources"] if isinstance(row, dict))
    if not rows:
        return lambda row: False

    def identifiers(key):
        """Exclude malformed legacy UUIDs before building an ORM lookup."""
        values = set()
        for row in rows:
            try:
                values.add(UUID(str(row.get(key))))
            except (ValueError, TypeError, AttributeError):
                continue
        return values

    sources = {
        str(value)
        for value in DataSource.objects.filter(
            tenant_id=tenant_id, uuid__in=identifiers("datasource_uuid")
        ).values_list("uuid", flat=True)
    }
    items = {
        str(item): str(source)
        for item, source in DataSourceItem.objects.filter(
            datasource__tenant_id=tenant_id, uuid__in=identifiers("item_uuid")
        ).values_list("uuid", "datasource__uuid")
    }
    versions = {
        str(version): str(item)
        for version, item in DataSourceVersion.objects.filter(
            item__datasource__tenant_id=tenant_id, uuid__in=identifiers("version_uuid")
        ).values_list("uuid", "item__uuid")
    }

    def permitted(row):
        """Keep whole-source selections while requiring every supplied child to match."""
        source = str(row.get("datasource_uuid"))
        item = row.get("item_uuid")
        version = row.get("version_uuid")
        return (
            source in sources
            and (not item or items.get(str(item)) == source)
            and (not version or bool(item) and versions.get(str(version)) == str(item))
        )

    return permitted


def project_baseline_evidence(payload, tenant_id):
    """Check baseline provenance everywhere it occurs in a frozen response.

    Legacy baselines without a surviving source retain answer/citations, but
    cannot establish authority over historical configuration metadata.
    """
    baselines = []

    def collect(value):
        """Visit structured baseline fields without interpreting user text."""
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "baseline" and isinstance(child, dict):
                    baselines.append(child)
                else:
                    collect(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                collect(child)

    collect(payload)
    if not baselines:
        return payload
    uuids = set()
    for baseline in baselines:
        try:
            uuids.add(UUID(str(baseline.get("run_uuid"))))
        except (ValueError, TypeError, AttributeError):
            continue
    sources = dict(Run.objects.filter(uuid__in=uuids, tenant_id=tenant_id).values_list("uuid", "pk"))
    scope = run_evidence_scope(sources.values(), tenant_id)
    allowed = {str(uuid) for uuid, pk in sources.items() if scope.get(pk) is False}
    permitted = _baseline_datasource_scope(
        [baseline for baseline in baselines if str(baseline.get("run_uuid")) in allowed], tenant_id
    )

    def project_baseline(baseline):
        """Retain user-visible reference content while filtering configuration metadata."""
        if str(baseline.get("run_uuid")) not in allowed:
            return {**baseline, "metadata": {}}
        metadata = baseline.get("metadata")
        if not isinstance(metadata, dict) or not isinstance(metadata.get("datasources"), list):
            return dict(baseline)
        rows = [row for row in metadata["datasources"] if isinstance(row, dict) and permitted(row)]
        return {**baseline, "metadata": {**metadata, "datasources": rows or None}}

    def project(value):
        """Copy before hiding metadata so ORM JSON snapshots remain immutable."""
        if isinstance(value, dict):
            return {
                key: (project_baseline(child) if key == "baseline" and isinstance(child, dict) else project(child))
                for key, child in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [project(child) for child in value]
        return value

    return project(payload)
