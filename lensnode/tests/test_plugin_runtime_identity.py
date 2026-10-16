"""Keep executed Plugin code attributable to its built source bytes."""

import hashlib
import json
import os
import py_compile
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from lensnode.plugin_package_loader import PluginPackageLoadError, load_plugin_support, load_runtime_contract


RUNTIME_SOURCE = '''
PLUGIN_API_VERSION = 1
PLUGIN_KEY = "example"
PLUGIN_VERSION = "1.0.0"

def execute_tool(*args):
    return {"revision": "one"}
'''
MANIFEST_NAME = ".runtime-manifest.json"


def _install(root):
    """Create a minimal runtime and shared module without a release registry."""

    runtime = root / "example" / "runtime.py"
    runtime.parent.mkdir()
    runtime.write_text(RUNTIME_SOURCE, encoding="utf-8")
    support = root / "_shared" / "example.py"
    support.parent.mkdir()
    support.write_text('REVISION = "one"\n', encoding="utf-8")
    return runtime, support


def _manifest(root):
    """Freeze the source identity without relying on the production builder."""

    files = {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*.py"))
    }
    path = root / MANIFEST_NAME
    path.write_text(json.dumps({"schema_version": 1, "files": files}), encoding="utf-8")
    return path


@pytest.mark.parametrize("module", ["runtime", "support"])
def test_equal_size_same_mtime_updates_execute_new_source_instead_of_old_bytecode(tmp_path, monkeypatch, module):
    """A timestamp-valid pyc cannot override the bytes used for identity checks."""

    runtime, support = _install(tmp_path)
    monkeypatch.setenv("LENS_PLUGIN_ROOTS", str(tmp_path))
    path = runtime if module == "runtime" else support
    before = path.stat()
    py_compile.compile(str(path), doraise=True)
    path.write_text(path.read_text().replace('"one"', '"two"'), encoding="utf-8")
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))

    if module == "runtime":
        contract = load_runtime_contract("example", "1.0.0")
        assert contract.execute_tool()["revision"] == "two"
    else:
        assert load_plugin_support("example").REVISION == "two"


def test_loaded_identity_contains_full_source_and_shared_support_hashes(tmp_path, monkeypatch):
    """The recorded code identity must describe the source actually executed."""

    runtime, support = _install(tmp_path)
    monkeypatch.setenv("LENS_PLUGIN_ROOTS", str(tmp_path))
    manifest = _manifest(tmp_path)

    contract = load_runtime_contract("example", "1.0.0")
    shared = load_plugin_support("example")

    assert contract.runtime_sha256 == hashlib.sha256(runtime.read_bytes()).hexdigest()
    assert contract.support_sha256 == {"_shared/example.py": hashlib.sha256(support.read_bytes()).hexdigest()}
    assert contract.manifest_sha256 == hashlib.sha256(manifest.read_bytes()).hexdigest()
    assert shared.__lens_source_sha256__ == contract.support_sha256["_shared/example.py"]


@pytest.mark.parametrize("changed", ["runtime", "support"])
def test_runtime_loading_rejects_source_that_differs_from_build_manifest(tmp_path, monkeypatch, changed):
    """Neither cached runtime code nor its shared support may drift from a release."""

    runtime, support = _install(tmp_path)
    monkeypatch.setenv("LENS_PLUGIN_ROOTS", str(tmp_path))
    _manifest(tmp_path)
    load_runtime_contract("example", "1.0.0")
    path = runtime if changed == "runtime" else support
    path.write_text(path.read_text().replace('"one"', '"two"'), encoding="utf-8")

    with pytest.raises(PluginPackageLoadError, match="manifest"):
        load_runtime_contract("example", "1.0.0")


def test_shared_loading_rejects_source_that_differs_from_build_manifest(tmp_path, monkeypatch):
    """A late shared import must independently enforce the installed build manifest."""

    _, support = _install(tmp_path)
    monkeypatch.setenv("LENS_PLUGIN_ROOTS", str(tmp_path))
    _manifest(tmp_path)
    support.write_text('REVISION = "two"\n', encoding="utf-8")

    with pytest.raises(PluginPackageLoadError, match="manifest"):
        load_plugin_support("example")


@pytest.mark.parametrize(
    "manifest",
    ["not JSON", '{"schema_version": 2, "files": {}}', '{"schema_version": 1, "files": {}}'],
)
def test_runtime_loading_rejects_invalid_or_incomplete_manifest(tmp_path, monkeypatch, manifest):
    """A present but unusable build manifest must not silently disable verification."""

    _install(tmp_path)
    monkeypatch.setenv("LENS_PLUGIN_ROOTS", str(tmp_path))
    (tmp_path / MANIFEST_NAME).write_text(manifest, encoding="utf-8")

    with pytest.raises(PluginPackageLoadError, match="manifest"):
        load_runtime_contract("example", "1.0.0")


@pytest.mark.parametrize("loader", ["runtime", "support"])
def test_production_mode_requires_manifest(tmp_path, monkeypatch, loader):
    """Removing the release manifest must not turn a production image into development mode."""

    _install(tmp_path)
    monkeypatch.setenv("LENS_PLUGIN_ROOTS", str(tmp_path))
    monkeypatch.setenv("LENS_PLUGIN_MANIFEST_REQUIRED", "1")

    with pytest.raises(PluginPackageLoadError, match="manifest"):
        if loader == "runtime":
            load_runtime_contract("example", "1.0.0")
        else:
            load_plugin_support("example")


