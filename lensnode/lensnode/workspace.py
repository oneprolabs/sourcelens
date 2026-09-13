import fnmatch
import json
import logging
import os
from itertools import islice
from pathlib import Path
import re
import subprocess

from .path_rules import SIDECAR_SUFFIX

LOGGER = logging.getLogger("lensnode")

DEFAULT_EXCLUDED_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
}

DEFAULT_EXCLUDED_EXTENSIONS = {
    ".lock",
    ".pyc",
    ".sqlite3",
    ".svg",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".webp",
    ".bmp",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
    ".eot",
    ".map",
}

INTERNAL_CHECKPOINT_DIR = ".checkpoints"
INTERNAL_RUN_PATH = (".sourcelens", "runtime", "runs")

DEFAULT_CONTEXT_LINES = 2
DEFAULT_MAX_SEARCH_MATCHES = 50
MAX_SEARCH_MATCHES = 200
DEFAULT_MAX_MATCHES_PER_FILE = 15
DEFAULT_MAX_LINE_CHARS = 2000
DEFAULT_READ_LIMIT = 250
MAX_READ_LIMIT = 1000
DEFAULT_FILE_LIST_LIMIT = 100
GLOB_SCAN_LIMIT = 1000
BINARY_SNIFF_BYTES = 4096

_DEPRECATED_KEYS_LOGGED = set()


def available_dirs(workspace_path):
    """Return first-level directories with their immediate subdirectories."""

    root = Path(workspace_path)
    if not root.exists():
        return []

    dirs = []
    for child in sorted(root.iterdir(), key=lambda item: item.name):
        if not child.is_dir() or child.name.startswith("."):
            continue
        children = []
        try:
            for sub in sorted(child.iterdir(), key=lambda x: x.name):
                if sub.is_dir() and not sub.name.startswith("."):
                    children.append({"path": str(sub), "name": sub.name})
                    if len(children) >= 30:
                        break
        except PermissionError:
            pass
        dirs.append({"path": str(child), "name": child.name, "children": children})
    return dirs


def search_workspace(
    target_dirs,
    query,
    max_results=None,
    policy=None,
    *,
    regex=False,
    glob="",
    output_mode="content",
    context_lines=None,
    case_sensitive=False,
):
    """Search selected directories, ripgrep-style.

    output_mode controls the result shape:
    - "content" (default): line matches {path, line, text, before, after};
      falls back to a file listing when nothing matches.
    - "files": the list of files that contain a match.
    - "count": per-file match counts.

    `query` is treated as keywords (fixed-string, case-folded) by default;
    set regex=True to pass a ripgrep regular expression instead. `glob`
    restricts the search to matching paths (e.g. "**/*.md", "*.py"). File
    size never gates a match — ripgrep streams, so any size is searchable.
    """

    policy = policy or {}
    _note_deprecated_size({}, policy)
    max_results = _bounded_results(max_results, policy)
    dirs = []
    for item in target_dirs:
        root = Path(item.get("path", ""))
        if root.exists() and root.is_dir():
            dirs.append((root, target_scope(item)))

    if output_mode == "files":
        files = []
        for root, scope in dirs:
            files.extend(
                _rg_files_with_matches(
                    root, query, scope, policy,
                    regex=regex, glob=glob, case_sensitive=case_sensitive,
                )
            )
            if len(files) >= max_results:
                break
        files = _visible_paths(files, dirs, policy)
        return {"mode": "files", "files": files[:max_results]}

    if output_mode == "count":
        counts = []
        for root, scope in dirs:
            counts.extend(
                _rg_counts(
                    root, query, scope, policy,
                    regex=regex, glob=glob, case_sensitive=case_sensitive,
                )
            )
            if len(counts) >= max_results:
                break
        counts = _visible_counts(counts, dirs, policy)
        counts.sort(key=lambda item: (-item["count"], item["path"]))
        return {"mode": "count", "counts": counts[:max_results]}

    terms = [] if regex else _query_terms(query)
    matches = []
    for root, scope in dirs:
        ctx = int(
            context_lines
            if context_lines is not None
            else _option(scope, policy, "context_lines", DEFAULT_CONTEXT_LINES)
        )
        matches.extend(
            _rg_line_matches(
                root, query, terms, scope, policy, ctx,
                regex=regex, glob=glob, case_sensitive=case_sensitive,
            )
        )
        if len(matches) >= max_results:
            break

    if matches:
        matches = [
            match
            for match in matches
            if _path_allowed_in_dirs(match["path"], dirs, policy)
        ]
        for match in matches:
            match["path"] = str(citation_path(match["path"]))
        matches = _rank_matches(matches, terms)
        if matches:
            return {
                "mode": "content",
                "matches": matches[:max_results],
                "files": [],
            }

    files = []
    for root, scope in dirs:
        files.extend(
            _iter_scope_files(
                root,
                scope,
                policy,
                limit=DEFAULT_FILE_LIST_LIMIT - len(files),
            )
        )
        if len(files) >= DEFAULT_FILE_LIST_LIMIT:
            break
    note = (
        "No matches in the selected workspace. Listing files in scope so "
        "you can read them directly with read_workspace_file (use "
        "offset/limit to page). Tip: try different keywords (the "
        "documents' own terms), a regex, or find_files by name."
    )
    return {
        "mode": "content",
        "matches": [],
        "files": _visible_paths(files, dirs, policy),
        "note": note,
    }


