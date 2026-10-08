"""Read only manifest-owned source files and validated conversion sidecars."""

import hashlib
import json
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from uuid import UUID

from ..datasource_manifest import MARKER_FILE, manifest_items, manifest_local_path, manifest_source_id
from ..path_rules import sidecar_path
from ..workspace import DEFAULT_EXCLUDED_DIRS, is_path_policy_allowed, target_scope
from .config import MAX_CORPUS_BYTES, MAX_FILE_BYTES, MAX_FILES, IndexUnavailable, digest

TEXT_EXTENSIONS = frozenset(
    {
        ".md",
        ".mdx",
        ".txt",
        ".rst",
        ".csv",
        ".tsv",
        ".html",
        ".xml",
        ".py",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".vue",
        ".go",
        ".rs",
        ".java",
        ".c",
        ".h",
        ".cpp",
        ".hpp",
        ".cs",
        ".rb",
        ".php",
        ".kt",
        ".swift",
        ".sh",
        ".sql",
        ".toml",
        ".yaml",
        ".yml",
        ".json",
    }
)
CONVERTED_EXTENSIONS = frozenset({".pdf", ".docx", ".pptx", ".xlsx"})
EXCLUDED_NAMES = frozenset({"manifest.json", "credentials.json", "secrets.json", "id_rsa", "id_ed25519"})


@dataclass(frozen=True)
class Document:
    """A bounded, immutable input value for CocoIndex memoization."""

    source_id: str
    path: str
    source_hash: str
    text_hash: str
    text: str
    converted: bool


def source_path(root, relative):
    """Reject invalid and internal manifest paths without filesystem access."""

    path = PurePosixPath(relative)
    if not relative or path.is_absolute() or "\\" in relative or ".." in path.parts:
        raise IndexUnavailable("TEXT_INDEX_SOURCE_PATH_INVALID")
    for part in path.parts:
        if part.startswith(".") or part in DEFAULT_EXCLUDED_DIRS or part.endswith(".sourcelens"):
            raise IndexUnavailable("TEXT_INDEX_SOURCE_PATH_EXCLUDED")
    if path.name.lower() in EXCLUDED_NAMES:
        raise IndexUnavailable("TEXT_INDEX_SOURCE_PATH_EXCLUDED")
    return root / path


def safe_file(root, relative):
    """Reject traversal, internal paths, nested datasources, and symlinks."""

    source_path(root, relative)
    current = root
    for part in PurePosixPath(relative).parts:
        current = current / part
        if current.is_symlink():
            raise IndexUnavailable("TEXT_INDEX_SOURCE_PATH_INVALID")
        if current != root and current.is_dir() and (current / MARKER_FILE).exists():
            raise IndexUnavailable("TEXT_INDEX_NESTED_DATASOURCE")
    return current


def bounded_bytes(path, limit=MAX_FILE_BYTES):
    """Read a regular file within a fixed memory budget."""

    if path.is_symlink() or not path.is_file():
        raise IndexUnavailable("TEXT_INDEX_SOURCE_UNAVAILABLE")
    with path.open("rb") as stream:
        content = stream.read(limit + 1)
    if len(content) > limit:
        raise IndexUnavailable("TEXT_INDEX_SOURCE_LIMIT")
    return content


def read_manifest(root, datasource_uuid=None):
    """Load a complete retained catalog, preserving missing-but-retained files."""

    try:
        marker = json.loads(bounded_bytes(root / MARKER_FILE))
        actual_uuid = str(UUID(marker["datasource_uuid"]))
        if datasource_uuid is not None and actual_uuid != str(UUID(datasource_uuid)):
            raise ValueError
        manifest = json.loads(bounded_bytes(root / "manifest.json", 8 * 1024 * 1024))
        if str(UUID(manifest["datasource_uuid"])) != actual_uuid:
            raise ValueError
        if not isinstance(manifest.get("items", manifest.get("documents")), list):
            raise ValueError
        stats = manifest.get("stats") or {}
        if stats.get("scan_complete") is False or stats.get("failed", 0):
            raise IndexUnavailable("TEXT_INDEX_SOURCE_INCOMPLETE")
        items = manifest_items(manifest)
        if len(items) > MAX_FILES:
            raise IndexUnavailable("TEXT_INDEX_SOURCE_LIMIT")
        if any(item.get("status") == "failed" or item.get("error") for item in items):
            raise IndexUnavailable("TEXT_INDEX_SOURCE_INCOMPLETE")
        return actual_uuid, items
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise IndexUnavailable("TEXT_INDEX_MANIFEST_INVALID") from None


