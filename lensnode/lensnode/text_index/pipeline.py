"""Build compact navigation catalogs and official PageIndex local PDF trees."""

import asyncio
import fcntl
import json
import os
import re
import tempfile
from pathlib import Path

from ..datasource_manifest import manifest_local_path, manifest_source_id
from .config import IndexUnavailable, digest, index_directory
from .documents import read_document, read_manifest, safe_file

MAX_SECTIONS = 512
MAX_CATALOG_BYTES = 64 * 1024 * 1024


def revision(root, item):
    """Version a manifest-owned file and its conversion sidecar without reading it."""

    from ..path_rules import sidecar_path

    relative = manifest_local_path(item)
    source = safe_file(root, relative)
    paths = [source]
    if source.suffix.lower() in {".docx", ".pptx", ".xlsx"}:
        sidecar = sidecar_path(source)
        if sidecar.is_symlink():
            raise IndexUnavailable("TEXT_INDEX_SOURCE_PATH_INVALID")
        paths.extend([sidecar / "meta.json", sidecar / "content.md"])
    values = []
    for path in paths:
        if path.is_symlink():
            raise IndexUnavailable("TEXT_INDEX_SOURCE_PATH_INVALID")
        try:
            stat = path.stat()
            values.append([stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns])
        except FileNotFoundError:
            values.append(None)
    return digest([str(manifest_source_id(item)), values])


def current_revisions(root, items):
    """Include nontext files and unavailable conversions in the file directory."""

    result = {}
    for item in items:
        if item.get("status") == "deleted":
            continue
        path = manifest_local_path(item)
        try:
            value = revision(root, item)
        except IndexUnavailable as exc:
            if str(exc) in {"TEXT_INDEX_SOURCE_PATH_EXCLUDED", "TEXT_INDEX_NESTED_DATASOURCE"}:
                continue
            raise
        if path in result and result[path] != value:
            raise IndexUnavailable("TEXT_INDEX_DUPLICATE_PATH")
        result[path] = value
    return result


def load_catalog(directory):
    """Read bounded, local JSON state without following a linked catalog."""

    path = directory / "catalog.json"
    if path.is_symlink():
        raise IndexUnavailable("TEXT_INDEX_SOURCE_PATH_INVALID")
    try:
        with path.open("rb") as stream:
            data = stream.read(MAX_CATALOG_BYTES + 1)
        if len(data) > MAX_CATALOG_BYTES:
            raise ValueError
        catalog = json.loads(data)
        if catalog["schema_version"] != 2 or not isinstance(catalog["documents"], dict):
            raise ValueError
        if not isinstance(catalog.get("profile"), str) or not isinstance(catalog.get("generation"), str):
            raise ValueError
        for row in catalog["documents"].values():
            if not isinstance(row, dict) or not all(
                isinstance(row.get(key), str) for key in ("revision", "title", "status", "kind")
            ):
                raise ValueError
            if not isinstance(row.get("bytes"), int) or not isinstance(row.get("sections"), list):
                raise ValueError
            if len(row["sections"]) > MAX_SECTIONS:
                raise ValueError
            for section in row["sections"]:
                if not isinstance(section, dict) or not isinstance(section.get("title"), str):
                    raise ValueError
                if "summary" in section and not isinstance(section["summary"], str):
                    raise ValueError
        return catalog
    except (OSError, ValueError, KeyError, TypeError):
        raise IndexUnavailable("TEXT_INDEX_UNAVAILABLE") from None


def pdf_sections(source, settings, directory):
    """Export only structure from the SDK; discard its temporary page-text store."""

    from pageindex import PageIndexClient

    backend = {}
    if os.getenv("LENSNODE_PAGEINDEX_API_BASE"):
        backend["api_base"] = os.environ["LENSNODE_PAGEINDEX_API_BASE"]
    if os.getenv("LENSNODE_PAGEINDEX_API_KEY"):
        backend["api_key"] = os.environ["LENSNODE_PAGEINDEX_API_KEY"]
    with tempfile.TemporaryDirectory(prefix="pdf-", dir=directory) as temporary:
        client = PageIndexClient(
            mode="local",
            index_model=settings.index_model,
            storage_path=temporary,
            index_backend=backend or None,
            summary_max_words=60,
            summary_concurrency=2,
        )
        document = client.submit_document(str(source))
        tree = client.get_tree(document["doc_id"], node_summary=True, include_text=False)
        if tree.get("status") != "completed":
            raise IndexUnavailable("TEXT_INDEX_INCOMPLETE")
        sections = []

        def visit(nodes, parent=None, level=1):
            """Flatten the tree while keeping parent links and original page locations."""

            for node in nodes:
                if len(sections) >= MAX_SECTIONS:
                    break
                node_id = str(node.get("node_id") or len(sections))[:100]
                sections.append(
                    {
                        "node_id": node_id,
                        "parent_id": parent,
                        "level": level,
                        "title": str(node.get("title") or "")[:200],
                        "summary": str(node.get("summary") or "")[:300],
                        "page": int(node["start_index"]),
                        "end_page": int(node["end_index"]),
                    }
                )
                visit(node.get("nodes") or [], node_id, level + 1)

        visit(tree.get("result") or [])
        return sections