def glob_files(target_dirs, pattern, max_results=None, policy=None):
    """Find files by name/path glob across selected dirs, newest first.

    Mirrors a Glob tool: returns allowed file paths matching the pattern
    (e.g. "**/*.md", "src/**/*.py", "**/*install*"), sorted by
    modification time so the most recently changed files come first.
    """

    policy = policy or {}
    max_results = _bounded_results(max_results, policy)
    pattern = pattern or "**/*"
    found = []
    for item in target_dirs:
        root = Path(item.get("path", ""))
        if not root.exists() or not root.is_dir():
            continue
        scope = target_scope(item)
        seen_dirs = set()
        link_boundary = _symlink_boundary(root)
        for current_root, dirnames, filenames in os.walk(
            root, followlinks=True
        ):
            real_root = os.path.realpath(current_root)
            if real_root in seen_dirs:
                dirnames[:] = []
                continue
            seen_dirs.add(real_root)
            dirnames[:] = [
                name
                for name in dirnames
                if (
                    _include_hidden(scope, policy)
                    or not name.startswith(".")
                )
                and _symlink_target_allowed(
                    Path(current_root) / name, link_boundary
                )
                and os.path.realpath(
                    os.path.join(current_root, name)
                ) not in seen_dirs
            ]
            for name in filenames:
                path = Path(current_root) / name
                relative = path.relative_to(root).as_posix()
                if not _matches_glob_pattern(relative, pattern):
                    continue
                if len(found) >= GLOB_SCAN_LIMIT:
                    break
                if is_path_allowed(root, path, scope, policy):
                    visible = citation_path(path)
                    if visible != path and not is_path_allowed(
                        root,
                        visible,
                        scope,
                        policy,
                    ):
                        continue
                    found.append(visible)
            if len(found) >= GLOB_SCAN_LIMIT:
                break
    found = sorted(set(found), key=_safe_mtime, reverse=True)
    return [str(path) for path in found[:max_results]]