def read_document(root, item):
    """Read text or a successful sidecar whose source fingerprint still matches."""

    relative = manifest_local_path(item)
    if item.get("status") == "deleted":
        return None
    try:
        source = safe_file(root, relative)
    except IndexUnavailable as exc:
        if str(exc) in {"TEXT_INDEX_SOURCE_PATH_EXCLUDED", "TEXT_INDEX_NESTED_DATASOURCE"}:
            return None
        raise
    converted = source.suffix.lower() in CONVERTED_EXTENSIONS
    if not converted and source.suffix.lower() not in TEXT_EXTENSIONS:
        return None
    raw = bounded_bytes(source, 32 * 1024 * 1024 if converted else MAX_FILE_BYTES)
    source_hash = hashlib.sha256(raw).hexdigest()
    if converted:
        sidecar = sidecar_path(source)
        if sidecar.is_symlink():
            raise IndexUnavailable("TEXT_INDEX_SOURCE_PATH_INVALID")
        try:
            metadata = json.loads(bounded_bytes(sidecar / "meta.json"))
            if metadata.get("conversion", {}).get("status") != "success":
                raise ValueError
            if metadata.get("source", {}).get("sha256") != source_hash:
                raise ValueError
        except (ValueError, AttributeError):
            raise IndexUnavailable("TEXT_INDEX_CONVERSION_STALE") from None
        raw = bounded_bytes(sidecar / "content.md")
    try:
        text = raw.decode("utf-8")
        if "\x00" in text:
            raise ValueError
    except ValueError:
        raise IndexUnavailable("TEXT_INDEX_SOURCE_NOT_TEXT") from None
    return Document(
        str(manifest_source_id(item)),
        relative,
        source_hash,
        hashlib.sha256(raw).hexdigest(),
        text,
        converted,
    )


def collect_documents(root, datasource_uuid):
    """Materialize one bounded batch; failures abort publication of the batch."""

    _, items = read_manifest(root, datasource_uuid)
    documents = []
    paths = {}
    total = 0
    for item in items:
        document = read_document(root, item)
        if document is None:
            continue
        if document.path in paths:
            if paths[document.path] != document:
                raise IndexUnavailable("TEXT_INDEX_DUPLICATE_PATH")
            continue
        paths[document.path] = document
        total += len(document.text.encode("utf-8"))
        if total > MAX_CORPUS_BYTES:
            raise IndexUnavailable("TEXT_INDEX_SOURCE_LIMIT")
        documents.append(document)
    return sorted(documents, key=lambda document: document.path)


def generation_for(documents, profile):
    """Identify the whole indexed batch independently of mutable timestamps."""

    return digest(
        [profile, [[doc.source_id, doc.path, doc.source_hash, doc.text_hash, doc.converted] for doc in documents]]
    )


def source_revisions(root, items, *, skip_unavailable=False):
    """Fingerprint catalog identities and file versions without reading document text.

    Include conversion artifacts so a newly matching passage invalidates an
    old index even when the unchanged binary source still has the same hash.
    """

    revisions = {}
    for item in items:
        if item.get("status") == "deleted":
            continue
        relative = manifest_local_path(item)
        try:
            source = safe_file(root, relative)
            converted = source.suffix.lower() in CONVERTED_EXTENSIONS
            if not converted and source.suffix.lower() not in TEXT_EXTENSIONS:
                continue
            paths = [source]
            if converted:
                sidecar = sidecar_path(source)
                if sidecar.is_symlink():
                    raise IndexUnavailable("TEXT_INDEX_SOURCE_PATH_INVALID")
                paths.extend([sidecar / "meta.json", sidecar / "content.md"])
            versions = []
            for path in paths:
                version = path.stat(follow_symlinks=False)
                if not stat.S_ISREG(version.st_mode):
                    raise IndexUnavailable("TEXT_INDEX_SOURCE_UNAVAILABLE")
                versions.append([version.st_size, version.st_mtime_ns, version.st_ctime_ns])
            revision = digest([str(manifest_source_id(item)), versions])
            if relative in revisions and revisions[relative] != revision:
                raise IndexUnavailable("TEXT_INDEX_DUPLICATE_PATH")
            revisions[relative] = revision
        except (IndexUnavailable, OSError) as exc:
            if skip_unavailable or str(exc) in {"TEXT_INDEX_SOURCE_PATH_EXCLUDED", "TEXT_INDEX_NESTED_DATASOURCE"}:
                continue
            raise
    return revisions


def authorized_scopes(settings, target_dirs, policy):
    """Derive logical path filters from current manifests and Run bindings.

    Live filesystem authorization is deferred to ranked candidate files.
    These filters alone never authorize disclosure of stored chunk text.
    """

    scopes = []
    for entry in target_dirs:
        selected = Path(entry.get("path", "")).resolve()
        if not selected.is_relative_to(settings.workspace_path) or not selected.is_dir():
            continue
        root = selected
        while root != settings.workspace_path and not (root / MARKER_FILE).is_file():
            root = root.parent
        if root == settings.workspace_path:
            continue
        datasource_uuid, items = read_manifest(root)
        allowed = {}
        for item in items:
            if item.get("status") == "deleted":
                continue
            relative = manifest_local_path(item)
            try:
                path = source_path(root, relative)
            except IndexUnavailable:
                continue
            if path.is_relative_to(selected) and is_path_policy_allowed(selected, path, target_scope(entry), policy):
                allowed[relative] = item
        if allowed:
            scopes.append((root, datasource_uuid, allowed, entry))
    return scopes
