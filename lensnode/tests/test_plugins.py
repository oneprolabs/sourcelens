from types import SimpleNamespace

from lensnode.plugins import collect_mcp_servers
from lensnode.plugins.codegraph import (
    CODEGRAPH_SERVER_NAME,
    CodeGraphPlugin,
    _codegraph_rebuild,
    _codegraph_sync,
    _ensure_codegraph_index,
    _refresh_codegraph_index,
    _has_indexable_code,
)
from lensnode.runtime_resources import _apply_feature_flags


def _config(**overrides):
    values = {
        "workspace_path": "/workspace",
        "mcp_enable_codegraph": True,
        "codegraph_command": "codegraph",
        "mcp_stdio_allowlist": ("codegraph",),
        "codegraph_init_timeout_s": 30,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_codegraph_plugin_contributes_stdio_server(monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text("print('hi')")
    monkeypatch.setattr(
        "lensnode.plugins.codegraph._ensure_codegraph_index",
        lambda *_args, **_kwargs: True,
    )

    servers = collect_mcp_servers(
        _config(workspace_path=str(tmp_path)),
        [],
        command={
            "task": "code_analysis",
            "target_dirs": [{"path": str(tmp_path)}],
        },
    )

    assert [server["name"] for server in servers] == [CODEGRAPH_SERVER_NAME]
    assert servers[0]["transport"] == "stdio"
    assert servers[0]["config"]["command"] == "codegraph"


def test_codegraph_plugin_scopes_index_to_session_root(monkeypatch, tmp_path):
    session = tmp_path / "sessions" / "session-1"
    session.mkdir(parents=True)
    captured = {}

    def ensure(config, workspace, emit_event=None):
        captured["workspace"] = workspace
        return True

    monkeypatch.setattr(
        "lensnode.plugins.codegraph._ensure_codegraph_index", ensure
    )
    config = _config()
    config.workspace_path = str(tmp_path)
    command = {"task": "code_analysis", "target_dirs": [{"path": str(session)}]}

    CodeGraphPlugin().contribute_mcp_servers(config, command=command)

    assert captured["workspace"] == session


def test_codegraph_plugin_indexes_selected_dir_outside_sessions(
    monkeypatch, tmp_path
):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("print('hi')")
    captured = {}

    def ensure(_config, workspace, emit_event=None):
        captured["workspace"] = workspace
        return True

    monkeypatch.setattr(
        "lensnode.plugins.codegraph._ensure_codegraph_index", ensure
    )
    command = {"task": "code_analysis", "target_dirs": [{"path": str(repo)}]}

    CodeGraphPlugin().contribute_mcp_servers(
        _config(workspace_path=str(tmp_path)),
        command=command,
    )

    assert captured["workspace"] == repo


def test_codegraph_plugin_reads_through_datasource_symlink(
    monkeypatch, tmp_path
):
    source = tmp_path / "datasources" / "source-version"
    source.mkdir(parents=True)
    (source / "app.py").write_text("print('hi')")
    link = tmp_path / "sessions" / "session-1" / "sources" / "ds_source"
    link.parent.mkdir(parents=True)
    link.symlink_to(source, target_is_directory=True)
    captured = {}

    def ensure(_config, workspace, emit_event=None):
        captured["workspace"] = workspace
        return True

    monkeypatch.setattr(
        "lensnode.plugins.codegraph._ensure_codegraph_index", ensure
    )
    command = {"task": "code_analysis", "target_dirs": [{"path": str(link)}]}

    CodeGraphPlugin().contribute_mcp_servers(
        _config(workspace_path=str(tmp_path)),
        command=command,
    )

    assert captured["workspace"] == source.resolve()


def test_codegraph_plugin_prefers_reference_over_subject(
    monkeypatch, tmp_path
):
    subject = tmp_path / "sessions" / "session-1" / "subject-documents"
    subject.mkdir(parents=True)
    repo = tmp_path / "sessions" / "session-1" / "sources" / "repo"
    repo.mkdir(parents=True)
    captured = {}

    def ensure(_config, workspace, emit_event=None):
        captured["workspace"] = workspace
        return True

    monkeypatch.setattr(
        "lensnode.plugins.codegraph._ensure_codegraph_index", ensure
    )
    command = {
        "task": "code_analysis",
        "target_dirs": [
            {"path": str(subject), "material_role": "subject"},
            {"path": str(repo)},
        ],
    }

    CodeGraphPlugin().contribute_mcp_servers(
        _config(workspace_path=str(tmp_path)),
        command=command,
    )

    assert captured["workspace"] == repo


def test_codegraph_plugin_skips_dir_outside_workspace(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "app.py").write_text("print('hi')")
    monkeypatch.setattr(
        "lensnode.plugins.codegraph._ensure_codegraph_index",
        lambda *_args, **_kwargs: True,
    )
    command = {
        "task": "code_analysis",
        "target_dirs": [{"path": str(outside)}],
    }

    servers = CodeGraphPlugin().contribute_mcp_servers(
        _config(workspace_path=str(workspace)),
        command=command,
    )

    assert servers == []


def test_codegraph_plugin_skips_without_target_dirs(monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text("print('hi')")
    checked = []
    monkeypatch.setattr(
        "lensnode.plugins.codegraph._ensure_codegraph_index",
        lambda *_args, **_kwargs: checked.append(True) or True,
    )

    servers = CodeGraphPlugin().contribute_mcp_servers(
        _config(workspace_path=str(tmp_path)),
        command={"task": "code_analysis"},
    )

    assert servers == []
    assert checked == []


def test_codegraph_detects_code_through_datasource_symlink(tmp_path):
    source = tmp_path / "datasources" / "source-version"
    source.mkdir(parents=True)
    (source / "app.py").write_text("print('hi')\n")
    session = tmp_path / "sessions" / "run-1" / "sources"
    session.mkdir(parents=True)
    (session / "ds_source").symlink_to(source, target_is_directory=True)

    assert _has_indexable_code(tmp_path / "sessions" / "run-1")


def test_codegraph_plugin_skips_general_chat_before_index_check(
    monkeypatch,
):
    index_checks = []
    monkeypatch.setattr(
        "lensnode.plugins.codegraph._ensure_codegraph_index",
        lambda *_args, **_kwargs: index_checks.append(True) or True,
    )

    servers = collect_mcp_servers(
        _config(),
        [],
        command={"task": "general_chat"},
    )

    assert servers == []
    assert index_checks == []


def test_codegraph_plugin_contributes_generic_agent_runtime_behavior():
    plugin = CodeGraphPlugin()
    tools = [
        SimpleNamespace(name="mcp__codegraph__codegraph_explore"),
    ]

    contribution = plugin.contribute_agent_runtime(
        _config(),
        {"task": "code_analysis"},
        tools,
    )

    assert contribution.prompt_guidance.startswith("CodeGraph is available")
    assert "empty, unavailable, or fails" in contribution.prompt_guidance
    assert "search_workspace" in contribution.prompt_guidance
    assert contribution.middleware == contribution.subagent_middleware
    assert contribution.always_visible_tool_prefixes == ("mcp__codegraph__",)
    assert (
        plugin.contribute_agent_runtime(
            _config(),
            {"task": "knowledge_qa"},
            tools,
        )
        is None
    )


def test_codegraph_plugin_disabled_when_toggle_off():
    config = _config(mcp_enable_codegraph=False)
    assert not CodeGraphPlugin().enabled(config)


def test_codegraph_plugin_disabled_when_not_allowlisted():
    config = _config(mcp_stdio_allowlist=("other",))
    assert not CodeGraphPlugin().enabled(config)


def test_codegraph_plugin_disabled_when_binary_missing(monkeypatch):
    monkeypatch.setattr(
        "lensnode.plugins.codegraph.shutil.which",
        lambda _command: None,
    )
    assert not CodeGraphPlugin().enabled(_config())


def test_codegraph_plugin_config_wins_over_contribution(monkeypatch):
    configured = {
        "name": "codegraph",
        "transport": "url",
        "endpoint": "https://mcp.example.com/api",
        "config": {},
        "load_config": {},
    }
    monkeypatch.setattr(
        "lensnode.plugins.codegraph._ensure_codegraph_index",
        lambda *_args, **_kwargs: True,
    )

    servers = collect_mcp_servers(_config(), [configured])

    assert servers == [configured]


def test_codegraph_index_skipped_without_indexable_code(tmp_path):
    (tmp_path / "notes.txt").write_text("no code here")

    result = collect_mcp_servers(
        _config(workspace_path=str(tmp_path)),
        [],
    )

    assert result == []
    assert not (tmp_path / ".codegraph").exists()


def test_codegraph_refresh_skips_sync_when_index_fresh(monkeypatch, tmp_path):
    (tmp_path / ".codegraph").mkdir()
    calls = []
    monkeypatch.setattr(
        "lensnode.plugins.codegraph._codegraph_status",
        lambda _config, _workspace: {
            "pending": False,
            "reindex": False,
            "state": "complete",
        },
    )
    monkeypatch.setattr(
        "lensnode.plugins.codegraph._codegraph_sync",
        lambda *_args, **_kwargs: calls.append("sync"),
    )
    monkeypatch.setattr(
        "lensnode.plugins.codegraph._codegraph_rebuild",
        lambda *_args, **_kwargs: calls.append("rebuild"),
    )

    result = _refresh_codegraph_index(_config(), tmp_path)

    assert result is True
    assert calls == []


def test_codegraph_refresh_syncs_when_pending(monkeypatch, tmp_path):
    (tmp_path / ".codegraph").mkdir()
    calls = []
    monkeypatch.setattr(
        "lensnode.plugins.codegraph._codegraph_status",
        lambda _config, _workspace: {
            "pending": True,
            "reindex": False,
            "state": "complete",
        },
    )
    monkeypatch.setattr(
        "lensnode.plugins.codegraph._codegraph_sync",
        lambda *_args, **_kwargs: calls.append("sync") or True,
    )

    result = _refresh_codegraph_index(_config(), tmp_path)

    assert result is True
    assert calls == ["sync"]


def test_codegraph_refresh_rebuilds_when_recommended(monkeypatch, tmp_path):
    (tmp_path / ".codegraph").mkdir()
    calls = []
    monkeypatch.setattr(
        "lensnode.plugins.codegraph._codegraph_status",
        lambda _config, _workspace: {
            "pending": False,
            "reindex": True,
            "state": "complete",
        },
    )
    monkeypatch.setattr(
        "lensnode.plugins.codegraph._codegraph_rebuild",
        lambda *_args, **_kwargs: calls.append("rebuild") or True,
    )

    result = _refresh_codegraph_index(_config(), tmp_path)

    assert result is True
    assert calls == ["rebuild"]


def test_codegraph_refresh_rebuilds_when_incomplete(monkeypatch, tmp_path):
    (tmp_path / ".codegraph").mkdir()
    calls = []
    monkeypatch.setattr(
        "lensnode.plugins.codegraph._codegraph_status",
        lambda _config, _workspace: {
            "pending": False,
            "reindex": False,
            "state": "incomplete",
        },
    )
    monkeypatch.setattr(
        "lensnode.plugins.codegraph._codegraph_rebuild",
        lambda *_args, **_kwargs: calls.append("rebuild") or True,
    )

    result = _refresh_codegraph_index(_config(), tmp_path)

    assert result is True
    assert calls == ["rebuild"]


def test_codegraph_refresh_keeps_index_when_status_unreadable(monkeypatch, tmp_path):
    (tmp_path / ".codegraph").mkdir()
    calls = []
    monkeypatch.setattr(
        "lensnode.plugins.codegraph._codegraph_status",
        lambda _config, _workspace: None,
    )
    monkeypatch.setattr(
        "lensnode.plugins.codegraph._codegraph_sync",
        lambda *_args, **_kwargs: calls.append("sync"),
    )
    monkeypatch.setattr(
        "lensnode.plugins.codegraph._codegraph_rebuild",
        lambda *_args, **_kwargs: calls.append("rebuild"),
    )

    result = _refresh_codegraph_index(_config(), tmp_path)

    assert result is True
    assert calls == []


def test_codegraph_sync_propagates_success(monkeypatch, tmp_path):
    ran = []
    monkeypatch.setattr(
        "lensnode.plugins.codegraph.subprocess.run",
        lambda *_args, **_kwargs: ran.append(_args),
    )

    assert _codegraph_sync(_config(), tmp_path) is True
    assert ran


def test_codegraph_status_parses_nested_reindex_flag(monkeypatch, tmp_path):
    import subprocess as real_subprocess

    monkeypatch.setattr(
        "lensnode.plugins.codegraph.subprocess.run",
        lambda *_args, **_kwargs: real_subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=(
                '{"initialized": true, "pendingChanges": {"added": 0, '
                '"modified": 0, "removed": 0}, "index": {'
                '"reindexRecommended": true, "state": "complete"}}'
            ).encode(),
        ),
    )

    from lensnode.plugins.codegraph import _codegraph_status

    status = _codegraph_status(_config(), tmp_path)

    assert status == {
        "pending": False,
        "reindex": True,
        "state": "complete",
    }


def test_codegraph_status_reports_pending_changes(monkeypatch, tmp_path):
    import subprocess as real_subprocess

    monkeypatch.setattr(
        "lensnode.plugins.codegraph.subprocess.run",
        lambda *_args, **_kwargs: real_subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=(
                '{"initialized": true, "pendingChanges": {"added": 2, '
                '"modified": 0, "removed": 0}, "index": {'
                '"reindexRecommended": false, "state": "complete"}}'
            ).encode(),
        ),
    )

    from lensnode.plugins.codegraph import _codegraph_status

    status = _codegraph_status(_config(), tmp_path)

    assert status["pending"] is True


def test_codegraph_status_returns_none_on_uninitialized(monkeypatch, tmp_path):
    import subprocess as real_subprocess

    monkeypatch.setattr(
        "lensnode.plugins.codegraph.subprocess.run",
        lambda *_args, **_kwargs: real_subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=('{"initialized": false}').encode(),
        ),
    )

    from lensnode.plugins.codegraph import _codegraph_status

    assert _codegraph_status(_config(), tmp_path) is None