def read_workspace_window(path_value, offset=1, limit=None, policy=None):
    """Read a line window of a file: limit lines starting at offset.

    Reads only the requested window by streaming lines and stopping at
    offset+limit, so memory is bounded by the window, not the file size.
    Returns numbered lines plus has_more so the caller can page by
    increasing offset.
    """

    policy = policy or {}
    _note_deprecated_size({}, policy)
    max_line_chars = int(_option({}, policy, "max_line_chars", DEFAULT_MAX_LINE_CHARS))
    limit = _bounded_limit(limit, policy)
    offset = max(1, int(offset or 1))
    path = Path(path_value)

    if _is_binary(path):
        return {
            "path": str(path),
            "error": "BINARY_FILE",
            "message": "Binary file; not readable as text.",
        }

    lines = []
    has_more = False
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            window = islice(handle, offset - 1, offset - 1 + limit)
            for index, raw in enumerate(window, start=offset):
                lines.append(
                    {"n": index, "text": _truncate(raw.rstrip("\n"), max_line_chars)}
                )
            has_more = handle.readline() != ""
    except OSError as exc:
        return {"path": str(path), "error": "READ_FAILED", "message": str(exc)}

    content = "\n".join(f"{item['n']}\t{item['text']}" for item in lines)
    return {
        "path": str(path),
        "start_line": offset if lines else 0,
        "end_line": offset + len(lines) - 1,
        "returned_lines": len(lines),
        "has_more": has_more,
        "content": content,
    }


def read_text_samples(target_dirs, max_files=16, max_chars=30000, policy=None):
    """Collect small readable text samples from selected directories."""

    policy = policy or {}
    samples = []
    remaining_chars = max_chars
    for item in target_dirs:
        root = Path(item.get("path", ""))
        if not root.exists() or not root.is_dir():
            continue
        scope = target_scope(item)
        for path in _iter_allowed_paths(root, scope, policy):
            if len(samples) >= max_files or remaining_chars <= 0:
                return samples
            text = _read_text(path)
            if not text:
                continue
            text = text[:remaining_chars]
            remaining_chars -= len(text)
            samples.append(
                {
                    "path": str(path),
                    "content": text,
                }
            )
    return samples


def summarize_hits(hits):
    """Return a JSON summary string compatible with ai-query events."""

    return json.dumps({"hits": hits}, ensure_ascii=False)


def summarize_snippets(samples):
    """Return a JSON summary string compatible with ai-query events."""

    snippets = []
    for sample in samples:
        snippets.append(
            {
                "path": sample["path"],
                "excerpt": sample["content"][:500],
            }
        )
    return json.dumps({"snippets": snippets}, ensure_ascii=False)


def is_path_allowed(root, path, scope, policy):
    """Return whether a path is useful as a workspace sample.

    File size is intentionally not a criterion: large files are read in
    bounded windows and searched by streaming, so size never excludes a
    file from retrieval.
    """

    try:
        path.relative_to(root)
        path.resolve().relative_to(_workspace_boundary(root))
        if not _path_uses_allowed_links(root, path):
            return False
    except (OSError, ValueError):
        return False
    if not path.is_file():
        return False
    if is_path_excluded(root, path, scope, policy):
        return False
    if _is_sidecar_artifact(path) and converted_source_path(path) is None:
        return False
    exclude_extensions = _option(
        scope,
        policy,
        "exclude_extensions",
        DEFAULT_EXCLUDED_EXTENSIONS,
    )
    if path.suffix in _normalize_extensions(exclude_extensions):
        return False
    return True


def converted_source_path(path_value):
    """Return the source document represented by a sidecar content file."""

    path = Path(path_value)
    parent = path.parent
    if (
        not path.is_file()
        or path.name != "content.md"
        or not parent.name.endswith(SIDECAR_SUFFIX)
    ):
        return None
    source_name = parent.name[: -len(SIDECAR_SUFFIX)]
    if not source_name:
        return None
    source = parent.parent / source_name
    return source if source.is_file() else None


def citation_path(path_value):
    """Return the user-facing source path for a retrieval artifact."""

    path = Path(path_value)
    return converted_source_path(path) or path


def retrieval_path(path_value):
    """Return searchable sidecar text when a source document has one."""

    path = Path(path_value)
    content = Path(f"{path}{SIDECAR_SUFFIX}") / "content.md"
    if path.is_file() and converted_source_path(content) == path:
        return content
    return path


