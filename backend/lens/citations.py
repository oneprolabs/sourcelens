"""Validation and public rendering for captured source citations."""

import re
from pathlib import PurePosixPath


MAX_CITATION_SOURCE_CHARS = 100_000
MAX_LINE_NUMBER = 10_000_000
CITATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
# Datasource mounts are materialized as `ds_<uuid>` directories; the segment
# is internal plumbing and carries no meaning for a reader of the citation.
# This is the guarantee layer: `lensnode/consulted_sources.py` normally
# replaces a generated mount with the datasource name before the citation
# reaches us, and this strip covers the runs where no name is available.
DATASOURCE_MOUNT_PATTERN = re.compile(r"^ds_[0-9a-f]{32}(_[0-9a-f]{8,32})?$")
GAP_CATEGORIES = {
    "caller_context",
    "runtime_error",
    "source",
    "structural",
}
PLANNER_STATUSES = {"fallback", "repaired", "valid"}
PUBLIC_CITATION_FIELDS = (
    "id",
    "project",
    "repository",
    "revision",
    "path",
    "symbol",
    "start_line",
    "end_line",
    "supports",
)


def sanitize_run_citations(value):
    """Return workspace-relative citation snapshots, one per path."""

    if not isinstance(value, list):
        return []
    citations = []
    seen = set()
    for item in value:
        citation = _sanitize_citation(item)
        if citation is None or citation["path"] in seen:
            continue
        seen.add(citation["path"])
        citations.append(citation)
    return citations


def public_run_citations(value):
    """Remove captured source text from user-visible citation metadata."""

    return [
        {field: item[field] for field in PUBLIC_CITATION_FIELDS}
        for item in sanitize_run_citations(value)
    ]


def sanitize_planned_evidence(value):
    """Return the bounded user-safe evidence quality summary."""

    if not isinstance(value, dict):
        return {}
    output = {}
    if isinstance(value.get("sufficient"), bool):
        output["sufficient"] = value["sufficient"]
    gaps = value.get("gap_categories")
    if isinstance(gaps, list):
        output["gap_categories"] = [
            item for item in gaps[:8] if item in GAP_CATEGORIES
        ]
    planner_status = value.get("planner_status")
    if planner_status in PLANNER_STATUSES:
        output["planner_status"] = planner_status
    reason = value.get("planner_rejection_reason")
    if isinstance(reason, str) and reason:
        output["planner_rejection_reason"] = reason[:500]
    return output


def citation_source_payload(citation):
    """Return line-numbered captured source for the code viewer."""

    start_line = citation["start_line"]
    lines = [
        {"number": start_line + index, "content": content}
        for index, content in enumerate(citation["source"].splitlines())
    ]
    return {
        **{
            field: citation[field]
            for field in PUBLIC_CITATION_FIELDS
        },
        "highlight_start_line": start_line,
        "highlight_end_line": citation["end_line"],
        "lines": lines,
    }


def _sanitize_citation(value):
    if not isinstance(value, dict):
        return None
    citation_id = str(value.get("id") or "").strip()
    path_text = str(value.get("path") or "").strip()
    path = PurePosixPath(path_text)
    if (
        not CITATION_ID_PATTERN.fullmatch(citation_id)
        or not path_text
        or path.is_absolute()
        or ".." in path.parts
        or "\\" in path_text
    ):
        return None
    try:
        start_line = int(value.get("start_line"))
        end_line = int(value.get("end_line"))
    except (TypeError, ValueError):
        return None
    source = str(value.get("source") or "")
    if (
        start_line < 1
        or end_line < start_line
        or end_line > MAX_LINE_NUMBER
        or not source
        or len(source) > MAX_CITATION_SOURCE_CHARS
        or len(source.splitlines()) < end_line - start_line + 1
    ):
        return None
    revision = _bounded_text(value.get("revision"), 160)
    supports = _bounded_text(value.get("supports"), 1_000)
    if not revision or not supports:
        return None
    return {
        "id": citation_id,
        "evidence_id": _bounded_text(
            value.get("evidence_id") or citation_id,
            128,
        ),
        "project": _bounded_text(value.get("project"), 160),
        "repository": _bounded_text(value.get("repository"), 240),
        "revision": revision,
        "path": _public_path(path),
        "symbol": _bounded_text(value.get("symbol"), 500),
        "start_line": start_line,
        "end_line": end_line,
        "supports": supports,
        "source": source,
    }


def _bounded_text(value, limit):
    return str(value or "").strip()[:limit]


def _public_path(path):
    """Return the reader-facing citation path.

    A datasource is mounted as a ``ds_<uuid>`` directory, so its files arrive
    as ``ds_<uuid>/<relative path>``. That first segment is internal plumbing,
    so it is dropped for display while the rest of the path is preserved.
    """

    parts = path.parts
    if len(parts) > 1 and DATASOURCE_MOUNT_PATTERN.fullmatch(parts[0]):
        return PurePosixPath(*parts[1:]).as_posix()
    return path.as_posix()
