"""Catalog publication, SDK tree export, and incremental source lifecycle."""

import asyncio
import fcntl
import json
import sys
from dataclasses import replace
from types import SimpleNamespace

import pytest

from lensnode.text_index import pipeline
from lensnode.text_index.config import IndexUnavailable, index_directory
from lensnode.text_index.pipeline import build_index
from lensnode.text_index.query import search
from lensnode.tests.test_pageindex_navigation import source


def test_new_matching_file_invalidates_then_rebuilds(source):
    root, identity, settings = source
    asyncio.run(build_index(settings, root, identity))
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    (root / "new.md").write_text("# New recovery plan\nNew evidence\n")
    manifest["items"].append({"source_id": "new", "local_path": "new.md"})
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(IndexUnavailable, match="STALE"):
        asyncio.run(search(settings, [{"path": str(root)}], {}, "recovery"))
    result = asyncio.run(build_index(settings, root, identity))
    assert result["changed_files"] == 1
    assert len(asyncio.run(search(settings, [{"path": str(root)}], {}, "recovery"))["matches"]) == 2
    manifest["items"].pop()
    manifest_path.write_text(json.dumps(manifest))
    (root / "new.md").unlink()
    assert asyncio.run(build_index(settings, root, identity))["deleted_files"] == 1


def test_locked_writer_preserves_catalog(source):
    root, identity, settings = source
    asyncio.run(build_index(settings, root, identity))
    directory = index_directory(root, identity)
    before = (directory / "catalog.json").read_bytes()
    with (directory / "writer.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(IndexUnavailable, match="BUSY"):
            asyncio.run(build_index(settings, root, identity))
    assert (directory / "catalog.json").read_bytes() == before


def test_sdk_is_explicitly_local_and_text_cache_is_discarded(source, monkeypatch):
    root, identity, settings = source
    (root / "manual.pdf").write_bytes(b"%PDF test fixture")
    manifest = json.loads((root / "manifest.json").read_text())
    manifest["items"].append({"source_id": "pdf", "local_path": "manual.pdf"})
    (root / "manifest.json").write_text(json.dumps(manifest))
    calls = []

    class LocalClient:
        """Use an SDK boundary fake so tests need no billable model calls."""

        def __init__(self, **kwargs):
            assert kwargs["mode"] == "local"
            assert "api_key" not in kwargs
            from pathlib import Path

            (Path(kwargs["storage_path"]) / "pages.json").write_text("DO_NOT_KEEP_FULL_TEXT")

        def submit_document(self, path):
            calls.append(path)
            return {"doc_id": "pdf-id"}

        def get_tree(self, doc_id, **kwargs):
            assert kwargs == {"node_summary": True, "include_text": False}
            return {
                "status": "completed",
                "result": [
                    {
                        "title": "Recovery",
                        "node_id": "1",
                        "start_index": 2,
                        "end_index": 3,
                        "summary": "Retry procedures",
                        "text": "DO_NOT_KEEP_FULL_TEXT",
                        "nodes": [],
                    }
                ],
            }

    monkeypatch.setitem(sys.modules, "pageindex", SimpleNamespace(PageIndexClient=LocalClient))
    settings = replace(settings, index_model="openai/test")
    result = asyncio.run(build_index(settings, root, identity))
    assert result["failed_files"] == 0
    assert calls == [str(root / "manual.pdf")]
    directory = index_directory(root, identity)
    assert not list(directory.glob("pdf-*"))
    assert "DO_NOT_KEEP_FULL_TEXT" not in (directory / "catalog.json").read_text()
    matches = asyncio.run(search(settings, [{"path": str(root)}], {}, "Recovery"))["matches"]
    pdf = next(row for row in matches if row["relative_path"] == "manual.pdf")
    assert pdf["page"] == 2 and pdf["end_page"] == 3
    asyncio.run(build_index(settings, root, identity))
    assert len(calls) == 1


def test_failed_pdf_does_not_block_other_document_trees(source, monkeypatch):
    root, identity, settings = source
    (root / "scanned.pdf").write_bytes(b"bad pdf")
    manifest = json.loads((root / "manifest.json").read_text())
    manifest["items"].append({"source_id": "pdf", "local_path": "scanned.pdf"})
    (root / "manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(pipeline, "pdf_sections", lambda *args: (_ for _ in ()).throw(ValueError("sensitive failure")))
    result = asyncio.run(build_index(replace(settings, index_model="test"), root, identity))
    assert result["indexed_files"] == 2 and result["failed_files"] == 1
    assert "sensitive failure" not in (index_directory(root, identity) / "catalog.json").read_text()


def test_selected_subdirectory_and_symlinks_do_not_leak(source, tmp_path):
    root, identity, settings = source
    private = root / "private"
    private.mkdir()
    (private / "secret.md").write_text("# Secret heading\n")
    manifest = json.loads((root / "manifest.json").read_text())
    manifest["items"].append({"source_id": "secret", "local_path": "private/secret.md"})
    (root / "manifest.json").write_text(json.dumps(manifest))
    asyncio.run(build_index(settings, root, identity))
    public_result = asyncio.run(search(settings, [{"path": str(private)}], {}, ""))
    assert [row["relative_path"] for row in public_result["matches"]] == ["private/secret.md"]
    (private / "secret.md").unlink()
    external = tmp_path / "external.md"
    external.write_text("# External secret\n")
    (private / "secret.md").symlink_to(external)
    with pytest.raises(IndexUnavailable, match="PATH_INVALID"):
        asyncio.run(search(settings, [{"path": str(private)}], {}, "Secret"))


def test_installed_official_sdk_local_contract(source, monkeypatch):
    """Exercise real SDK persistence/export; replace only model-backed tree generation."""

    import fitz
    from pageindex.local_api import LocalAPI

    root, identity, settings = source
    source_pdf = root / "manual.pdf"
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "Recovery procedures: retry failed requests.")
    pdf.save(source_pdf)
    pdf.close()
    manifest = json.loads((root / "manifest.json").read_text())
    manifest["items"].append({"source_id": "manual", "local_path": "manual.pdf"})
    (root / "manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(
        LocalAPI,
        "_index_flash",
        lambda *_args: (
            [
                {
                    "title": "Recovery",
                    "node_id": "0001",
                    "start_index": 1,
                    "end_index": 1,
                    "summary": "Retry procedures",
                    "nodes": [],
                }
            ],
            "Operations manual",
        ),
    )
    settings = replace(settings, index_model="openai/contract-test")
    result = asyncio.run(build_index(settings, root, identity))
    assert result["failed_files"] == 0
    catalog = pipeline.load_catalog(index_directory(root, identity))
    assert catalog["documents"]["manual.pdf"]["sections"][0]["page"] == 1
    assert "retry failed requests" not in json.dumps(catalog)
    assert not list(index_directory(root, identity).glob("pdf-*"))