def _is_sidecar_artifact(path_value):
    """Return whether a path is inside a conversion sidecar directory."""

    path = Path(path_value)
    return any(
        part != SIDECAR_SUFFIX and part.endswith(SIDECAR_SUFFIX)
        for part in path.parts
    )


def _path_allowed_in_dirs(path_value, dirs, policy):
    """Return whether a tool result remains allowed by its selected root."""

    path = Path(path_value)
    for root, scope in dirs:
        if not is_path_allowed(root, path, scope, policy):
            continue
        source = converted_source_path(path)
        if source is None or is_path_allowed(root, source, scope, policy):
            return True
    return False


def _visible_paths(paths, dirs, policy):
    """Return unique allowed paths with conversion artifacts normalized."""

    visible = []
    for path in paths:
        if not _path_allowed_in_dirs(path, dirs, policy):
            continue
        value = str(citation_path(path))
        if value not in visible:
            visible.append(value)
    return visible


def _visible_counts(counts, dirs, policy):
    """Merge match counts under their user-facing source paths."""

    visible = {}
    for item in counts:
        path = item["path"]
        if not _path_allowed_in_dirs(path, dirs, policy):
            continue
        value = str(citation_path(path))
        visible[value] = visible.get(value, 0) + int(item["count"])
    return [
        {"path": path, "count": count}
        for path, count in visible.items()
    ]


def _query_terms(query):
    """Extract lightweight search terms from a user question."""

    terms = []
    for term in re.findall(r"[\w一-鿿]+", query.lower()):
        if len(term) < 2:
            continue
        terms.append(term)
        if _contains_cjk(term):
            terms.extend(_ngrams(term, 2))
            terms.extend(_ngrams(term, 3))
    seen = set()
    unique_terms = []
    for term in terms:
        if term in seen:
            continue
        seen.add(term)
        unique_terms.append(term)
    return unique_terms


def _run_rg(cmd):
    """Run ripgrep; return stdout, or None when ripgrep is unavailable.

    A non-{0,1} return code (e.g. a bad regex) yields "" so the caller
    treats it as no results rather than falling back.
    """

    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            check=False,
            text=True,
        )
    except OSError:
        return None
    if completed.returncode not in {0, 1}:
        return ""
    return completed.stdout


def _rg_glob_args(root, scope, policy, glob):
    """Build ripgrep -g include/exclude args from scope and an extra glob.

    The trivial "**/*" include is skipped: ripgrep unions include globs,
    so emitting it would defeat a narrower glob (e.g. "**/*.md").
    Ripgrep's default already searches everything minus the excludes.
    """

    args = []
    if _include_hidden(scope, policy):
        args.append("--hidden")
    for pattern in scope.get("include_paths") or []:
        if pattern == "**/*":
            continue
        args.extend(["-g", pattern])
    if glob:
        args.extend(["-g", glob])
    for pattern in _exclude_globs(root, scope, policy):
        args.extend(["-g", f"!{pattern}"])
    for pattern in _excluded_symlink_globs(root):
        args.extend(["-g", f"!{pattern}"])
    return args


def _excluded_symlink_globs(root):
    """Return ripgrep exclusions for directory links outside datasources."""

    boundary = _symlink_boundary(root)
    excluded = []
    for current_root, dirnames, _ in os.walk(root, followlinks=False):
        for name in dirnames:
            path = Path(current_root) / name
            if path.is_symlink() and not _symlink_target_allowed(
                path, boundary
            ):
                relative = path.relative_to(root).as_posix()
                excluded.extend((relative, f"{relative}/**"))
    return excluded


def _rg_patterns(query, terms, regex):
    """Return (patterns, fixed) for a search, or (None, _) when empty."""

    if regex:
        return ([query], False) if query else (None, False)
    return (terms, True) if terms else (None, True)


