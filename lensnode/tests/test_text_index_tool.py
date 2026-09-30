"""Tool integration, fallback, public paths, and subprocess error boundaries."""

import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from lensnode.agent_tools import build_agent_tools
from lensnode.consulted_sources import ConsultedSources
from lensnode.text_index import tool as tool_module
from lensnode.text_index.config import IndexUnavailable


def test_cli_keeps_native_library_logs_out_of_json():
    """Rust writes directly to stdout's descriptor, bypassing redirect_stdout."""

    script = """
import os
import sys
from lensnode.text_index import __main__ as cli

async def execute(args):
    os.write(1, b'native library diagnostic\\n')
    print('Python library diagnostic')
    return {'status': 'ready'}

cli.execute = execute
sys.argv = ['text-index', 'query']
raise SystemExit(cli.main())
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == {"status": "ready"}
    assert "native library diagnostic" in result.stderr
    assert "Python library diagnostic" in result.stderr


def tools_for(root, *, enabled=True, emit=None, recorder=None):
    """Build the actual Agent tool registry with one selected mount."""

    command = {"target_dirs": [{"path": str(root), "name": "docs"}]}
    return {
        item.name: item
        for item in build_agent_tools(
            command,
            config=SimpleNamespace(text_index_enabled=enabled),
            emit_event=emit,
            source_recorder=recorder,
        )
    }


def test_disabled_mode_does_not_add_index_tool(tmp_path):
    assert "search_indexed_workspace" not in tools_for(tmp_path, enabled=False)


def test_index_tool_returns_readable_public_paths_and_records_span(tmp_path, monkeypatch):
    path = tmp_path / "guide.md"
    path.write_text("# Recovery\nRetry requests.\n")
    recorder = ConsultedSources({"target_dirs": [{"path": str(tmp_path), "name": "docs"}]})
    events = []
    tools = tools_for(tmp_path, emit=lambda *args: events.append(args), recorder=recorder)
    monkeypatch.setattr(
        tool_module,
        "query_worker",
        lambda *_args: {
            "mode": "indexed",
            "matches": [
                {
                    "path": str(path),
                    "line": 1,
                    "end_line": 2,
                    "text": "# Recovery\nRetry requests.",
                    "generation": "generation-1",
                }
            ],
        },
    )
    result = json.loads(tools["search_indexed_workspace"].invoke({"query": "recovery"}))
    assert result["matches"][0]["path"] == "docs/guide.md"
    read = json.loads(tools["read_workspace_file"].invoke({"path": "docs/guide.md"}))
    assert "Retry requests" in str(read)
    assert recorder.export_state()["searches"][0][1][0][2] == 2
    done = next(detail for event, detail in events if event == "tool.search_indexed_workspace.done")
    assert done["generations"] == ["generation-1"]


@pytest.mark.parametrize("reason", ["TEXT_INDEX_STALE", "TEXT_INDEX_UNAVAILABLE", "TEXT_INDEX_QUERY_TIMEOUT"])
def test_index_failure_falls_back_to_existing_search(tmp_path, monkeypatch, reason):
    (tmp_path / "guide.md").write_text("recovery retries requests")

    def fail(*_args):
        raise IndexUnavailable(reason)

    monkeypatch.setattr(tool_module, "query_worker", fail)
    result = json.loads(tools_for(tmp_path)["search_indexed_workspace"].invoke({"query": "recovery"}))
    assert result["index_fallback"] == reason
    assert result["matches"]


def test_empty_index_result_uses_literal_fallback(tmp_path, monkeypatch):
    (tmp_path / "guide.md").write_text("recovery retries requests")
    monkeypatch.setattr(tool_module, "query_worker", lambda *_args: {"mode": "indexed", "matches": []})
    result = json.loads(tools_for(tmp_path)["search_indexed_workspace"].invoke({"query": "recovery"}))
    assert result["index_fallback"] == "TEXT_INDEX_NO_MATCHES"
    assert result["matches"]


def test_public_path_cannot_read_another_mount(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    (tmp_path / "secret.txt").write_text("private")
    tool = tools_for(root)["read_workspace_file"]
    result = tool.invoke({"path": "docs/../secret.txt"})
    assert "private" not in result
    assert json.loads(result)["error"] == "PATH_NOT_ALLOWED"


def test_subprocess_error_cannot_leak_arbitrary_text(monkeypatch):
    monkeypatch.setattr(
        tool_module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout=json.dumps({"error": "credential value from external error"}),
        ),
    )
    with pytest.raises(IndexUnavailable, match="^TEXT_INDEX_WORKER_FAILED$"):
        tool_module.query_worker({"target_dirs": []}, "retry", 8)


def test_worker_timeout_is_bounded_and_reported(monkeypatch):
    def timeout(*_args, **kwargs):
        assert kwargs["timeout"] == 45
        raise subprocess.TimeoutExpired("worker", 45)

    monkeypatch.setattr(tool_module.subprocess, "run", timeout)
    with pytest.raises(IndexUnavailable, match="TIMEOUT"):
        tool_module.query_worker({}, "retry", 8)
