"""Manifest, content consistency, and authorization boundaries for indexing."""

import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from lensnode.text_index.config import IndexUnavailable, TextIndexSettings
from lensnode.text_index.documents import authorized_scopes, collect_documents, generation_for


@pytest.fixture
def corpus(tmp_path):
    """Create two cataloged documents in a marked datasource."""

    root = tmp_path / "source"
    root.mkdir()
    identity = str(uuid4())
    (root / ".sourcelens-datasource.json").write_text(json.dumps({"datasource_uuid": identity}))
    items = []
    for name in ["public.md", "private.md"]:
        (root / name).write_text(f"# {name}\nRecovery retries failed jobs.\n")
        items.append({"source_id": name, "local_path": name, "status": "synced"})
    manifest = {"datasource_uuid": identity, "items": items, "stats": {"scan_complete": True}}
    (root / "manifest.json").write_text(json.dumps(manifest))
    return root, identity, manifest


def test_generation_changes_with_source_and_profile(corpus):
    root, identity, _ = corpus
    before = generation_for(collect_documents(root, identity), "profile")
    assert before == generation_for(collect_documents(root, identity), "profile")
    assert before != generation_for(collect_documents(root, identity), "other-profile")
    (root / "public.md").write_text("Changed source\n")
    assert before != generation_for(collect_documents(root, identity), "profile")


def test_incomplete_scan_cannot_publish_deletions(corpus):
    root, identity, manifest = corpus
    manifest["items"] = []
    manifest["stats"]["scan_complete"] = False
    (root / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(IndexUnavailable, match="INCOMPLETE"):
        collect_documents(root, identity)


def test_missing_retained_and_confirmed_deleted_removed(corpus):
    root, identity, manifest = corpus
    manifest["items"][0]["status"] = "missing"
    manifest["items"][1]["status"] = "deleted"
    (root / "manifest.json").write_text(json.dumps(manifest))
    assert [doc.path for doc in collect_documents(root, identity)] == ["public.md"]


@pytest.mark.parametrize("path", ["../outside.md", "/outside.md", "nested\\outside.md"])
def test_manifest_cannot_escape_datasource(corpus, path):
    root, identity, manifest = corpus
    manifest["items"][0]["local_path"] = path
    (root / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(IndexUnavailable, match="PATH_INVALID"):
        collect_documents(root, identity)


def test_manifest_symlink_cannot_read_unselected_file(corpus):
    root, identity, _ = corpus
    (root / "public.md").unlink()
    (root / "public.md").symlink_to(root / "private.md")
    with pytest.raises(IndexUnavailable, match="PATH_INVALID"):
        collect_documents(root, identity)


def test_internal_files_are_never_indexed(corpus):
    root, identity, manifest = corpus
    (root / ".env").write_text("PRIVATE=value")
    manifest["items"].append({"local_path": ".env", "source_id": "secret"})
    (root / "manifest.json").write_text(json.dumps(manifest))
    assert len(collect_documents(root, identity)) == 2


def test_conversion_requires_current_source_hash(corpus):
    root, identity, manifest = corpus
    source = root / "manual.pdf"
    source.write_bytes(b"PDF fixture")
    sidecar = root / "manual.pdf.sourcelens"
    sidecar.mkdir()
    (sidecar / "content.md").write_text("# Converted recovery instructions\n")
    (sidecar / "meta.json").write_text(
        json.dumps(
            {
                "source": {"sha256": hashlib.sha256(source.read_bytes()).hexdigest()},
                "conversion": {"status": "success"},
            }
        )
    )
    manifest["items"].append({"source_id": "pdf", "local_path": "manual.pdf"})
    (root / "manifest.json").write_text(json.dumps(manifest))
    doc = next(doc for doc in collect_documents(root, identity) if doc.converted)
    assert doc.path == "manual.pdf"
    source.write_bytes(b"New PDF")
    with pytest.raises(IndexUnavailable, match="CONVERSION_STALE"):
        collect_documents(root, identity)


def test_query_filters_are_derived_from_selected_scope(corpus):
    root, _, _ = corpus
    settings = SimpleNamespace(workspace_path=root.parent)
    bindings = [{"path": str(root), "retrieval_scope": {"exclude_paths": ["private.md"]}}]
    scopes = authorized_scopes(settings, bindings, {})
    assert list(scopes[0][2]) == ["public.md"]
    assert authorized_scopes(settings, [{"path": "/"}], {}) == []


def test_pipeline_profile_tracks_chunking_parameters(tmp_path):
    settings = TextIndexSettings(tmp_path / "index", tmp_path / "workspace")
    assert settings.profile != replace(settings, chunk_size=800).profile
    assert settings.profile != replace(settings, chunk_overlap=100).profile
