"""Summarize human calibration without treating unreviewed results as correct."""


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