def _rg_line_matches(
    root, query, terms, scope, policy, context_lines,
    *, regex=False, glob="", case_sensitive=False,
):
    """Return ripgrep line matches with context, or a Python fallback."""

    patterns, fixed = _rg_patterns(query, terms, regex)
    if not patterns:
        return []

    max_per_file = int(
        _option(scope, policy, "max_matches_per_file", DEFAULT_MAX_MATCHES_PER_FILE)
    )
    max_line_chars = int(
        _option(scope, policy, "max_line_chars", DEFAULT_MAX_LINE_CHARS)
    )
    cmd = ["rg", "--follow", "--json"]
    if not case_sensitive:
        cmd.append("-i")
    if fixed:
        cmd.append("-F")
    cmd.extend([
        "-C",
        str(max(0, int(context_lines))),
        "-m",
        str(max(1, max_per_file)),
        "--max-columns",
        str(max(1, max_line_chars)),
        "--max-columns-preview",
    ])
    cmd.extend(_rg_glob_args(root, scope, policy, glob))
    for pattern in patterns:
        cmd.extend(["-e", pattern])
    cmd.append(str(root))

    stdout = _run_rg(cmd)
    if stdout is None:
        if regex:
            return []
        return _python_line_matches(
            root, terms, scope, policy, context_lines, max_per_file, max_line_chars
        )
    return _parse_rg_json(stdout, int(context_lines), max_line_chars)


def _rg_files_with_matches(
    root, query, scope, policy, *, regex=False, glob="", case_sensitive=False,
):
    """Return files in a directory that contain a match (rg -l)."""

    patterns, fixed = _rg_patterns(query, _query_terms(query), regex)
    if not patterns:
        return []
    cmd = ["rg", "--follow", "-l"]
    if not case_sensitive:
        cmd.append("-i")
    if fixed:
        cmd.append("-F")
    cmd.extend(_rg_glob_args(root, scope, policy, glob))
    for pattern in patterns:
        cmd.extend(["-e", pattern])
    cmd.append(str(root))
    stdout = _run_rg(cmd)
    if not stdout:
        return []
    return [line.strip() for line in stdout.splitlines() if line.strip()]


def _rg_counts(
    root, query, scope, policy, *, regex=False, glob="", case_sensitive=False,
):
    """Return per-file match counts in a directory (rg -c)."""

    patterns, fixed = _rg_patterns(query, _query_terms(query), regex)
    if not patterns:
        return []
    cmd = ["rg", "--follow", "-c"]
    if not case_sensitive:
        cmd.append("-i")
    if fixed:
        cmd.append("-F")
    cmd.extend(_rg_glob_args(root, scope, policy, glob))
    for pattern in patterns:
        cmd.extend(["-e", pattern])
    cmd.append(str(root))
    stdout = _run_rg(cmd)
    if not stdout:
        return []
    counts = []
    for line in stdout.splitlines():
        path, sep, value = line.rpartition(":")
        if not sep:
            continue
        try:
            counts.append({"path": path, "count": int(value)})
        except ValueError:
            continue
    return counts


def _safe_mtime(path):
    """Return a file's mtime, or 0 when it cannot be read."""

    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _rank_matches(matches, terms):
    """Order matches so the most topically relevant files come first.

    Relevance is the number of distinct query terms a file's matched
    lines collectively contain (broad coverage ranks above a single
    common word repeated), with raw match count as a tie-breaker. Files
    stay grouped and their lines stay in order. The cost is
    O(matches * terms), so ranking adds no meaningful latency.
    """

    if not matches or not terms:
        return matches
    coverage = {}
    count = {}
    for match in matches:
        path = match["path"]
        text = match["text"].lower()
        terms_seen = coverage.setdefault(path, set())
        for term in terms:
            if term in text:
                terms_seen.add(term)
        count[path] = count.get(path, 0) + 1

    def sort_key(match):
        path = match["path"]
        return (-len(coverage[path]), -count[path], path, match["line"])

    return sorted(matches, key=sort_key)