def outline(document):
    """Retain heading locations and hierarchy, never paragraph or chunk text."""

    sections = []
    parents = []
    fenced = False
    for line, text in enumerate(document.text.splitlines(), 1):
        if text.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
            continue
        match = re.match(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$", text) if not fenced else None
        if not match:
            continue
        level = len(match[1])
        while parents and parents[-1][0] >= level:
            parents.pop()
        node_id = str(line)
        sections.append(
            {
                "node_id": node_id,
                "parent_id": parents[-1][1] if parents else None,
                "level": level,
                "title": match[2][:200],
                "line": line,
            }
        )
        parents.append((level, node_id))
        if len(sections) >= MAX_SECTIONS:
            break
    return sections


def build_catalog(settings, root, datasource_uuid, *, full_reprocess=False):
    """Refresh changed entries and publish a consistent catalog atomically."""

    root = Path(root).resolve(strict=True)
    if root == settings.workspace_path or not root.is_relative_to(settings.workspace_path):
        raise IndexUnavailable("TEXT_INDEX_SOURCE_PATH_INVALID")
    directory = index_directory(root, datasource_uuid)
    if any((directory / name).is_symlink() for name in ("writer.lock", "catalog.json")):
        raise IndexUnavailable("TEXT_INDEX_SOURCE_PATH_INVALID")
    directory.mkdir(parents=True, exist_ok=True)
    (directory.parent / ".gitignore").write_text("*\n!.gitignore\n", encoding="utf-8")
    with (directory / "writer.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise IndexUnavailable("TEXT_INDEX_BUSY") from None
        _, items = read_manifest(root, datasource_uuid)
        revisions = current_revisions(root, items)
        try:
            old = load_catalog(directory)
        except IndexUnavailable:
            old = {}
        reusable = not full_reprocess and old.get("profile") == settings.profile
        documents = {}
        changed = 0
        for item in items:
            relative = manifest_local_path(item)
            if relative not in revisions or relative in documents:
                continue
            previous = (old.get("documents") or {}).get(relative, {})
            if reusable and previous.get("revision") == revisions[relative] and previous.get("status") != "failed":
                documents[relative] = previous
                continue
            source = safe_file(root, relative)
            row = {
                "revision": revisions[relative],
                "title": source.stem[:200],
                "sections": [],
                "kind": source.suffix.lower().lstrip("."),
                "bytes": source.stat().st_size if source.is_file() else 0,
                "status": "directory_only",
            }
            if source.suffix.lower() == ".pdf":
                if settings.index_model:
                    try:
                        row["sections"] = pdf_sections(source, settings, directory)
                        row["status"] = "ready"
                    except Exception:
                        row["status"] = "failed"
                        row["reason"] = "PAGEINDEX_PDF_BUILD_FAILED"
                else:
                    row["reason"] = "PAGEINDEX_MODEL_NOT_CONFIGURED"
            else:
                try:
                    document = read_document(root, item)
                    if document:
                        row["sections"] = outline(document)
                        row["status"] = "ready"
                except (IndexUnavailable, OSError):
                    row["reason"] = "DOCUMENT_OUTLINE_UNAVAILABLE"
            documents[relative] = row
            changed += 1
        _, live_items = read_manifest(root, datasource_uuid)
        if revisions != current_revisions(root, live_items):
            raise IndexUnavailable("TEXT_INDEX_SOURCE_CHANGED")
        catalog = {
            "schema_version": 2,
            "engine": "pageindex",
            "profile": settings.profile,
            "generation": digest([settings.profile, revisions]),
            "documents": documents,
        }
        data = json.dumps(catalog, ensure_ascii=False).encode("utf-8")
        if len(data) > MAX_CATALOG_BYTES:
            raise IndexUnavailable("TEXT_INDEX_SOURCE_LIMIT")
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as stream:
            temporary = Path(stream.name)
            try:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise
        try:
            os.replace(temporary, directory / "catalog.json")
        finally:
            temporary.unlink(missing_ok=True)
        return {
            "status": "ready",
            "engine": "pageindex",
            "generation": catalog["generation"],
            "indexed_files": len(documents),
            "changed_files": changed,
            "deleted_files": len(set(old.get("documents") or {}) - set(documents)),
            "failed_files": sum(row["status"] == "failed" for row in documents.values()),
        }


async def build_index(settings, root, datasource_uuid, *, full_reprocess=False):
    """Run local SDK and catalog I/O outside an existing asynchronous agent loop."""

    return await asyncio.to_thread(build_catalog, settings, root, datasource_uuid, full_reprocess=full_reprocess)
