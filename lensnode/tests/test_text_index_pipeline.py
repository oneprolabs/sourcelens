"""Real CocoIndex and local FTS contracts without models or external storage."""

import asyncio
import fcntl
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

pytest.importorskip("cocoindex")

from lensnode.text_index import pipeline, store
from lensnode.text_index.config import IndexUnavailable, TextIndexSettings, digest, index_directory
from lensnode.text_index.pipeline import build_index
from lensnode.text_index.query import search


@pytest.fixture
def indexed_source(tmp_path):
    """Use a disposable workspace with datasource-local indexes."""

    workspace = tmp_path / "workspace"
    root = workspace / "source"
    root.mkdir(parents=True)
    identity = str(uuid4())
    (root / ".sourcelens-datasource.json").write_text(json.dumps({"datasource_uuid": identity}))
    manifest = {"datasource_uuid": identity, "stats": {"scan_complete": True}, "items": []}
    for name, text in [
        ("recovery.md", "# Recovery\nRetry failed requests.\n失败请求重试策略。\n"),
        ("private.md", "# Internal\nPrivate recovery procedures.\n"),
    ]:
        (root / name).write_text(text)
        manifest["items"].append({"source_id": name, "local_path": name, "status": "synced"})
    (root / "manifest.json").write_text(json.dumps(manifest))
    settings = TextIndexSettings(workspace)
    return settings, root, identity, manifest


def test_real_pipeline_reuses_prepared_files_and_updates_fts(indexed_source, monkeypatch):
    original_splitter = pipeline.RecursiveSplitter
    processed = []

    class CountingSplitter:
        """Observe actual CocoIndex processing, independently of FTS writes."""

        def split(self, text, **kwargs):
            processed.append(text)
            return original_splitter().split(text, **kwargs)

    monkeypatch.setattr(pipeline, "RecursiveSplitter", CountingSplitter)

    async def scenario():
        settings, root, identity, manifest = indexed_source
        first = await build_index(settings, root, identity)
        assert first["changed_files"] == 2
        assert len(processed) == 2
        prepared = index_directory(root, identity) / settings.profile / "prepared"
        old_mtimes = {file.name: file.stat().st_mtime_ns for file in prepared.iterdir()}
        second = await build_index(settings, root, identity)
        assert first["generation"] == second["generation"]
        assert second["changed_files"] == 0
        assert len(processed) == 2
        assert {file.name: file.stat().st_mtime_ns for file in prepared.iterdir()} == old_mtimes
        command_scope = [{"path": str(root), "name": "docs", "retrieval_scope": {"exclude_paths": ["private.md"]}}]
        result = await search(settings, command_scope, {}, "recovery")
        assert [row["relative_path"] for row in result["matches"]] == ["recovery.md"]
        assert result["matches"][0]["line"] == 1
        assert (await search(settings, command_scope, {}, "重试"))["matches"]
        (root / "recovery.md").write_text("# Recovery\nNew exponential backoff.\n")
        with pytest.raises(IndexUnavailable, match="STALE"):
            await search(settings, command_scope, {}, "recovery")
        updated = await build_index(settings, root, identity)
        assert updated["changed_files"] == 1
        assert len(processed) == 3
        assert (prepared / (digest("private.md") + ".json")).stat().st_mtime_ns == old_mtimes[
            digest("private.md") + ".json"
        ]
        manifest["items"][0]["status"] = "deleted"
        (root / "manifest.json").write_text(json.dumps(manifest))
        deleted = await build_index(settings, root, identity)
        assert deleted["deleted_files"] == 1
        assert not (prepared / (digest("recovery.md") + ".json")).exists()
        published = index_directory(root, identity) / "index.sqlite3"
        assert store.search_index(published, ["recovery.md"], "recovery", settings.profile, 8) == []
        with pytest.raises(IndexUnavailable, match="SCOPE_UNAVAILABLE"):
            await search(settings, command_scope, {}, "recovery")

    asyncio.run(scenario())


def test_publication_failure_preserves_previous_generation(indexed_source, monkeypatch):
    async def scenario():
        settings, root, identity, _ = indexed_source
        first = await build_index(settings, root, identity)
        (root / "private.md").write_text("Modified recovery text")
        original_replace = store.os.replace

        def fail_replace(*_args):
            raise OSError("Simulated atomic publication failure")

        monkeypatch.setattr(store.os, "replace", fail_replace)
        with pytest.raises(OSError):
            await build_index(settings, root, identity)
        scope = [{"path": str(root), "retrieval_scope": {"exclude_paths": ["private.md"]}}]
        result = await search(settings, scope, {}, "recovery")
        assert {row["generation"] for row in result["matches"]} == {first["generation"]}
        monkeypatch.setattr(store.os, "replace", original_replace)
        recovered = await build_index(settings, root, identity)
        assert recovered["generation"] != first["generation"]
        with pytest.raises(IndexUnavailable, match="PROFILE_STALE"):
            await search(replace(settings, chunk_size=800), scope, {}, "recovery")

    asyncio.run(scenario())


