"""Metric configuration, per-case scoring and batch aggregation."""

from lens.evaluation.evaluators import derive_run_quality
from lens.evaluation.evaluators.citation import citation_integrity_metric
from lens.evaluation.evaluators.groundedness import groundedness_metric
from lens.evaluation.evaluators.rule_based import (
    execution_metric,
    latency_metric,
    tool_success_metric,
)
from lens.evaluation.evaluators.trace import load_trace_events, summarize_trace

DEFAULT_METRICS = (
    {
        "name": "execution_success",
        "category": "correctness",
        "evaluator_type": "outcome",
        "weight": 0.35,
        "threshold": 1.0,
        "config": {},
    },
    {
        "name": "tool_success_rate",
        "category": "reliability",
        "evaluator_type": "tool_success",
        "weight": 0.2,
        "threshold": 0.9,
        "config": {},
    },
    {
        "name": "citation_integrity",
        "category": "groundedness",
        "evaluator_type": "citation_integrity",
        "weight": 0.2,
        "threshold": 0.8,
        "config": {},
    },
    {
        "name": "evidence_verified",
        "category": "groundedness",
        "evaluator_type": "groundedness",
        "weight": 0.15,
        "threshold": 1.0,
        "config": {},
    },
    {
        "name": "latency_budget",
        "category": "efficiency",
        "evaluator_type": "latency",
        "weight": 0.1,
        "threshold": 0.5,
        "config": {"budget_seconds": 600},
    },
)

_INFORMATIONAL_TYPES = {"outcome"}


def _metric_row_to_config(row):
    """Return a plain metric configuration from a persisted metric row."""

    return {
        "name": row.name,
        "category": row.category,
        "evaluator_type": row.evaluator_type,
        "weight": float(row.weight),
        "threshold": None if row.threshold is None else float(row.threshold),
        "config": row.config or {},
    }


def resolve_metric_configs(metric_rows=None):
    """Return enabled metric configs, falling back to the built-in set."""

    rows = [row for row in (metric_rows or []) if getattr(row, "enabled", True)]
    if not rows:
        return [dict(item) for item in DEFAULT_METRICS]
    return [_metric_row_to_config(row) for row in rows]


def metrics_snapshot(metric_configs):
    """Return the frozen metric definition list stored on an evaluation run."""

    return [
        {
            "name": item["name"],
            "category": item["category"],
            "evaluator_type": item["evaluator_type"],
            "weight": item["weight"],
            "threshold": item["threshold"],
            "config": item["config"],
        }
        for item in metric_configs
    ]


def _run_duration_seconds(run):
    """Return wall-clock execution seconds for one run."""

    if not run.started_at or not run.finished_at:
        return None
    return max((run.finished_at - run.started_at).total_seconds(), 0.0)


def _evaluate_metric(metric, *, run, case, summary, known_paths):
    """Dispatch one metric to its deterministic evaluator."""

    evaluator_type = metric.get("evaluator_type")
    if evaluator_type == "outcome":
        return execution_metric(derive_run_quality(run))
    if evaluator_type == "tool_success":
        return tool_success_metric(summary)
    if evaluator_type == "citation_integrity":
        return citation_integrity_metric(
            run.citations or [],
            expected_sources=case.expected_sources or [],
            known_paths=known_paths,
        )
    if evaluator_type == "groundedness":
        return groundedness_metric(
            summary,
            planned_evidence=run.planned_evidence or {},
        )
    if evaluator_type == "latency":
        return latency_metric(_run_duration_seconds(run), metric.get("config"))
    return {"score": None, "detail": {"reason": "unsupported_evaluator"}}


def _metric_passed(metric, outcome):
    """Return the conclusive pass flag for one metric result."""

    if "passed" in outcome:
        return outcome.get("passed")
    score = outcome.get("score")
    if score is None:
        return None
    threshold = metric.get("threshold")
    if threshold is None:
        return None
    return float(score) >= float(threshold)


