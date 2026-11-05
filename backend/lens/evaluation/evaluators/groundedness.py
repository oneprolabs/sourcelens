"""Groundedness scoring from persisted evidence verification events."""

_ACTION_SCORES = {
    "accept": 1.0,
    "not_requested": 1.0,
    "accept_best_effort": 0.5,
    "recheck": 0.0,
    "inconclusive": 0.0,
}


def groundedness_metric(summary, planned_evidence=None):
    """Return the latest evidence verdict as a bounded score.

    A run that never emitted a verification event is inconclusive rather than
    failed: the route may legitimately require no evidence.
    """

    actions = summary.get("evidence_verifications") or []
    if not actions:
        planned = planned_evidence if isinstance(planned_evidence, dict) else {}
        sufficient = planned.get("sufficient")
        if isinstance(sufficient, bool):
            return {
                "score": 1.0 if sufficient else 0.0,
                "detail": {
                    "source": "planned_evidence",
                    "sufficient": sufficient,
                    "gaps": planned.get("gap_categories") or [],
                },
            }
        return {
            "score": None,
            "detail": {"reason": "no_verification_events", "actions": []},
        }
    last = actions[-1]
    score = _ACTION_SCORES.get(last)
    if score is None:
        return {
            "score": None,
            "detail": {"reason": "unknown_action", "actions": actions},
        }
    return {
        "score": score,
        "detail": {"actions": actions, "latest": last},
    }
