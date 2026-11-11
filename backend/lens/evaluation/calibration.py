"""Summarize human calibration without treating unreviewed results as correct."""

# A Judge may only gate quality once humans have reviewed enough samples and
# agreed often enough. Both numbers are intentionally conservative defaults.
MIN_CALIBRATION_SAMPLES = 20
MIN_AGREEMENT_RATE = 0.8


def judge_gate(summary, configured):
    """Return whether human calibration allows the Judge to gate quality.

    The answer dimension drives the gate because that verdict feeds the quality
    score. Evidence agreement is reported alongside it as secondary evidence.
    """

    dimensions = (summary or {}).get("dimensions") or {}
    answer = dimensions.get("answer") or {}
    evidence = dimensions.get("evidence") or {}
    comparable = int(answer.get("comparable_reviews") or 0)
    rate = answer.get("agreement_rate")
    if not configured:
        status = "not_configured"
    elif comparable < MIN_CALIBRATION_SAMPLES:
        status = "insufficient_samples"
    elif rate is None or float(rate) < MIN_AGREEMENT_RATE:
        status = "below_threshold"
    else:
        status = "ok"
    return {
        "status": status,
        "min_samples": MIN_CALIBRATION_SAMPLES,
        "min_agreement": MIN_AGREEMENT_RATE,
        "answer": {
            "comparable_reviews": comparable,
            "agreement_rate": rate,
        },
        "evidence": {
            "comparable_reviews": int(evidence.get("comparable_reviews") or 0),
            "agreement_rate": evidence.get("agreement_rate"),
        },
    }


def summarize_calibration(results):
    """Count current-result human reviews and their agreement with recorded Judge labels.

    A review is eligible only while its frozen output, Run identity, and Judge
    match the current result. Multiple reviews remain visible; the latest per
    reviewer is used to avoid counting repeated revisions as independent votes.
    """

    summary = {
        key: {"reviewed_results": 0, "comparable_reviews": 0, "agreed": 0, "disagreed": 0, "agreement_rate": None}
        for key in ("answer", "evidence")
    }
    stale_reviews = 0
    total_results = 0
    for result in results:
        total_results += 1
        latest = {}
        for review in result.calibrations.all():
            context = review.context_snapshot or {}
            run_uuid = str(result.source_run_uuid) if result.source_run_uuid else None
            if (
                review.reviewer_id is None
                or context.get("source_run_uuid") != run_uuid
                or context.get("output") != result.output_snapshot
                or context.get("scores") != result.scores
                or context.get("expectation")
                != (result.evaluation_run.config_snapshot or {}).get("case_expectations", {}).get(str(result.case_id))
                or review.judge_snapshot != (result.judge_evidence or {})
            ):
                stale_reviews += 1
                continue
            latest[review.reviewer_id] = review
        for dimension in ("answer", "evidence"):
            data = summary[dimension]
            if latest:
                data["reviewed_results"] += 1
            for review in latest.values():
                human = getattr(review, f"{dimension}_verdict")
                judge = (review.judge_snapshot or {}).get(dimension)
                judge = judge.get("verdict") if isinstance(judge, dict) else None
                if human not in ("pass", "fail") or judge not in ("pass", "fail"):
                    continue
                data["comparable_reviews"] += 1
                if human == judge:
                    data["agreed"] += 1
                else:
                    data["disagreed"] += 1
    for data in summary.values():
        n = data["comparable_reviews"]
        if n:
            data["agreement_rate"] = round(data["agreed"] / n, 4)
    return {"total_results": total_results, "stale_reviews": stale_reviews, "dimensions": summary}