def test_codegraph_sync_failure_returns_false(monkeypatch, tmp_path):
    import subprocess as real_subprocess

    def fail(*_args, **_kwargs):
        raise real_subprocess.CalledProcessError(1, "codegraph")

    monkeypatch.setattr("lensnode.plugins.codegraph.subprocess.run", fail)

    assert _codegraph_sync(_config(), tmp_path) is False


def test_codegraph_rebuild_failure_returns_false(monkeypatch, tmp_path):
    import subprocess as real_subprocess

    def fail(*_args, **_kwargs):
        raise real_subprocess.CalledProcessError(1, "codegraph")

    monkeypatch.setattr("lensnode.plugins.codegraph.subprocess.run", fail)

    assert _codegraph_rebuild(_config(), tmp_path) is False


def test_ensure_refreshes_existing_index_under_lock(monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text("print('hi')")
    (tmp_path / ".codegraph").mkdir()
    calls = []
    monkeypatch.setattr(
        "lensnode.plugins.codegraph._codegraph_status",
        lambda _config, _workspace: {
            "pending": True,
            "reindex": False,
            "state": "complete",
        },
    )
    monkeypatch.setattr(
        "lensnode.plugins.codegraph._codegraph_sync",
        lambda *_args, **_kwargs: calls.append("sync") or True,
    )
    monkeypatch.setattr(
        "lensnode.plugins.codegraph.subprocess.run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("init must not run for an existing index")
        ),
    )

    result = _ensure_codegraph_index(_config(), tmp_path)

    assert result is True
    assert calls == ["sync"]


