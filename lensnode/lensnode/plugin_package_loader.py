"""Load trusted, versioned Plugin runtimes from fixed entrypoints."""

import hashlib
import importlib.util
import json
import os
import re
from contextvars import ContextVar
from dataclasses import dataclass
from functools import lru_cache, update_wrapper
from pathlib import Path

PLUGIN_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
PLUGIN_VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
TOOL_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
MAX_RUNTIME_BYTES = 2 * 1024 * 1024
_ACTIVE_SOURCE = ContextVar("plugin_source", default=None)


class PluginPackageLoadError(RuntimeError):
    """Raised when installed Plugin code violates the host contract."""


@dataclass(frozen=True)
class _PluginSource:
    """Bind deferred support imports to one verified runtime source snapshot."""

    root: Path
    support_sha256: tuple
    manifest_sha256: str


class _SourceBoundExport:
    """Call an export inside its source root while retaining its introspection data."""

    def __init__(self, callback, source):
        self.callback = callback
        self.source = source
        update_wrapper(self, callback, updated=())

    def __call__(self, *args, **kwargs):
        token = _ACTIVE_SOURCE.set(self.source)
        try:
            return self.callback(*args, **kwargs)
        finally:
            _ACTIVE_SOURCE.reset(token)

    def __getattr__(self, name):
        return getattr(self.callback, name)


@dataclass(frozen=True)
class PluginRuntimeContract:
    """Validated runtime exports for one installed Plugin release."""

    plugin_key: str
    plugin_version: str
    http_origins: object
    build_tool: object
    execute_tool: object
    build_datasource_command: object
    sync_datasource: object
    http_post_paths: object = None
    project_decision: object = None
    agent_extensions: object = None
    tool_metadata: object = None
    project_tool_result: object = None
    project_tool_evidence: object = None
    runtime_sha256: str = ""
    support_sha256: object = None
    manifest_sha256: str = ""

    @property
    def runtime_identity(self):
        """Return the source identity carried by execution events and tool metadata."""

        return {
            "runtime_sha256": self.runtime_sha256,
            "support_sha256": dict(self.support_sha256 or {}),
            "manifest_sha256": self.manifest_sha256,
        }


MANIFEST_FILENAME = ".runtime-manifest.json"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def load_runtime_contract(plugin_key, plugin_version, roots=None):
    """Load one exact Plugin release from controlled package roots."""

    if (
        not isinstance(plugin_key, str)
        or not PLUGIN_KEY_PATTERN.fullmatch(plugin_key)
        or not isinstance(plugin_version, str)
        or not PLUGIN_VERSION_PATTERN.fullmatch(plugin_version)
    ):
        raise PluginPackageLoadError("plugin runtime identity is invalid")
    for root_value in roots or _configured_roots():
        root = Path(root_value).resolve()
        manifest = _load_build_manifest(root)
        package = root / plugin_key
        if not package.exists():
            continue
        if package.is_symlink() or not package.is_dir():
            raise PluginPackageLoadError("plugin package is invalid")
        runtime_path = package / "runtime.py"
        if not runtime_path.is_file():
            legacy_package = package / plugin_version
            if legacy_package.is_dir() and not legacy_package.is_symlink():
                package = legacy_package
                runtime_path = package / "runtime.py"
        resolved_package = package.resolve(strict=True)
        if root != resolved_package and root not in resolved_package.parents:
            raise PluginPackageLoadError("plugin package is outside root")
        return _runtime_contract(
            runtime_path,
            plugin_key,
            plugin_version,
            root,
            manifest,
        )
    raise PluginPackageLoadError("installed plugin runtime is required")


def _configured_roots():
    """Return runtime roots, including the source-tree root for development."""

    source = _ACTIVE_SOURCE.get()
    if source is not None:
        return [source.root]
    configured = [
        value.strip()
        for value in os.getenv("LENS_PLUGIN_ROOTS", "").split(",")
        if value.strip()
    ]
    if configured:
        return configured
    source_root = Path(__file__).resolve().parents[2] / "plugins"
    return [Path("/opt/plugins"), source_root]


