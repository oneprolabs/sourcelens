import pytest

from lensnode.plugin_package_loader import (
    PluginPackageLoadError,
    load_runtime_contract,
)


RUNTIME_SOURCE = '''
PLUGIN_API_VERSION = 1
PLUGIN_KEY = "example"
PLUGIN_VERSION = "1.0.0"

def http_origins(endpoint):
    return (endpoint,)

def build_tool(definition, executor):
    return definition, executor

def execute_tool(tool_key, client, arguments, secret, endpoint, config):
    return {"ok": True}

def build_datasource_command(snapshot, material, trigger):
    return {"source_type": "git"}

def sync_datasource(command, workspace_path, emit, execute):
    return execute(command, workspace_path, emit)
'''


def _package(tmp_path, source=RUNTIME_SOURCE, version="1.0.0"):
    package = tmp_path / "example"
    package.mkdir(parents=True)
    (package / "runtime.py").write_text(source, encoding="utf-8")
    return package


def test_loads_runtime_contract_from_configured_root(tmp_path):
    _package(tmp_path)

    contract = load_runtime_contract(
        "example",
        "1.0.0",
        roots=[tmp_path],
    )

    assert contract.plugin_key == "example"
    assert contract.plugin_version == "1.0.0"
    assert callable(contract.build_tool)
    assert callable(contract.execute_tool)
    assert callable(contract.build_datasource_command)
    assert callable(contract.sync_datasource)
    assert contract.http_origins("https://example.com") == (
        "https://example.com",
    )


def test_loads_tool_only_runtime_without_datasource_command(tmp_path):
    source = RUNTIME_SOURCE.replace(
        '\ndef build_datasource_command(snapshot, material, trigger):\n'
        '    return {"source_type": "git"}\n',
        "",
    ).replace(
        '\ndef sync_datasource(command, workspace_path, emit, execute):\n'
        '    return execute(command, workspace_path, emit)\n',
        "",
    )
    _package(tmp_path, source)

    contract = load_runtime_contract(
        "example",
        "1.0.0",
        roots=[tmp_path],
    )

    assert contract.build_datasource_command is None
    assert contract.sync_datasource is None


def test_rejects_runtime_identity_mismatch(tmp_path):
    _package(tmp_path, RUNTIME_SOURCE.replace('"example"', '"other"'))

    with pytest.raises(PluginPackageLoadError):
        load_runtime_contract("example", "1.0.0", roots=[tmp_path])


def test_rejects_runtime_version_mismatch(tmp_path):
    source = RUNTIME_SOURCE.replace(
        'PLUGIN_VERSION = "1.0.0"',
        'PLUGIN_VERSION = "1.0.1"',
    )
    _package(tmp_path, source)

    with pytest.raises(PluginPackageLoadError):
        load_runtime_contract("example", "1.0.0", roots=[tmp_path])


def test_rejects_symlinked_runtime_entrypoint(tmp_path):
    package = _package(tmp_path)
    external = tmp_path / "external.py"
    external.write_text(RUNTIME_SOURCE, encoding="utf-8")
    (package / "runtime.py").unlink()
    (package / "runtime.py").symlink_to(external)

    with pytest.raises(PluginPackageLoadError):
        load_runtime_contract("example", "1.0.0", roots=[tmp_path])


def test_rejects_path_like_plugin_identity(tmp_path):
    with pytest.raises(PluginPackageLoadError):
        load_runtime_contract("../example", "1.0.0", roots=[tmp_path])


def test_requires_complete_runtime_contract(tmp_path):
    _package(
        tmp_path,
        RUNTIME_SOURCE.replace("def execute_tool", "def missing"),
    )

    with pytest.raises(PluginPackageLoadError):
        load_runtime_contract("example", "1.0.0", roots=[tmp_path])


def test_reloads_runtime_when_package_content_changes(tmp_path):
    """Development package updates must not leave a stale runtime cached."""

    package = _package(tmp_path)
    first = load_runtime_contract(
        "example",
        "1.0.0",
        roots=[tmp_path],
    )
    updated = RUNTIME_SOURCE.replace(
        'return definition, executor',
        'return {"updated": True}, executor',
    )
    (package / "runtime.py").write_text(updated, encoding="utf-8")

    second = load_runtime_contract(
        "example",
        "1.0.0",
        roots=[tmp_path],
    )

    assert first.build_tool({"key": "one"}, None)[0] == {"key": "one"}
    assert second.build_tool({"key": "one"}, None)[0] == {
        "updated": True
    }
