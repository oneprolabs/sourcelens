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


def test_identical_manifest_records_are_processed_once(corpus):
    """Existing retained catalogs can contain repeated copies of one entry."""

    root, identity, manifest = corpus
    expected = collect_documents(root, identity)
    manifest["items"].append(dict(manifest["items"][0]))
    (root / "manifest.json").write_text(json.dumps(manifest))
    assert collect_documents(root, identity) == expected


def test_conflicting_source_identities_cannot_share_a_path(corpus):
    """Deduplication must not hide ambiguous provenance."""

    root, identity, manifest = corpus
    manifest["items"].append({**manifest["items"][0], "source_id": "another-source"})
    (root / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(IndexUnavailable, match="DUPLICATE_PATH"):
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
    settings = TextIndexSettings(tmp_path / "workspace")
    assert settings.profile != replace(settings, chunk_size=800).profile
    assert settings.profile != replace(settings, chunk_overlap=100).profile


def test_settings_need_only_workspace(tmp_path, monkeypatch):
    """Colocated indexes require no external state path configuration."""

    monkeypatch.setenv("LENSNODE_WORKSPACE_PATH", str(tmp_path))
    monkeypatch.delenv("LENSNODE_TEXT_INDEX_STATE_PATH", raising=False)
    assert TextIndexSettings.from_env().workspace_path == tmp_path


def test_index_location_follows_datasource_and_rejects_symlinks(tmp_path):
    """Moving a datasource preserves its index layout and identity isolation."""

    from lensnode.text_index.config import index_directory

    identity = str(uuid4())
    root = tmp_path / "source"
    root.mkdir()
    directory = index_directory(root, identity)
    assert directory.parent == root / ".cocoindex"
    assert directory != index_directory(root, str(uuid4()))
    assert directory.relative_to(root) == index_directory(tmp_path / "moved", identity).relative_to(tmp_path / "moved")
    (root / ".cocoindex").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(IndexUnavailable, match="PATH_INVALID"):
        index_directory(root, identity)


def test_sync_catalog_excludes_and_preserves_cocoindex(corpus):
    """Generated state is neither counted nor collected as source documents."""

    from lensnode.datasource_manifest import should_skip_dir
    from lensnode.datasource_sync import _count_file_extensions, _managed_workspace_conversion_items

    root, identity, manifest = corpus
    internal = root / ".cocoindex" / "prepared"
    internal.mkdir(parents=True)
    chunks = internal / "chunks.json"
    chunks.write_text('{"text": "private recovery"}')
    manifest["items"].append({"source_id": "index", "local_path": ".cocoindex/prepared/chunks.json"})
    (root / "manifest.json").write_text(json.dumps(manifest))
    assert len(collect_documents(root, identity)) == 2
    assert should_skip_dir(root / ".cocoindex", identity, [])
    assert _count_file_extensions(root) == {"md": 2}
    assert {item.local_path for item in _managed_workspace_conversion_items(root, [])} == {"public.md", "private.md"}
    assert chunks.exists()