def test_competing_writer_is_rejected(indexed_source):
    async def scenario():
        settings, root, identity, _ = indexed_source
        directory = index_directory(root, identity)
        directory.mkdir(parents=True)
        with (directory / "writer.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with pytest.raises(IndexUnavailable, match="BUSY"):
                await build_index(settings, root, identity)

    asyncio.run(scenario())


def test_source_changes_before_publication_are_rejected(indexed_source, monkeypatch):
    async def scenario():
        settings, root, identity, _ = indexed_source
        await build_index(settings, root, identity)
        original_collect = pipeline.collect_documents
        count = 0

        def collect(*args):
            nonlocal count
            count += 1
            if count == 2:
                (root / "recovery.md").write_text("Recovery changed while indexing")
            return original_collect(*args)

        monkeypatch.setattr(pipeline, "collect_documents", collect)
        with pytest.raises(IndexUnavailable, match="SOURCE_CHANGED"):
            await build_index(settings, root, identity)

    asyncio.run(scenario())


def test_incomplete_manifest_preserves_published_index(indexed_source):
    async def scenario():
        settings, root, identity, manifest = indexed_source
        await build_index(settings, root, identity)
        published = index_directory(root, identity) / "index.sqlite3"
        before = published.read_bytes()
        manifest["items"] = []
        manifest["stats"]["scan_complete"] = False
        (root / "manifest.json").write_text(json.dumps(manifest))
        with pytest.raises(IndexUnavailable, match="INCOMPLETE"):
            await build_index(settings, root, identity)
        assert before == published.read_bytes()

    asyncio.run(scenario())


def test_cli_and_agent_tool_end_to_end(indexed_source, monkeypatch):
    """Run the actual index CLI and the Agent's isolated query subprocess."""

    from types import SimpleNamespace

    from lensnode.agent_tools import build_agent_tools

    settings, root, identity, _ = indexed_source
    monkeypatch.setenv("LENSNODE_WORKSPACE_PATH", str(settings.workspace_path))
    monkeypatch.delenv("LENSNODE_TEXT_INDEX_STATE_PATH", raising=False)
    command = [
        sys.executable,
        "-m",
        "lensnode.text_index",
        "index",
        "--root",
        str(root),
        "--datasource",
        identity,
    ]
    built = subprocess.run(command, capture_output=True, text=True, timeout=30, check=True)
    assert json.loads(built.stdout)["status"] == "ready"
    repeated = subprocess.run(command, capture_output=True, text=True, timeout=30, check=True)
    assert json.loads(repeated.stdout)["changed_files"] == 0
    tools = {
        tool.name: tool
        for tool in build_agent_tools(
            {"target_dirs": [{"path": str(root), "name": "docs"}]},
            config=SimpleNamespace(text_index_enabled=True),
        )
    }
    result = json.loads(tools["search_indexed_workspace"].invoke({"query": "重试"}))
    assert result["mode"] == "indexed"
    assert result["matches"][0]["path"] == "docs/recovery.md"
    read = tools["read_workspace_file"].invoke({"path": result["matches"][0]["path"]})
    assert "重试" in read
    (root / "recovery.md").write_text("Recovery has changed; use the updated file.")
    fallback = json.loads(tools["search_indexed_workspace"].invoke({"query": "重试"}))
    assert fallback["index_fallback"] == "TEXT_INDEX_STALE"


def test_cocoindex_component_error_blocks_publication(indexed_source, monkeypatch):
    """CocoIndex background failures must not look like a successful build."""

    async def scenario():
        settings, root, identity, _ = indexed_source
        first = await build_index(settings, root, identity)
        (root / "private.md").write_text("Changed content")

        class BrokenSplitter:
            """Fail only the changed component to exercise partial updates."""

            def split(self, *_args, **_kwargs):
                raise ValueError("Simulated preparation failure")

        monkeypatch.setattr(pipeline, "RecursiveSplitter", BrokenSplitter)
        with pytest.raises(IndexUnavailable, match="INCOMPLETE"):
            await build_index(settings, root, identity)
        scope = [{"path": str(root), "retrieval_scope": {"exclude_paths": ["private.md"]}}]
        result = await search(settings, scope, {}, "recovery")
        assert {row["generation"] for row in result["matches"]} == {first["generation"]}

    asyncio.run(scenario())


def test_query_filesystem_work_is_bounded_by_hits(indexed_source, monkeypatch):
    """Unrelated catalog files must not cause live filesystem authorization."""

    async def scenario():
        settings, root, identity, manifest = indexed_source
        for index in range(80):
            name = f"unrelated-{index}.md"
            (root / name).write_text("Unrelated catalog material\n")
            manifest["items"].append({"source_id": name, "local_path": name})
        (root / "manifest.json").write_text(json.dumps(manifest))
        await build_index(settings, root, identity)
        calls = []
        original_stat = Path.stat

        def counting_stat(path, *args, **kwargs):
            calls.append(path)
            return original_stat(path, *args, **kwargs)

        monkeypatch.setattr(Path, "stat", counting_stat)
        result = await search(settings, [{"path": str(root)}], {}, "recovery")
        assert len(result["matches"]) == 2
        assert not any(path.name.startswith("unrelated-") for path in calls)
        assert len(calls) < 160

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["file_link", "directory_link", "nested_source", "policy", "extension", "deleted"])
def test_repeated_query_rechecks_live_authorization(indexed_source, change):
    """A previous successful query must not authorize a changed binding or file."""

    async def scenario():
        settings, root, identity, manifest = indexed_source
        directory = root / "nested"
        directory.mkdir()
        source = directory / "guard.md"
        source.write_text("UniqueScopeGuard material\n")
        manifest["items"].append({"source_id": "guard", "local_path": "nested/guard.md"})
        (root / "manifest.json").write_text(json.dumps(manifest))
        await build_index(settings, root, identity)
        scope = [{"path": str(root)}]
        policy = {}
        assert (await search(settings, scope, policy, "UniqueScopeGuard"))["matches"]
        if change == "file_link":
            target = root / "uncataloged.md"
            source.rename(target)
            source.symlink_to(target)
        elif change == "directory_link":
            target = root / "uncataloged"
            directory.rename(target)
            directory.symlink_to(target, target_is_directory=True)
        elif change == "nested_source":
            (directory / ".sourcelens-datasource.json").write_text(json.dumps({"datasource_uuid": str(uuid4())}))
        elif change == "policy":
            policy["exclude_paths"] = ["nested/**"]
        elif change == "extension":
            scope[0]["retrieval_scope"] = {"exclude_extensions": ["md"]}
        else:
            manifest["items"][-1]["status"] = "deleted"
            (root / "manifest.json").write_text(json.dumps(manifest))
        if change == "extension":
            with pytest.raises(IndexUnavailable, match="SCOPE_UNAVAILABLE"):
                await search(settings, scope, policy, "UniqueScopeGuard")
        else:
            assert not (await search(settings, scope, policy, "UniqueScopeGuard"))["matches"]

    asyncio.run(scenario())