def _parse_rg_json(stdout, context_lines, max_line_chars):
    """Parse ripgrep --json output into match records with context."""

    by_file = {}
    order = []
    for raw in stdout.splitlines():
        try:
            event = json.loads(raw)
        except ValueError:
            continue
        event_type = event.get("type")
        if event_type not in {"match", "context"}:
            continue
        data = event.get("data") or {}
        path = (data.get("path") or {}).get("text")
        line_number = data.get("line_number")
        text = (data.get("lines") or {}).get("text", "")
        if path is None or line_number is None:
            continue
        if path not in by_file:
            by_file[path] = []
            order.append(path)
        by_file[path].append(
            (int(line_number), text.rstrip("\n"), event_type == "match")
        )

    results = []
    for path in order:
        items = by_file[path]
        line_map = {ln: txt for ln, txt, _ in items}
        for ln, txt, is_match in items:
            if not is_match:
                continue
            before = [
                {"n": n, "text": _truncate(line_map[n], max_line_chars)}
                for n in range(ln - context_lines, ln)
                if n in line_map
            ]
            after = [
                {"n": n, "text": _truncate(line_map[n], max_line_chars)}
                for n in range(ln + 1, ln + 1 + context_lines)
                if n in line_map
            ]
            results.append(
                {
                    "path": path,
                    "line": ln,
                    "text": _truncate(txt, max_line_chars),
                    "before": before,
                    "after": after,
                }
            )
    return results


def _python_line_matches(
    root, terms, scope, policy, context_lines, max_per_file, max_line_chars
):
    """Pure-Python line search fallback when ripgrep is unavailable."""

    lowered = [term.lower() for term in terms]
    results = []
    for path in _iter_scope_files(root, scope, policy, limit=DEFAULT_FILE_LIST_LIMIT):
        if _is_binary(path):
            continue
        text = _read_text(path)
        if not text:
            continue
        lines = text.splitlines()
        count = 0
        for index, line in enumerate(lines):
            if count >= max_per_file:
                break
            if not any(term in line.lower() for term in lowered):
                continue
            before = [
                {"n": n + 1, "text": _truncate(lines[n], max_line_chars)}
                for n in range(max(0, index - context_lines), index)
            ]
            after = [
                {"n": n + 1, "text": _truncate(lines[n], max_line_chars)}
                for n in range(index + 1, min(len(lines), index + 1 + context_lines))
            ]
            results.append(
                {
                    "path": str(path),
                    "line": index + 1,
                    "text": _truncate(line, max_line_chars),
                    "before": before,
                    "after": after,
                }
            )
            count += 1
    return results


def _iter_scope_files(root, scope, policy, limit=DEFAULT_FILE_LIST_LIMIT):
    """Yield up to limit allowed files from include paths (bounded)."""

    found = []
    for pattern in scope.get("include_paths") or ["**/*"]:
        boundary = _symlink_boundary(root)
        seen_dirs = set()
        for current_root, dirnames, filenames in os.walk(
            root, followlinks=True
        ):
            real_root = os.path.realpath(current_root)
            if real_root in seen_dirs:
                dirnames[:] = []
                continue
            seen_dirs.add(real_root)
            dirnames[:] = [
                name for name in dirnames
                if (
                    _include_hidden(scope, policy)
                    or not name.startswith(".")
                )
                and _symlink_target_allowed(
                    Path(current_root) / name, boundary
                )
                and os.path.realpath(
                    os.path.join(current_root, name)
                ) not in seen_dirs
            ]
            paths = [Path(current_root) / name for name in filenames]
            for path in paths:
                relative = path.relative_to(root).as_posix()
                if not _matches_glob_pattern(relative, pattern):
                    continue
                if len(found) >= limit:
                    return sorted(set(found))[:limit]
                if is_path_allowed(root, path, scope, policy):
                    found.append(path)
    return sorted(set(found))[:limit]


