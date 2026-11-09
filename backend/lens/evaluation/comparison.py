"""Compare frozen evaluation results without inferring statistical significance."""


def compare_batches(left, right):
    """Return paired case results and explicit comparability limitations."""

    a = left.config_snapshot or {}
    b = right.config_snapshot or {}
    ad = a.get("dataset") or {}
    bd = b.get("dataset") or {}
    reasons = []
    if left.dataset_id != right.dataset_id:
        reasons.append("different_dataset")
    if ad.get("revision") is None or bd.get("revision") is None:
        reasons.append("dataset_revision_unknown")
    elif ad.get("revision") != bd.get("revision"):
        reasons.append("different_dataset_revision")
    if not a.get("scoring_version") or not b.get("scoring_version"):
        reasons.append("scoring_version_unknown")
    elif a.get("scoring_version") != b.get("scoring_version"):
        reasons.append("different_scoring_version")
    if not a.get("metrics") or not b.get("metrics"):
        reasons.append("metrics_unknown")
    elif a["metrics"] != b["metrics"]:
        reasons.append("different_metrics")
    if set(a.get("case_uuids") or []) != set(b.get("case_uuids") or []):
        reasons.append("different_case_selection")
    if left.status != "completed" or right.status != "completed":
        reasons.append("batch_not_completed")
    reasons.append("runtime_environment_not_fully_frozen")
    left_results = {str(result.case_id): result for result in left.results.all()}
    right_results = {str(result.case_id): result for result in right.results.all()}
    ids = sorted(set(left_results) | set(right_results))

    def item(result):
        if result is None:
            return None
        return {
            "passed": result.passed,
            "output": result.output_snapshot,
            "scores": result.scores,
            "execution_metrics": result.execution_metrics,
            "error": result.error,
        }

    return {
        "left": str(left.uuid),
        "right": str(right.uuid),
        "fully_comparable": False,
        "limitations": reasons,
        "cases": [
            {"case_uuid": key, "left": item(left_results.get(key)), "right": item(right_results.get(key))}
            for key in ids
        ],
    }
