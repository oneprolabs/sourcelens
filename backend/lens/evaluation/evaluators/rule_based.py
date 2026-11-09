"""Deterministic rule-based metrics derived from Run evidence."""

DEFAULT_LATENCY_BUDGET_SECONDS = 600.0


def tool_success_metric(summary):
    """Return the share of started tool calls that completed without error.

    ``tool_started`` is the denominator so calls that never reported a terminal
    event (run cancelled or trace truncated) count as failures instead of
    disappearing from the ratio.
    """

    started = int(summary.get("tool_started") or 0)
    completed = int(summary.get("tools_completed") or 0)
    failed = int(summary.get("tools_failed") or 0)
    total = max(started, completed + failed)
    if total == 0:
        return {
            "score": None,
            "detail": {"reason": "no_tool_calls", "completed": 0, "failed": 0, "started": 0},
        }
    unfinished = max(total - completed - failed, 0)
    return {
        "score": round(completed / total, 4),
        "detail": {
            "started": started,
            "completed": completed,
            "failed": failed,
            "unfinished": unfinished,
            "total": total,
        },
    }


def latency_metric(duration_seconds, config=None):
    """Return a latency score that decays as wall time exceeds the budget."""

    config = config if isinstance(config, dict) else {}
    budget = config.get("budget_seconds", DEFAULT_LATENCY_BUDGET_SECONDS)
    if not isinstance(budget, (int, float)) or isinstance(budget, bool) or budget <= 0:
        budget = DEFAULT_LATENCY_BUDGET_SECONDS
    if not isinstance(duration_seconds, (int, float)) or duration_seconds <= 0:
        return {
            "score": None,
            "detail": {"reason": "duration_unavailable", "budget_seconds": budget},
        }
    score = min(1.0, float(budget) / float(duration_seconds))
    return {
        "score": round(score, 4),
        "detail": {
            "duration_seconds": round(float(duration_seconds), 3),
            "budget_seconds": budget,
        },
    }


def execution_metric(quality):
    """Return the outcome verdict as a scored metric."""

    passed = quality.get("passed")
    return {
        "score": None if passed is None else (1.0 if passed else 0.0),
        "passed": passed,
        "detail": {
            "status": quality.get("status"),
            "reason": quality.get("reason"),
        },
    }