def _iter_allowed_paths(root, scope, policy):
    """Yield allowed files from include paths without applying weights."""

    paths = []
    for pattern in scope.get("include_paths") or ["**/*"]:
        paths.extend(
            path
            for path in root.glob(pattern)
            if is_path_allowed(root, path, scope, policy)
        )
    return sorted(set(paths))


def _option(scope, policy, key, default):
    """Read a retrieval option from scope first, then policy."""

    if key in scope:
        return scope[key]
    if key in policy:
        return policy[key]
    return default


def _matches_glob_pattern(relative, pattern):
    """Match a relative path using the previous Path.glob semantics."""

    path = Path(relative)
    if "/" not in pattern:
        return path.parent == Path(".") and fnmatch.fnmatchcase(
            path.name, pattern
        )
    patterns = [pattern]
    if "**/" in pattern:
        patterns.append(pattern.replace("**/", "", 1))
    return any(path.match(candidate) for candidate in patterns)


def _symlink_boundary(root):
    """Return the trusted boundary for Session datasource symlinks."""

    parts = root.resolve().parts
    if "sessions" in parts:
        workspace = Path(*parts[: parts.index("sessions")])
        return workspace / "datasources"
    return root.resolve()


def _workspace_boundary(root):
    """Return the workspace root allowed for resolved session paths."""

    parts = root.resolve().parts
    if "sessions" in parts:
        return Path(*parts[: parts.index("sessions")])
    return root.resolve()


def _path_uses_allowed_links(root, path):
    """Return whether resolved path components stay within link policy."""

    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    current = root
    boundary = _symlink_boundary(root)
    for part in relative.parts:
        current = current / part
        if current.is_symlink() and not _symlink_target_allowed(
            current, boundary
        ):
            return False
    return True


def _symlink_target_allowed(path, boundary):
    """Allow datasource links while rejecting arbitrary external links."""

    if not path.is_symlink():
        return True
    try:
        path.resolve().relative_to(boundary)
    except (OSError, ValueError):
        return False
    return True


def target_scope(item):
    """Return retrieval options plus the trusted runtime material role."""

    scope = dict(item.get("retrieval_scope") or {})
    if item.get("material_role") == "subject":
        scope["material_role"] = "subject"
    return scope


def _include_hidden(scope, policy):
    """Return whether hidden descendants are explicitly enabled."""

    return _option(scope, policy, "include_hidden", False) is True


def _bounded_results(max_results, policy):
    """Clamp the requested match count to a sane positive ceiling."""

    default = int(_option({}, policy, "max_search_matches", DEFAULT_MAX_SEARCH_MATCHES))
    try:
        value = int(max_results)
    except (TypeError, ValueError):
        value = default
    if value <= 0:
        value = default
    return min(value, MAX_SEARCH_MATCHES)


def _bounded_limit(limit, policy):
    """Clamp the requested read window to a sane positive ceiling."""

    default = int(_option({}, policy, "max_read_lines", DEFAULT_READ_LIMIT))
    try:
        value = int(limit)
    except (TypeError, ValueError):
        value = default
    if value <= 0:
        value = default
    return min(value, MAX_READ_LIMIT)


