"""Neutral host <-> Decision Plugin result contract."""

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class DecisionResult:
    """Neutral decision shape shared by every Decision backend."""

    kind: str
    value: object
    confidence: float | None
    legend: dict | None
    usage: dict


@dataclass(frozen=True)
class RankedCandidate:
    """One ranked candidate with its comparable scalar."""

    label: str
    score: float
    confidence: float | None
    result: DecisionResult


def rank_score(result, target_option=None):
    """Return the comparable scalar of one DecisionResult, or None.

    ``score`` results are ordered by their rubric, so the probability-weighted
    position is comparable.  ``choice`` results are comparable only when the
    analysis names a ``target_option``: the probability of that option is the
    "how good" scalar.
    """

    if result.kind == "choice":
        if not target_option or not isinstance(result.value, dict):
            return None
        probability = result.value.get(target_option)
        if type(probability) not in (int, float):
            return None
        return float(probability)
    if result.kind != "score" or not isinstance(result.value, dict):
        return None
    total = 0.0
    for key, probability in result.value.items():
        try:
            index = int(key)
        except (TypeError, ValueError):
            return None
        if index < 0:
            return None
        total += index * probability
    return total


def rank_tiebreak(result, target_option=None):
    """Return the tie-break probability of one DecisionResult."""

    if result.kind == "choice":
        return rank_score(result, target_option) or 0.0
    if not isinstance(result.value, dict) or not result.value:
        return 0.0
    return max(result.value.values())


def aggregate_ranked(entries, target_option=None):
    """Return candidates sorted by score, then probability, then input order.

    ``entries`` is a sequence of ``(index, label, DecisionResult)``.  Ordering
    is fully deterministic: equal scores break on the dominant probability and
    finally on the caller's input order.
    """

    ranked = []
    for index, label, result in entries:
        score = rank_score(result, target_option)
        if score is None:
            continue
        ranked.append(
            (
                -score,
                -rank_tiebreak(result, target_option),
                index,
                RankedCandidate(label, score, result.confidence, result),
            )
        )
    ranked.sort(key=lambda item: item[:3])
    return [item[3] for item in ranked]


def validate_decision_result(payload, kind):
    """Validate one neutral Decision view produced by a Plugin runtime.

    The Plugin owns the projection from its vendor vocabulary into this
    neutral shape (``kind`` / ``value`` / ``confidence`` / ``legend`` /
    ``usage``); the host only validates it.
    """

    if not isinstance(payload, dict) or payload.get("kind") != kind:
        return None
    usage = payload.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    confidence = payload.get("confidence")
    if type(confidence) not in (int, float) or not math.isfinite(confidence):
        confidence = None
    else:
        confidence = float(confidence)
    if kind == "noul":
        value = _unit_value(payload.get("value"))
        if value is None:
            return None
        return DecisionResult(kind, value, confidence, None, usage)
    probabilities = payload.get("value")
    if not isinstance(probabilities, dict) or not probabilities:
        return None
    normalized = {}
    for key, number in probabilities.items():
        number = _unit_value(number)
        if number is None:
            return None
        normalized[str(key)] = number
    legend = payload.get("legend")
    return DecisionResult(
        kind,
        normalized,
        confidence,
        legend if isinstance(legend, dict) else None,
        usage,
    )


def _unit_value(value):
    """Return one finite probability in [0, 1], or None."""

    if type(value) not in (int, float) or not math.isfinite(value):
        return None
    if not 0 <= value <= 1:
        return None
    return float(value)