def _runtime_contract(path, plugin_key, plugin_version, root, manifest):
    """Validate a fixed runtime entrypoint and its structural interface."""

    if path.is_symlink():
        raise PluginPackageLoadError("plugin runtime entrypoint is invalid")
    try:
        resolved = path.resolve(strict=True)
        size = resolved.stat().st_size
        source = resolved.read_bytes()
        content_digest = hashlib.sha256(source).hexdigest()
    except OSError as exc:
        raise PluginPackageLoadError("plugin runtime entrypoint is required") from exc
    if not resolved.is_file() or size > MAX_RUNTIME_BYTES:
        raise PluginPackageLoadError("plugin runtime entrypoint is invalid")
    _verify_manifest_entry(root, resolved, manifest, source)
    support_sha256 = _support_hashes(root, manifest)
    source_context = _PluginSource(root, tuple(sorted(support_sha256.items())), manifest["digest"] if manifest else "")
    module = _load_runtime_module(
        str(resolved),
        plugin_key,
        plugin_version,
        content_digest,
        source,
        source_context,
    )
    if (
        getattr(module, "PLUGIN_API_VERSION", None) != 1
        or getattr(module, "PLUGIN_KEY", None) != plugin_key
        or getattr(module, "PLUGIN_VERSION", None) != plugin_version
    ):
        raise PluginPackageLoadError("plugin runtime identity is invalid")
    build_tool = getattr(module, "build_tool", None)
    execute_tool = getattr(module, "execute_tool", None)
    build_datasource_command = getattr(
        module,
        "build_datasource_command",
        None,
    )
    sync_datasource = getattr(module, "sync_datasource", None)
    if not callable(execute_tool):
        raise PluginPackageLoadError("plugin runtime contract is invalid")
    if build_tool is not None and not callable(build_tool):
        raise PluginPackageLoadError("plugin runtime contract is invalid")
    if build_datasource_command is not None and not callable(build_datasource_command):
        raise PluginPackageLoadError("plugin runtime contract is invalid")
    if sync_datasource is not None and not callable(sync_datasource):
        raise PluginPackageLoadError("plugin runtime contract is invalid")
    if (build_datasource_command is None) != (sync_datasource is None):
        raise PluginPackageLoadError("plugin datasource contract is invalid")
    http_origins = getattr(module, "http_origins", None)
    if http_origins is not None and not callable(http_origins):
        raise PluginPackageLoadError("plugin runtime contract is invalid")
    http_post_paths = getattr(module, "http_post_paths", None)
    if http_post_paths is not None and not callable(http_post_paths):
        raise PluginPackageLoadError("plugin runtime contract is invalid")
    project_decision = getattr(module, "project_decision", None)
    if project_decision is not None and not callable(project_decision):
        raise PluginPackageLoadError("plugin runtime contract is invalid")
    agent_extensions = getattr(module, "agent_extensions", None)
    project_tool_result = getattr(module, "project_tool_result", None)
    project_tool_evidence = getattr(module, "project_tool_evidence", None)
    for hook in (agent_extensions, project_tool_result, project_tool_evidence):
        if hook is not None and not callable(hook):
            raise PluginPackageLoadError("plugin agent contract is invalid")
    tool_metadata = getattr(module, "TOOL_METADATA", {})
    if (
        not isinstance(tool_metadata, dict)
        or len(tool_metadata) > 100
        or any(
            not isinstance(key, str) or not TOOL_KEY_PATTERN.fullmatch(key) or not isinstance(value, dict)
            for key, value in tool_metadata.items()
        )
    ):
        raise PluginPackageLoadError("plugin tool metadata is invalid")
    if any(
        "durable_evidence" in value and not isinstance(value["durable_evidence"], bool)
        for value in tool_metadata.values()
    ):
        raise PluginPackageLoadError("plugin tool metadata is invalid")
    if any(value.get("durable_evidence") is True for value in tool_metadata.values()) and project_tool_evidence is None:
        raise PluginPackageLoadError("plugin evidence projection is required")
    return PluginRuntimeContract(
        plugin_key=plugin_key,
        plugin_version=plugin_version,
        http_origins=_bind_export(http_origins, source_context),
        build_tool=_bind_export(build_tool, source_context),
        execute_tool=_bind_export(execute_tool, source_context),
        build_datasource_command=_bind_export(build_datasource_command, source_context),
        sync_datasource=_bind_export(sync_datasource, source_context),
        http_post_paths=_bind_export(http_post_paths, source_context),
        project_decision=_bind_export(project_decision, source_context),
        agent_extensions=_bind_export(agent_extensions, source_context),
        tool_metadata=tool_metadata,
        project_tool_result=_bind_export(project_tool_result, source_context),
        project_tool_evidence=_bind_export(project_tool_evidence, source_context),
        runtime_sha256=content_digest,
        support_sha256=support_sha256,
        manifest_sha256=manifest["digest"] if manifest else "",
    )


def _bind_export(callback, source):
    """Leave absent hooks unchanged and bind each callable to its own Plugin root."""

    return _SourceBoundExport(callback, source) if callback is not None else None


def _load_build_manifest(root):
    """Read the optional build manifest and reject malformed release metadata."""

    path = root / MANIFEST_FILENAME
    required = os.getenv("LENS_PLUGIN_MANIFEST_REQUIRED", "").lower() in {"1", "true", "yes"}
    if path.is_symlink():
        raise PluginPackageLoadError("plugin build manifest is invalid")
    if not path.exists():
        if required:
            raise PluginPackageLoadError("plugin build manifest is required")
        return None
    try:
        raw = path.read_bytes()
        payload = json.loads(raw)
    except (OSError, ValueError) as exc:
        raise PluginPackageLoadError("plugin build manifest is invalid") from exc
    files = payload.get("files") if isinstance(payload, dict) else None
    if not isinstance(payload, dict) or payload.get("schema_version") != 1 or not isinstance(files, dict) or not files:
        raise PluginPackageLoadError("plugin build manifest is invalid")
    for name, digest in files.items():
        if (
            not isinstance(name, str)
            or Path(name).is_absolute()
            or ".." in Path(name).parts
            or not isinstance(digest, str)
            or not SHA256_PATTERN.fullmatch(digest)
        ):
            raise PluginPackageLoadError("plugin build manifest is invalid")
    return {"files": files, "digest": hashlib.sha256(raw).hexdigest()}