def is_path_excluded(root, path, scope, policy):
    """Return whether a path is excluded by configured rules."""

    try:
        if root is None:
            relative = path
        else:
            try:
                relative = path.relative_to(root)
            except ValueError:
                relative = path.resolve().relative_to(root.resolve())
    except ValueError:
        return True
    if INTERNAL_CHECKPOINT_DIR in path.parts:
        return True
    if (
        _contains_path_parts(path.parts, INTERNAL_RUN_PATH)
        and not _is_subject_runtime_root(root, scope)
    ):
        return True
    if _include_hidden(scope, policy):
        hidden_parts = ()
    elif _is_subject_runtime_root(root, scope):
        hidden_parts = relative.parts
    else:
        hidden_parts = path.parts
    if any(part.startswith(".") for part in hidden_parts):
        return True
    parts = set(relative.parts)
    exclude_dirs = set(
        _option(scope, policy, "exclude_dirs", DEFAULT_EXCLUDED_DIRS)
    )
    if parts.intersection(exclude_dirs) or (
        root is not None and root.name in exclude_dirs
    ):
        return True
    max_depth = _option(scope, policy, "max_depth", None)
    if root is not None and max_depth is not None:
        if len(relative.parts) > int(max_depth):
            return True
    exclude_paths = _option(scope, policy, "exclude_paths", [])
    if root is None:
        return False
    return any(relative.match(pattern) for pattern in exclude_paths)


def _is_subject_runtime_root(root, scope):
    """Return whether root is a private Run subject-document directory."""

    if root is None or scope.get("material_role") != "subject":
        return False
    parts = root.parts
    return (
        len(parts) >= 5
        and parts[-5:-2] == (".sourcelens", "runtime", "runs")
        and parts[-1] == "subject-documents"
    )


def _contains_path_parts(parts, expected):
    """Return whether expected appears contiguously in path parts."""

    width = len(expected)
    return any(
        tuple(parts[index : index + width]) == expected
        for index in range(len(parts) - width + 1)
    )


def _exclude_globs(root, scope, policy):
    """Build ripgrep glob exclusions from scope and policy."""

    globs = [f"**/{INTERNAL_CHECKPOINT_DIR}/**"]
    if not _is_subject_runtime_root(root, scope):
        globs.append("**/.sourcelens/runtime/runs/**")
    exclude_dirs = _option(scope, policy, "exclude_dirs", DEFAULT_EXCLUDED_DIRS)
    for dirname in exclude_dirs:
        globs.append(f"**/{dirname}/**")
    globs.extend(_option(scope, policy, "exclude_paths", []))
    exclude_extensions = _option(
        scope,
        policy,
        "exclude_extensions",
        DEFAULT_EXCLUDED_EXTENSIONS,
    )
    for extension in _normalize_extensions(exclude_extensions):
        globs.append(f"**/*{extension}")
    return globs


def _normalize_extensions(values):
    """Normalize extension values to dotted lowercase suffixes."""

    extensions = set()
    for value in values or []:
        extension = str(value).lower().strip()
        if not extension:
            continue
        if not extension.startswith("."):
            extension = f".{extension}"
        extensions.add(extension)
    return extensions


def _contains_cjk(value):
    """Return whether a string contains CJK characters."""

    return any("一" <= char <= "鿿" for char in value)


def _ngrams(value, size):
    """Return character n-grams for compact CJK query terms."""

    if len(value) < size:
        return []
    return [value[index : index + size] for index in range(len(value) - size + 1)]


def _truncate(text, max_chars):
    """Truncate an over-long line, marking that it was cut."""

    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "…[truncated]"


def _is_binary(path):
    """Return whether a file looks binary by sniffing for null bytes."""

    try:
        with path.open("rb") as handle:
            chunk = handle.read(BINARY_SNIFF_BYTES)
    except OSError:
        return False
    return b"\x00" in chunk


def _note_deprecated_size(scope, policy):
    """Log once when the deprecated max_file_size option is present."""

    if "max_file_size" in _DEPRECATED_KEYS_LOGGED:
        return
    if "max_file_size" in (scope or {}) or "max_file_size" in (policy or {}):
        _DEPRECATED_KEYS_LOGGED.add("max_file_size")
        LOGGER.info(
            "retrieval config 'max_file_size' is deprecated and ignored; "
            "file size no longer limits workspace retrieval."
        )


def _read_text(path):
    """Read a text file, returning an empty string when unreadable."""

    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            return path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return ""
    except OSError:
        return ""