def evaluate_case_result(run, case, metric_configs, *, known_paths=None):
    """Return scores, pass verdict and issues for one executed case.

    The execution metrics stay separate from the weighted scores so token and
    latency evidence remains visible even when a dimension is not scored.
    """

    events = load_trace_events(run)
    summary = summarize_trace(events)
    scores = {}
    issues = []
    verdicts = []
    for metric in metric_configs:
        name = metric["name"]
        outcome = _evaluate_metric(
            metric,
            run=run,
            case=case,
            summary=summary,
            known_paths=known_paths,
        )
        passed = _metric_passed(metric, outcome)
        scores[name] = {
            "score": outcome.get("score"),
            "passed": passed,
            "weight": metric.get("weight"),
            "threshold": metric.get("threshold"),
            "category": metric.get("category"),
            "detail": outcome.get("detail") or {},
        }
        verdicts.append((name, passed))
        if passed is False:
            issues.append(
                {
                    "metric": name,
                    "code": f"{name}_below_threshold",
                    "detail": outcome.get("detail") or {},
                }
            )

    expected_answer = (getattr(case, "expected_answer", "") or "").strip()
    expected_facts = getattr(case, "expected_facts", None) or []
    output = (run.output_message.content if run.output_message_id else "") or ""
    checks = []
    if expected_answer:
        checks.append(output.strip().casefold() == expected_answer.casefold())
    checks.extend(str(fact).strip().casefold() in output.casefold() for fact in expected_facts if str(fact).strip())
    correctness = all(checks) if checks else None
    scores["answer_correctness"] = {"score": None if correctness is None else float(correctness),
        "passed": correctness, "weight": 0, "threshold": 1.0, "category": "correctness", "detail": {}}
    if correctness is False:
        issues.append({"metric": "answer_correctness", "code": "answer_correctness_below_threshold", "detail": {}})
    failed = correctness is False or any(passed is False for _name, passed in verdicts)
    outcome_passed = next(
        (passed for metric, (_name, passed) in zip(metric_configs, verdicts) if metric.get("evaluator_type") == "outcome"),
        None,
    )
    if failed:
        passed = False
    elif outcome_passed is True and correctness is True:
        passed = True
    else:
        passed = None

    execution_metrics = {
        "status": run.status,
        "outcome": run.outcome or "",
        "duration_seconds": _run_duration_seconds(run),
        "model_calls": summary["model_calls"],
        "model_failed": summary["model_failed"],
        "tool_calls": summary["tool_started"],
        "tools_completed": summary["tools_completed"],
        "tools_failed": summary["tools_failed"],
        "prompt_tokens": summary["prompt_tokens"],
        "completion_tokens": summary["completion_tokens"],
        "total_tokens": summary["total_tokens"],
        "cached_tokens": summary["cached_tokens"],
        "ttft_ms_avg": summary["ttft_ms_avg"],
        "model_duration_ms": summary["model_duration_ms"],
        "trace_events": len(events),
    }
    return {
        "scores": scores,
        "passed": passed,
        "issues": issues,
        "execution_metrics": execution_metrics,
    }


def aggregate_results(results, metric_configs):
    """Return the weighted batch score, coverage and pass distribution."""

    results = list(results)
    per_metric = {}
    for metric in metric_configs:
        per_metric[metric["name"]] = {
            "category": metric.get("category"),
            "weight": metric.get("weight"),
            "scored": 0,
            "passed": 0,
            "failed": 0,
            "score_sum": 0.0,
        }

    passed_cases = 0
    failed_cases = 0
    inconclusive_cases = 0
    for result in results:
        if result.passed is True:
            passed_cases += 1
        elif result.passed is False:
            failed_cases += 1
        else:
            inconclusive_cases += 1
        scores = result.scores if isinstance(result.scores, dict) else {}
        for name, entry in scores.items():
            bucket = per_metric.get(name)
            if bucket is None or not isinstance(entry, dict):
                continue
            score = entry.get("score")
            if isinstance(score, (int, float)) and not isinstance(score, bool):
                bucket["scored"] += 1
                bucket["score_sum"] += float(score)
            if entry.get("passed") is True:
                bucket["passed"] += 1
            elif entry.get("passed") is False:
                bucket["failed"] += 1

    weighted_sum = 0.0
    weight_total = 0.0
    for name, bucket in per_metric.items():
        if not bucket["scored"]:
            bucket["mean_score"] = None
            continue
        mean_score = bucket["score_sum"] / bucket["scored"]
        bucket["mean_score"] = round(mean_score, 4)
        weight = bucket["weight"] or 0.0
        if weight > 0:
            weighted_sum += mean_score * weight
            weight_total += weight
    for bucket in per_metric.values():
        bucket.pop("score_sum", None)

    total_cases = len(results)
    scored_metrics = sum(1 for bucket in per_metric.values() if bucket["scored"])
    coverage = {
        "total_cases": total_cases,
        "scored_metrics": scored_metrics,
        "expected_metrics": len(per_metric),
        "ratio": round(scored_metrics / len(per_metric), 4) if per_metric else 0.0,
        "per_metric": {
            name: {
                "scored_cases": bucket["scored"],
                "total_cases": total_cases,
                "mean_score": bucket["mean_score"],
            }
            for name, bucket in per_metric.items()
        },
    }
    aggregate_score = round(weighted_sum / weight_total, 4) if weight_total > 0 else None
    return {
        "aggregate_score": aggregate_score,
        "coverage": coverage,
        "per_metric": per_metric,
        "passed_cases": passed_cases,
        "failed_cases": failed_cases,
        "inconclusive_cases": inconclusive_cases,
    }
