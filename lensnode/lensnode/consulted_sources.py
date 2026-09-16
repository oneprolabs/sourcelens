"""Bounded, non-sensitive source citations for tool-loop answers.

The planned-evidence pipeline maps model-selected evidence IDs to citations.
Tool-loop tasks (knowledge Q&A, general chat) never pick evidence IDs, so they
report the workspace files the run actually inspected instead. Paths are
reduced to a public ``<mount name>/<relative path>`` form: internal runtime
locations and host absolute paths never leave the LensNode.

This module owns the *reader-facing* prefix: a mount name the operator chose is
kept, while a generated ``ds_*`` mount is replaced by its datasource name so
citations stay readable and attributable. ``lens/citations.py`` owns the
*guarantee* that no generated segment ever reaches a reader; it is a safety net
for the case where no datasource name is available, not the normal path.
"""

import re
from pathlib import Path

MAX_CONSULTED_SOURCES = 5
MAX_SOURCE_QUERY_CHARS = 500
MAX_SOURCE_LABEL_CHARS = 80
REVISION = "working-tree"

# Mount names the control plane derives from a datasource UUID carry no
# meaning for a reader, so the datasource's own name is preferred instead.
# Item-scoped bindings append the item's hex suffix (see snapshots.py), hence
# the optional trailing group. `lens/citations.py` strips any generated
# segment that survives this substitution.
GENERATED_MOUNT_PATTERN = re.compile(r"ds_[0-9a-fA-F]{32}(_[0-9a-fA-F]{8,32})?")

_LABELS = {
    "en": {
        "read": "Read while answering",
        "retrieved": 'Retrieved for "{query}"',
    },
    "es": {
        "read": "Leído al responder",
        "retrieved": 'Recuperado para "{query}"',
    },
    "zh": {
        "read": "回答过程中查阅",
        "retrieved": "为“{query}”检索到",
    },
}


def _language_key(answer_language):
    """Return the label language key for a code or a language name.

    ``command_answer_language`` yields display names such as "Simplified
    Chinese", while API callers may pass codes such as "zh-CN"; both are
    accepted here.
    """

    normalized = str(answer_language or "").strip().lower()
    if "chinese" in normalized or normalized.startswith("zh"):
        return "zh"
    if (
        "spanish" in normalized
        or normalized.startswith("es-")
        or normalized in {"es", "spa"}
    ):
        return "es"
    return "en"