def test_ensure_inits_missing_index(monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text("print('hi')")
    ran = []

    def fake_run(args, *_args, **_kwargs):
        ran.append(args)
        (tmp_path / ".codegraph").mkdir()
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("lensnode.plugins.codegraph.subprocess.run", fake_run)

    result = _ensure_codegraph_index(_config(), tmp_path)

    assert result is True
    assert ran and ran[0][1] == "init"


def test_ensure_concurrent_runs_serialize_on_lock(monkeypatch, tmp_path):
    import threading
    import time

    (tmp_path / "app.py").write_text("print('hi')")
    (tmp_path / ".codegraph").mkdir()
    active = 0
    max_active = 0
    counter_lock = threading.Lock()
    calls = []

    def fake_sync(*_args, **_kwargs):
        nonlocal active, max_active
        with counter_lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.05)
        with counter_lock:
            active -= 1
        calls.append("sync")
        return True

    def fake_status(_config, _workspace):
        return {
            "pending": True,
            "reindex": False,
            "state": "complete",
        }

    monkeypatch.setattr("lensnode.plugins.codegraph._codegraph_sync", fake_sync)
    monkeypatch.setattr("lensnode.plugins.codegraph._codegraph_status", fake_status)

    threads = [
        threading.Thread(
            target=_ensure_codegraph_index,
            args=(_config(), tmp_path),
        )
        for _ in range(4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert max_active == 1
    assert len(calls) == 4


def test_feature_flags_override_codegraph_off():
    config = _config(mcp_enable_codegraph=True)

    applied = _apply_feature_flags(config, {"codegraph": False})

    assert applied is config
    assert applied.mcp_enable_codegraph is False


def test_feature_flags_override_codegraph_on():
    config = _config(mcp_enable_codegraph=False)

    applied = _apply_feature_flags(config, {"codegraph": True})

    assert applied is config
    assert applied.mcp_enable_codegraph is True


def test_feature_flags_missing_or_unknown_are_noop():
    config = _config(mcp_enable_codegraph=True)

    assert _apply_feature_flags(config, None) is config
    assert _apply_feature_flags(config, {}) is config
    assert _apply_feature_flags(config, {"unlisted_feature": True}) is config