def test_ranked_candidates_are_authorized_before_text_is_loaded(indexed_source, monkeypatch):
    """Rejecting the first candidate must refill the result without reading its text."""

    async def scenario():
        settings, root, identity, _ = indexed_source
        await build_index(settings, root, identity)
        statements = []
        original_connect = store.connect_readonly

        def connect(path):
            connection = original_connect(path)
            connection.set_trace_callback(statements.append)
            return connection

        monkeypatch.setattr(store, "connect_readonly", connect)
        candidates = []

        def authorize(path):
            candidates.append(path)
            return len(candidates) > 1

        rows = store.search_index(
            index_directory(root, identity) / "index.sqlite3",
            ["recovery.md", "private.md"],
            "recovery",
            settings.profile,
            1,
            path_allowed=authorize,
        )
        assert len(candidates) == 2
        assert len(rows) == 1
        assert rows[0]["path"] == candidates[1]
        assert len([sql for sql in statements if sql.startswith("SELECT * FROM chunks")]) == 1

    asyncio.run(scenario())


def test_moved_datasource_reuses_its_published_index(indexed_source):
    """Relocating documents and state keeps queries usable without rebuilding."""

    async def scenario():
        settings, root, identity, _ = indexed_source
        built = await build_index(settings, root, identity)
        moved = root.rename(root.parent / "moved")
        result = await search(settings, [{"path": str(moved)}], {}, "重试")
        assert result["matches"][0]["path"] == str(moved / "recovery.md")
        assert result["matches"][0]["generation"] == built["generation"]
        assert (await build_index(settings, moved, identity))["changed_files"] == 0

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "linked_path", ["profile", "prepared", "cocoindex.db", "writer.lock", "index.sqlite3", ".gitignore"]
)
def test_build_rejects_linked_state_paths(indexed_source, tmp_path, linked_path):
    """Datasource contents cannot redirect index writes outside their directory."""

    settings, root, identity, _ = indexed_source
    directory = index_directory(root, identity)
    profile = directory / settings.profile
    profile.mkdir(parents=True)
    candidates = {
        "profile": profile,
        "prepared": profile / "prepared",
        "cocoindex.db": profile / "cocoindex.db",
        "writer.lock": directory / "writer.lock",
        "index.sqlite3": directory / "index.sqlite3",
        ".gitignore": directory.parent / ".gitignore",
    }
    candidate = candidates[linked_path]
    if candidate.is_dir():
        candidate.rmdir()
    candidate.symlink_to(tmp_path)
    with pytest.raises(IndexUnavailable, match="PATH_INVALID"):
        asyncio.run(build_index(settings, root, identity))
