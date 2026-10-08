"""Bound answer-review inputs without dropping whole evidence sources silently."""

import json


def review_material(question, answer, evidence=None, limit=4000):
    """Reserve space for evidence and emit valid JSON with explicit coverage."""

    evidence = evidence if isinstance(evidence, dict) else {}
    payload = {
        "review_type": "answer_grounding",
        "question": str(question or ""),
        "answer": str(answer or ""),
        "evidence_completeness": evidence.get("evidence_completeness", "complete"),
        "runtime_evidence": {
            key: value for key, value in evidence.items() if not key.startswith("_") and key != "evidence_completeness"
        },
    }
    if not payload["runtime_evidence"]:
        payload["evidence_completeness"] = "incomplete"

    def encode():
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)

    def strings(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if isinstance(item, str) and key not in {"tool", "evidence_completeness", "review_type"}:
                    yield value, key, item
                elif isinstance(item, (dict, list)):
                    yield from strings(item)
        elif isinstance(value, list):
            for item in value:
                yield from strings(item)

    # Copy nested evidence before shrinking so runtime/checkpoint state stays intact.
    payload = json.loads(encode())
    while len(encode()) > limit:
        payload["evidence_completeness"] = "incomplete"
        entries = sorted(strings(payload), key=lambda entry: len(entry[2]), reverse=True)
        if entries and len(entries[0][2]) > 80:
            parent, key, value = entries[0]
            parent[key] = value[: max(80, len(value) // 2)]
            continue
        retrieved = payload["runtime_evidence"].get("retrieved_evidence")
        if isinstance(retrieved, list) and retrieved:
            retrieved.pop()
            continue
        if payload["runtime_evidence"]:
            payload["runtime_evidence"].pop(next(reversed(payload["runtime_evidence"])))
            continue
        # Tiny custom limits cannot carry a useful review; the caller must skip it.
        return json.dumps({"evidence_completeness": "incomplete"}, separators=(",", ":"))
    return encode()


def bound_review_state(text, limit):
    """Rebalance structured reviews when a binding uses a smaller state budget."""

    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("review_type") != "answer_grounding":
        return None
    evidence = dict(payload.get("runtime_evidence") or {})
    evidence["evidence_completeness"] = payload.get("evidence_completeness", "incomplete")
    return review_material(payload.get("question"), payload.get("answer"), evidence, limit)
