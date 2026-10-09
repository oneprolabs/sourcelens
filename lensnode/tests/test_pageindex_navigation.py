"""Navigation indexes retain structure, isolate scopes, and refresh with sources."""

import asyncio
import json
from uuid import uuid4

import pytest

from lensnode.text_index.config import IndexUnavailable, TextIndexSettings, index_directory
from lensnode.text_index.pipeline import build_index
from lensnode.text_index.query import search


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    identity = str(uuid4())
    (root / ".sourcelens-datasource.json").write_text(json.dumps({"datasource_uuid": identity}))
    (root / "guide.md").write_text("# Operations\nPRIVATE_BODY_123\n## Recovery\nRetry requests.\n")
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "datasource_uuid": identity,
                "items": [{"source_id": "guide", "local_path": "guide.md", "status": "synced"}],
            }
        )
    )
    return root, identity, TextIndexSettings(tmp_path)


def test_build_reuses_structure_without_fulltext(source):
    root, identity, settings = source
    first = asyncio.run(build_index(settings, root, identity))
    second = asyncio.run(build_index(settings, root, identity))
    assert first["indexed_files"] == 1
    assert second["changed_files"] == 0
    directory = index_directory(root, identity)
    assert directory.parent.name == ".pageindex"
    assert not list(directory.rglob("*.sqlite3"))
    catalog = (directory / "catalog.json").read_text()
    assert "PRIVATE_BODY_123" not in catalog
    assert "Recovery" in catalog
    result = asyncio.run(search(settings, [{"path": str(root)}], {}, "Recovery"))
    assert result["mode"] == "navigation"
    assert result["matches"][0]["line"] == 3
    assert "Retry requests." not in json.dumps(result)
    (root / "guide.md").write_text("# New heading\nNew body\n")
    with pytest.raises(IndexUnavailable, match="STALE"):
        asyncio.run(search(settings, [{"path": str(root)}], {}, "New"))


def test_tree_listing_is_paginated_and_not_evidence(source):
    root, identity, settings = source
    asyncio.run(build_index(settings, root, identity))
    result = asyncio.run(search(settings, [{"path": str(root)}], {}, "", 1))
    assert result["matches"][0]["relative_path"] == "guide.md"
    assert result["matches"][0]["evidence"] is False
    assert result["matches"][0]["sections"][0]["title"] == "Operations"


def test_excluded_paths_never_disclose_titles(source):
    root, identity, settings = source
    asyncio.run(build_index(settings, root, identity))
    result = asyncio.run(
        search(
            settings,
            [{"path": str(root), "retrieval_scope": {"exclude_paths": ["**/guide.md", "guide.md"]}}],
            {},
            "Recovery",
        )
    )
    assert not result["matches"]


def test_corrupt_or_linked_catalog_rejected(source, tmp_path):
    root, identity, settings = source
    asyncio.run(build_index(settings, root, identity))
    path = index_directory(root, identity) / "catalog.json"
    path.write_text("invalid")
    with pytest.raises(IndexUnavailable):
        asyncio.run(search(settings, [{"path": str(root)}], {}, "Recovery"))
    path.unlink()
    target = tmp_path / "external.json"
    target.write_text("{}")
    path.symlink_to(target)
    with pytest.raises(IndexUnavailable):
        asyncio.run(build_index(settings, root, identity))


def test_large_tree_sections_can_be_navigated(source):
    root, identity, settings = source
    (root / "guide.md").write_text("".join(f"# Heading {index}\nBody\n" for index in range(45)))
    asyncio.run(build_index(settings, root, identity))
    first = asyncio.run(search(settings, [{"path": str(root), "name": "docs"}], {}, "", path="docs/guide.md"))
    assert first["matches"][0]["next_section_offset"] == 20
    second = asyncio.run(
        search(settings, [{"path": str(root), "name": "docs"}], {}, "", path="docs/guide.md", section_offset=20)
    )
    assert second["matches"][0]["sections"][0]["title"] == "Heading 20"
    assert not asyncio.run(search(settings, [{"path": str(root)}], {}, "", path="../external.md"))["matches"]


def test_corrupt_catalog_schema_is_rebuilt_without_breaking_sync(source):
    root, identity, settings = source
    asyncio.run(build_index(settings, root, identity))
    path = index_directory(root, identity) / "catalog.json"
    invalid = json.loads(path.read_text())
    invalid["documents"]["guide.md"] = "broken document schema"
    path.write_text(json.dumps(invalid))
    result = asyncio.run(build_index(settings, root, identity))
    assert result["changed_files"] == 1
    assert asyncio.run(search(settings, [{"path": str(root)}], {}, "Recovery"))["matches"]