def test_build_manifest_records_all_python_sources_and_is_deterministic(tmp_path):
    """The image build freezes shared support together with per-Plugin runtime code."""

    _install(tmp_path)
    script = Path(__file__).resolve().parents[2] / "scripts" / "build_plugin_manifest.py"

    subprocess.run([sys.executable, str(script), str(tmp_path)], check=True, capture_output=True, text=True)
    first = (tmp_path / MANIFEST_NAME).read_bytes()
    subprocess.run([sys.executable, str(script), str(tmp_path)], check=True, capture_output=True, text=True)

    assert (tmp_path / MANIFEST_NAME).read_bytes() == first
    assert json.loads(first)["files"] == {
        "_shared/example.py": hashlib.sha256((tmp_path / "_shared/example.py").read_bytes()).hexdigest(),
        "example/runtime.py": hashlib.sha256((tmp_path / "example/runtime.py").read_bytes()).hexdigest(),
    }


@pytest.mark.parametrize("top_level", [False, True])
def test_runtime_support_imports_are_bound_to_the_runtime_root(tmp_path, monkeypatch, top_level):
    """Another configured root cannot override a runtime's actual support identity."""

    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    _, wrong_support = _install(first)
    (first / "example" / "runtime.py").unlink()
    (first / "example").rmdir()
    wrong_support.write_text('REVISION = "wrong"\n')
    runtime, _ = _install(second)
    source = RUNTIME_SOURCE.replace(
        'return {"revision": "one"}',
        'from lensnode.plugin_package_loader import load_plugin_support\n'
        '    return {"revision": load_plugin_support("example").REVISION}',
    )
    if top_level:
        source = (
            'from lensnode.plugin_package_loader import load_plugin_support\n'
            'SUPPORT = load_plugin_support("example")\n'
            + RUNTIME_SOURCE.replace('return {"revision": "one"}', 'return {"revision": SUPPORT.REVISION}')
        )
    runtime.write_text(source)
    monkeypatch.setenv("LENS_PLUGIN_ROOTS", f"{first},{second}")

    contract = load_runtime_contract("example", "1.0.0")

    assert contract.execute_tool()["revision"] == "one"


def test_missing_runtime_support_never_falls_through_to_another_root(tmp_path, monkeypatch):
    """A partial deployment fails instead of silently mixing installed releases."""

    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    _install(first)
    (first / "example" / "runtime.py").unlink()
    (first / "example").rmdir()
    runtime, own_support = _install(second)
    own_support.unlink()
    own_support.parent.rmdir()
    runtime.write_text(RUNTIME_SOURCE.replace(
        'return {"revision": "one"}',
        'from lensnode.plugin_package_loader import load_plugin_support\n'
        '    return {"revision": load_plugin_support("example").REVISION}',
    ))
    monkeypatch.setenv("LENS_PLUGIN_ROOTS", f"{first},{second}")
    contract = load_runtime_contract("example", "1.0.0")

    with pytest.raises(PluginPackageLoadError, match="support"):
        contract.execute_tool()


def test_shared_source_change_after_runtime_load_is_rejected_before_execution(tmp_path, monkeypatch):
    """A function cannot consume changed shared code under its earlier identity."""

    runtime, shared = _install(tmp_path)
    runtime.write_text(RUNTIME_SOURCE.replace(
        'return {"revision": "one"}',
        'from lensnode.plugin_package_loader import load_plugin_support\n'
        '    return {"revision": load_plugin_support("example").REVISION}',
    ))
    monkeypatch.setenv("LENS_PLUGIN_ROOTS", str(tmp_path))
    contract = load_runtime_contract("example", "1.0.0")
    shared.write_text('REVISION = "two"\n')

    with pytest.raises(PluginPackageLoadError, match="identity"):
        contract.execute_tool()


def test_concurrent_exports_keep_their_own_root_and_restore_outer_context(tmp_path, monkeypatch):
    """Runs on worker threads cannot borrow another release's shared support context."""

    contracts = []
    roots = [tmp_path / "first", tmp_path / "second"]
    for index, root in enumerate(roots):
        root.mkdir()
        runtime, shared = _install(root)
        shared.write_text(f'REVISION = "{index}"\n')
        runtime.write_text(RUNTIME_SOURCE.replace(
            'return {"revision": "one"}',
            'from lensnode.plugin_package_loader import load_plugin_support\n'
            '    args[0].wait()\n'
            '    return {"revision": load_plugin_support("example").REVISION}',
        ))
        contracts.append(load_runtime_contract("example", "1.0.0", roots=[root]))
    monkeypatch.setenv("LENS_PLUGIN_ROOTS", ",".join(str(root) for root in roots))
    barrier = Barrier(2)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda contract: contract.execute_tool(barrier), contracts))

    assert results == [{"revision": "0"}, {"revision": "1"}]
    with pytest.raises(PluginPackageLoadError, match="inconsistent"):
        load_plugin_support("example")


def test_runtime_module_cache_tracks_top_level_shared_import_identity(tmp_path, monkeypatch):
    """Reloading an unchanged runtime cannot retain shared code from its old identity."""

    runtime, shared = _install(tmp_path)
    runtime.write_text(
        'from lensnode.plugin_package_loader import load_plugin_support\n'
        'SUPPORT = load_plugin_support("example")\n'
        + RUNTIME_SOURCE.replace('return {"revision": "one"}', 'return {"revision": SUPPORT.REVISION}')
    )
    monkeypatch.setenv("LENS_PLUGIN_ROOTS", str(tmp_path))
    first = load_runtime_contract("example", "1.0.0")
    shared.write_text('REVISION = "two"\n')
    second = load_runtime_contract("example", "1.0.0")

    assert first.execute_tool()["revision"] == "one"
    assert second.execute_tool()["revision"] == "two"
    assert first.runtime_sha256 == second.runtime_sha256
    assert first.support_sha256 != second.support_sha256
