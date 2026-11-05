"""Dataset import parsing with row-level validation."""

import csv
import io
import json

MAX_IMPORT_ROWS = 2000


def _split_list(value):
    """Return a list from a JSON array or a pipe-separated CSV cell."""

    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            parsed = json.loads(text)
        except ValueError:
            return [item.strip() for item in text.split("|") if item.strip()]
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    return [item.strip() for item in text.split("|") if item.strip()]


def _parse_json_object(value, field, line):
    """Return a JSON object cell or raise a row error."""

    if value in (None, ""):
        return {}
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value)
    except ValueError as exc:
        raise ValueError(f"{field}: invalid JSON ({exc.msg})") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{field}: expected a JSON object")
    return parsed


def _normalize_row(row, line):
    """Return one validated case payload or raise a row error."""

    if not isinstance(row, dict):
        raise ValueError("row must be an object")
    text = str(row.get("input") or "").strip()
    if not text:
        raise ValueError("input is required")
    return {
        "input": text,
        "expected_answer": str(row.get("expected_answer") or ""),
        "expected_facts": _split_list(row.get("expected_facts")),
        "expected_sources": _split_list(row.get("expected_sources")),
        "tags": _split_list(row.get("tags")),
        "config": _parse_json_object(row.get("config"), "config", line),
    }


def parse_import_payload(payload, filename=""):
    """Return normalized case rows and line-numbered validation errors."""

    text = payload if isinstance(payload, str) else ""
    if not text.strip():
        return [], [{"line": 0, "error": "empty payload"}]
    if filename and not filename.lower().endswith((".csv", ".jsonl")):
        return [], [{"line": 0, "error": "filename must end in .csv or .jsonl"}]
    is_csv = filename.lower().endswith(".csv")
    rows = []
    errors = []
    if is_csv:
        reader = csv.DictReader(io.StringIO(text))
        for index, row in enumerate(reader, start=2):
            if index - 1 > MAX_IMPORT_ROWS:
                errors.append({"line": index, "error": f"超过 {MAX_IMPORT_ROWS} 行上限"})
                break
            try:
                rows.append(_normalize_row(row, index))
            except ValueError as exc:
                errors.append({"line": index, "error": str(exc)})
    else:
        for index, raw in enumerate(text.splitlines(), start=1):
            if index > MAX_IMPORT_ROWS:
                errors.append({"line": index, "error": f"超过 {MAX_IMPORT_ROWS} 行上限"})
                break
            if not raw.strip():
                continue
            try:
                row = json.loads(raw)
            except ValueError as exc:
                errors.append({"line": index, "error": f"invalid JSON ({exc.msg})"})
                continue
            try:
                rows.append(_normalize_row(row, index))
            except ValueError as exc:
                errors.append({"line": index, "error": str(exc)})
    if not rows and not errors:
        errors.append({"line": 0, "error": "no rows found"})
    return rows, errors


def import_cases(dataset, rows):
    """Create every case in one transaction, rejecting duplicate inputs."""

    from django.db import transaction

    from lens.models import EvaluationCase

    existing = set(dataset.cases.values_list("input", flat=True))
    duplicates = []
    created = []
    with transaction.atomic():
        for row in rows:
            if row["input"] in existing:
                duplicates.append(row["input"][:120])
                continue
            created.append(EvaluationCase.objects.create(dataset=dataset, **row))
            existing.add(row["input"])
    return {"created": len(created), "duplicates": duplicates}
