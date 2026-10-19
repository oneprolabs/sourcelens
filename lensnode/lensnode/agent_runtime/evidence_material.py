"""Bound answer-review inputs without dropping whole evidence sources silently."""

import json
from collections import Counter

DEFAULT_REVIEW_STATE_CHARS = 80000


def _share_review_values(evidence):
    """Factor repeated structured facts into an explicit, lossless reference table."""

    counts = Counter()
    keys = set()

    def encode(value):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    def count(value):
        if isinstance(value, dict):
            keys.update(value)
            for item in value.values():
                count(item)
        elif isinstance(value, list):
            for item in value:
                count(item)
        if isinstance(value, (dict, list, str)):
            serialized = encode(value)
            if len(serialized) >= 40:
                counts[serialized] += 1

    count(evidence)
    reference_key = "$review_ref"
    while reference_key in keys:
        reference_key += "_"
    table_key = "$review_table"
    while table_key in keys:
        table_key += "_"
    values = []
    references = {}

    def pack(value):
        serialized = encode(value) if isinstance(value, (dict, list, str)) else None
        shared = serialized is not None and counts[serialized] > 1
        if shared and serialized in references:
            return {reference_key: references[serialized]}
        if isinstance(value, dict):
            packed = {key: pack(item) for key, item in value.items()}
        elif isinstance(value, list):
            packed = [pack(item) for item in value]
            if len(packed) >= 3 and all(isinstance(item, dict) for item in packed):
                columns = list(packed[0])
                if all(set(item) == set(columns) for item in packed):
                    table = {table_key: {"columns": columns, "rows": [[item[key] for key in columns] for item in packed]}}
                    if len(encode(table)) < len(encode(packed)):
                        packed = table
        else:
            packed = value
        if shared:
            index = len(values)
            references[serialized] = index
            values.append(packed)
            return {reference_key: index}
        return packed

    data = pack(evidence)
    return {
        "encoding": "shared-values-v1",
        "instructions": "An object with only reference_key refers to values[index]. Expand all references "
                        "recursively when reviewing data. An object with only table_key encodes a list of objects: "
                        "zip columns with each row to reconstruct them. All original facts and coverage limits are retained.",
        "reference_key": reference_key,
        "table_key": table_key,
        "values": values,
        "data": data,
    }


def review_material(question, answer, evidence=None, limit=4000):
    """Reserve space for evidence and emit valid JSON with explicit coverage."""

    evidence = evidence if isinstance(evidence, dict) else {}
    payload = {
        "review_type": "answer_grounding",
        "question": str(question or ""),
        "answer": str(answer or ""),
        "evidence_completeness": evidence.get("evidence_completeness", "complete"),
        "review_completeness": evidence.get("review_completeness", "complete"),
        "runtime_evidence": {
            key: value
            for key, value in evidence.items()
            if not key.startswith("_") and key not in {"evidence_completeness", "review_completeness"}
        },
    }
    if not payload["runtime_evidence"]:
        payload["evidence_completeness"] = "incomplete"

    def encode():
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)

    def strings(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if isinstance(item, str) and key not in {
                    "tool",
                    "evidence_completeness",
                    "review_completeness",
                    "review_type",
                }:
                    yield value, key, item
                elif isinstance(item, (dict, list)):
                    yield from strings(item)
        elif isinstance(value, list):
            for item in value:
                yield from strings(item)

    # Copy nested evidence before shrinking so runtime/checkpoint state stays intact.
    payload = json.loads(encode())
    if len(encode()) > limit:
        original = payload["runtime_evidence"]
        structured = json.loads(json.dumps(original, ensure_ascii=False))
        for item in structured.get("retrieved_evidence", []):
            if not isinstance(item, dict) or not isinstance(item.get("content"), str):
                continue
            try:
                content = json.loads(item["content"])
            except (TypeError, ValueError):
                continue
            if isinstance(content, (dict, list)):
                item.update(content=content, content_encoding="json")
        payload["runtime_evidence"] = _share_review_values(structured)
        if len(encode()) > limit:
            payload["runtime_evidence"] = original
    while len(encode()) > limit:
        payload["review_completeness"] = "incomplete"
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
        return json.dumps(
            {
                "evidence_completeness": payload["evidence_completeness"],
                "review_completeness": "incomplete",
            },
            separators=(",", ":"),
        )
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
    evidence["review_completeness"] = payload.get("review_completeness", "complete")
    return review_material(payload.get("question"), payload.get("answer"), evidence, limit)