class ConsultedSources:
    """Collect the sources a run inspected and render bounded citations."""

    def __init__(self, command=None):
        self._command = command if isinstance(command, dict) else {}
        self._labels = _mount_labels(self._command)
        self._reads = []
        self._searches = []

    def record_read(self, path, start_line, end_line, source):
        """Record one file window the run read."""

        entry = _entry(path, start_line, end_line, source)
        if entry is not None:
            self._reads.append(entry)

    def record_search(self, query, matches):
        """Record the line matches a workspace search returned."""

        entries = []
        for match in matches or ():
            if not isinstance(match, dict):
                continue
            before = [str(item) for item in (match.get("before") or ())]
            after = [str(item) for item in (match.get("after") or ())]
            text = str(match.get("text") or "")
            try:
                line = int(match.get("line"))
            except (TypeError, ValueError):
                continue
            entry = _entry(
                match.get("path"),
                line - len(before),
                line + len(after),
                "\n".join([*before, text, *after]),
            )
            if entry is not None:
                entries.append(entry)
        if entries:
            self._searches.append((str(query or "").strip(), entries))

    def export_state(self):
        """Return JSON-safe observations for checkpoint persistence."""

        return {
            "reads": [list(entry) for entry in self._reads],
            "searches": [
                [query, [list(entry) for entry in entries]]
                for query, entries in self._searches
            ],
        }

    def restore_state(self, value):
        """Restore validated observations from a checkpoint."""

        if not isinstance(value, dict):
            return
        reads = []
        for entry in value.get("reads") or ():
            if isinstance(entry, (list, tuple)) and len(entry) == 4:
                item = _entry(*entry)
                if item is not None:
                    reads.append(item)
        searches = []
        for item in value.get("searches") or ():
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                continue
            entries = []
            for entry in item[1] or ():
                if isinstance(entry, (list, tuple)) and len(entry) == 4:
                    value_entry = _entry(*entry)
                    if value_entry is not None:
                        entries.append(value_entry)
            if entries:
                searches.append((str(item[0] or ""), entries))
        self._reads = reads
        self._searches = searches

    def citations(self, answer_language=None):
        """Return at most MAX_CONSULTED_SOURCES public citations.

        A source is reported as consulted, never as supporting the answer:
        the ``supports`` label says so, which keeps an answer that reports the
        workspace lacks the information from reading as grounded. Text-based
        detection of such answers was tried and dropped — a partial "not
        found" about one term (for example the user's wording) suppressed the
        citations of an otherwise fully grounded reply.
        """

        language = _language_key(answer_language)
        labels = _LABELS[language]
        collected = []
        seen = set()
        for path, start, end, source in self._reads:
            _append(
                collected,
                seen,
                self._public_path(path),
                start,
                end,
                source,
                labels["read"],
            )
        for query, entries in self._searches:
            supports = (
                labels["retrieved"].format(query=query[:MAX_SOURCE_QUERY_CHARS])
                if query
                else labels["read"]
            )
            for path, start, end, source in entries:
                _append(
                    collected,
                    seen,
                    self._public_path(path),
                    start,
                    end,
                    source,
                    supports,
                )
        return collected[:MAX_CONSULTED_SOURCES]

    def _public_path(self, path_text):
        """Return the public path, or empty when it leaves the mounted dirs."""

        try:
            resolved = Path(path_text).resolve()
        except OSError:
            return ""
        for item in self._command.get("target_dirs") or ():
            root_text = str(item.get("path") or "")
            if not root_text:
                continue
            try:
                root = Path(root_text).resolve()
                relative = resolved.relative_to(root)
            except (OSError, ValueError):
                continue
            name = str(item.get("name") or "").strip() or root.name
            if not name:
                continue
            return f"{_citation_prefix(name, self._labels)}/{relative.as_posix()}"
        return ""


def _mount_labels(command):
    """Map generated mount names to readable, unique citation prefixes.

    Only generated ``ds_*`` mounts are relabeled: a mount name the operator
    chose is already meaningful. A datasource mounted once per item would
    otherwise produce identical prefixes, so repeats get a stable index
    instead of falling back to the mount name (which carries no meaning).
    """

    generated = {}
    for snapshot in command.get("datasource_snapshots") or ():
        if not isinstance(snapshot, dict):
            continue
        mount = str(snapshot.get("mount_name") or "").strip()
        label = _label_slug(snapshot.get("datasource_name"))
        if not mount or not label:
            continue
        if not GENERATED_MOUNT_PATTERN.fullmatch(mount):
            continue
        generated.setdefault(label, []).append(mount)
    labels = {}
    for label, mounts in generated.items():
        if len(mounts) == 1:
            labels[mounts[0]] = label
            continue
        for index, mount in enumerate(sorted(mounts), start=1):
            labels[mount] = f"{label}-{index}"
    return labels


def _citation_prefix(mount_name, labels):
    """Return the citation prefix for one mounted directory."""

    return labels.get(mount_name) or mount_name


def _label_slug(value):
    """Return a path-segment-safe label for a datasource name."""

    text = " ".join(str(value or "").split())
    text = text.replace("/", "-").replace("\\", "-")
    return text.strip(" .-")[:MAX_SOURCE_LABEL_CHARS]


def _entry(path, start_line, end_line, source):
    """Return a validated observation tuple, or None when unusable."""

    path = str(path or "").strip()
    source = str(source or "")
    try:
        start = int(start_line)
        end = int(end_line)
    except (TypeError, ValueError):
        return None
    if not path or not source or start < 1 or end < start:
        return None
    if len(source.splitlines()) < end - start + 1:
        return None
    return (path, start, end, source)


def _append(collected, seen, path, start, end, source, supports):
    """Append one deduplicated citation when its path is public."""

    if not path:
        return
    key = (path, start, end)
    if key in seen:
        return
    seen.add(key)
    collected.append(
        {
            "id": f"src-{len(collected) + 1}",
            "project": "workspace",
            "repository": "workspace",
            "revision": REVISION,
            "path": path,
            "symbol": "",
            "start_line": start,
            "end_line": end,
            "supports": supports,
            "source": source,
        }
    )