def _verify_manifest_entry(root, path, manifest, source):
    """Ensure a loaded source file still matches the image build bytes."""

    if manifest is None:
        return
    try:
        relative = path.resolve(strict=True).relative_to(root).as_posix()
        expected = manifest["files"].get(relative)
        actual = hashlib.sha256(source).hexdigest()
    except (OSError, ValueError) as exc:
        raise PluginPackageLoadError("plugin build manifest verification failed") from exc
    if expected != actual:
        raise PluginPackageLoadError("plugin build manifest mismatch")


def _support_hashes(root, manifest):
    """Return hashes for shared support sources loaded by Plugin runtimes."""

    package = root / "_shared"
    if package.is_symlink():
        raise PluginPackageLoadError("plugin support entrypoint is invalid")
    names = {path.relative_to(root).as_posix() for path in package.glob("*.py")}
    if manifest:
        names.update(name for name in manifest["files"] if name.startswith("_shared/") and name.endswith(".py"))
    hashes = {}
    for name in sorted(names):
        path = root / name
        if path.is_symlink():
            raise PluginPackageLoadError("plugin support entrypoint is invalid")
        try:
            source = path.read_bytes()
        except OSError as exc:
            raise PluginPackageLoadError("plugin build manifest verification failed") from exc
        if len(source) > MAX_RUNTIME_BYTES:
            raise PluginPackageLoadError("plugin support entrypoint is invalid")
        _verify_manifest_entry(root, path, manifest, source)
        hashes[name] = hashlib.sha256(source).hexdigest()
    return hashes


@lru_cache(maxsize=128)
def _load_runtime_module(
    path_value,
    plugin_key,
    plugin_version,
    content_digest,
    source,
    source_context,
):
    """Execute a fixed regular file under an opaque module identity."""

    module_name = (
        f"_sourcelens_runtime_{plugin_key}_{plugin_version}_{content_digest}"
        .replace("-", "_")
        .replace(".", "_")
    )
    spec = importlib.util.spec_from_file_location(module_name, path_value)
    if spec is None or spec.loader is None:
        raise PluginPackageLoadError("plugin runtime entrypoint is invalid")
    module = importlib.util.module_from_spec(spec)
    token = _ACTIVE_SOURCE.set(source_context)
    try:
        code = compile(source, path_value, "exec")
        exec(code, module.__dict__)
    except Exception as exc:
        raise PluginPackageLoadError("plugin runtime failed to load") from exc
    finally:
        _ACTIVE_SOURCE.reset(token)
    return module


def load_plugin_support(name):
    """Load a shared regular module from the controlled Plugin roots."""

    if not isinstance(name, str) or not TOOL_KEY_PATTERN.fullmatch(name):
        raise PluginPackageLoadError("plugin support identity is invalid")
    candidates = []
    for value in _configured_roots():
        root = Path(value).resolve()
        manifest = _load_build_manifest(root)
        package = root / "_shared"
        path = package / f"{name}.py"
        if not package.exists():
            continue
        if package.is_symlink() or path.is_symlink():
            raise PluginPackageLoadError("plugin support entrypoint is invalid")
        try:
            resolved = path.resolve(strict=True)
            if not resolved.is_file() or resolved.stat().st_size > MAX_RUNTIME_BYTES:
                raise PluginPackageLoadError("plugin support entrypoint is invalid")
            content = resolved.read_bytes()
        except OSError as exc:
            raise PluginPackageLoadError("plugin support entrypoint is required") from exc
        if root not in resolved.parents or not resolved.is_file() or len(content) > MAX_RUNTIME_BYTES:
            raise PluginPackageLoadError("plugin support entrypoint is invalid")
        digest = hashlib.sha256(content).hexdigest()
        _verify_manifest_entry(root, resolved, manifest, content)
        active = _ACTIVE_SOURCE.get()
        if active is not None and (
            dict(active.support_sha256).get(f"_shared/{name}.py") != digest
            or active.manifest_sha256 != (manifest["digest"] if manifest else "")
        ):
            raise PluginPackageLoadError("plugin support source identity changed")
        candidates.append((root, resolved, content, digest, manifest))
    if candidates:
        digests = {item[3] for item in candidates}
        if len(digests) > 1:
            raise PluginPackageLoadError("plugin support roots are inconsistent")
        root, resolved, content, digest, manifest = candidates[0]
        source_context = _PluginSource(
            root, tuple(sorted(_support_hashes(root, manifest).items())), manifest["digest"] if manifest else ""
        )
        module = _load_runtime_module(str(resolved), f"support_{name}", "1.0.0", digest, content, source_context)
        module.__lens_source_sha256__ = digest
        module.__lens_manifest_sha256__ = manifest["digest"] if manifest else ""
        return module
    raise PluginPackageLoadError("installed plugin support is required")
