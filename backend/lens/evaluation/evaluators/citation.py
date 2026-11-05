"""Deterministic citation integrity checks for evaluation results."""

from pathlib import PurePosixPath


def _citation_path(item):
    """Return the normalized relative path of one citation, or an empty string."""

    if not isinstance(item, dict):
        return ""
    return str(item.get("path") or "").strip()


def _line_bounds(item):
    """Return the citation line range as two ints or ``None`` when invalid."""

    start = item.get("start_line")
    end = item.get("end_line")
    if start is None and end is None:
        return None
    if not isinstance(start, int) or isinstance(start, bool) or start < 1:
        return None
    if not isinstance(end, int) or isinstance(end, bool) or end < start:
        return None
    return start, end


def _path_is_relative(path):
    """Return True when a citation path is workspace-relative and normalized."""

    if not path or path.startswith("/"):
        return False
    parts = PurePosixPath(path).parts
    return bool(parts) and ".." not in parts


def _matches_known_path(path, known_paths):
    """Return True when a citation path exists in the supplied workspace keys."""

    normalized = path.strip("/")
    return any(
        key == normalized or key.endswith("/" + normalized) or normalized.endswith("/" + key) for key in known_paths
    )


def citation_integrity_metric(citations, expected_sources=None, known_paths=None):
    """Return structural and coverage scores for one answer's citations.

    ``known_paths`` is optional: when the caller cannot resolve the datasource
    manifest, structural validation still runs and the detail marks the
    existence check as not verified instead of guessing.
    """

    citations = citations if isinstance(citations, list) else []
    if not citations:
        return {
            "score": None,
            "detail": {"reason": "no_citations", "count": 0},
        }
    invalid = []
    paths = []
    for item in citations:
        path = _citation_path(item)
        paths.append(path)
        if not _path_is_relative(path):
            invalid.append({"path": path, "reason": "invalid_path"})
            continue
        bounds = _line_bounds(item)
        if item.get("start_line") is not None and bounds is None:
            invalid.append({"path": path, "reason": "invalid_line_range"})
    valid_ratio = (len(citations) - len(invalid)) / len(citations)

    expected = [str(value).strip() for value in (expected_sources or []) if str(value).strip()]
    missing_expected = [value for value in expected if not any(value in path for path in paths)]
    coverage_ratio = 1.0
    if expected:
        coverage_ratio = (len(expected) - len(missing_expected)) / len(expected)

    unresolved = []
    if known_paths is not None:
        known = {str(value).strip("/") for value in known_paths if str(value).strip()}
        unresolved = [path for path in paths if _path_is_relative(path) and not _matches_known_path(path, known)]
        if known:
            valid_ratio = max(
                valid_ratio - (len(unresolved) / len(citations)),
                0.0,
            )

    score = max(min(valid_ratio * coverage_ratio, 1.0), 0.0)
    return {
        "score": round(score, 4),
        "detail": {
            "count": len(citations),
            "invalid": invalid,
            "missing_expected_sources": missing_expected,
            "unresolved_paths": unresolved,
            "existence_checked": known_paths is not None,
        },
    }
