"""Load trusted, versioned Plugin runtimes from fixed entrypoints."""

import hashlib
import importlib.util
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


PLUGIN_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
PLUGIN_VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
MAX_RUNTIME_BYTES = 2 * 1024 * 1024


class PluginPackageLoadError(RuntimeError):
    """Raised when installed Plugin code violates the host contract."""


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
        resolved_package = package.resolve(strict=True)
        if root != resolved_package and root not in resolved_package.parents:
            raise PluginPackageLoadError("plugin package is outside root")
        return _runtime_contract(
            runtime_path,
            plugin_key,
            plugin_version,
        )
    raise PluginPackageLoadError("installed plugin runtime is required")


def _configured_roots():
    """Return runtime roots, including the source-tree root for development."""

    configured = [
        value.strip()
        for value in os.getenv("LENS_PLUGIN_ROOTS", "").split(",")
        if value.strip()
    ]
    if configured:
        return configured
    source_root = Path(__file__).resolve().parents[2] / "plugins"
    return [Path("/opt/plugins"), source_root]


def _runtime_contract(path, plugin_key, plugin_version):
    """Validate a fixed runtime entrypoint and its structural interface."""

    if path.is_symlink():
        raise PluginPackageLoadError("plugin runtime entrypoint is invalid")
    try:
        resolved = path.resolve(strict=True)
        size = resolved.stat().st_size
        content_digest = hashlib.sha256(
            resolved.read_bytes()
        ).hexdigest()[:16]
    except OSError as exc:
        raise PluginPackageLoadError(
            "plugin runtime entrypoint is required"
        ) from exc
    if not resolved.is_file() or size > MAX_RUNTIME_BYTES:
        raise PluginPackageLoadError("plugin runtime entrypoint is invalid")
    module = _load_runtime_module(
        str(resolved),
        plugin_key,
        plugin_version,
        content_digest,
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
    if not callable(build_tool) or not callable(execute_tool):
        raise PluginPackageLoadError("plugin runtime contract is invalid")
    if build_datasource_command is not None and not callable(
        build_datasource_command
    ):
        raise PluginPackageLoadError("plugin runtime contract is invalid")
    if sync_datasource is not None and not callable(sync_datasource):
        raise PluginPackageLoadError("plugin runtime contract is invalid")
    if (build_datasource_command is None) != (sync_datasource is None):
        raise PluginPackageLoadError("plugin datasource contract is invalid")
    http_origins = getattr(module, "http_origins", None)
    if http_origins is not None and not callable(http_origins):
        raise PluginPackageLoadError("plugin runtime contract is invalid")
    return PluginRuntimeContract(
        plugin_key=plugin_key,
        plugin_version=plugin_version,
        http_origins=http_origins,
        build_tool=build_tool,
        execute_tool=execute_tool,
        build_datasource_command=build_datasource_command,
        sync_datasource=sync_datasource,
    )


@lru_cache(maxsize=128)
def _load_runtime_module(
    path_value,
    plugin_key,
    plugin_version,
    content_digest,
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
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise PluginPackageLoadError("plugin runtime failed to load") from exc
    return module
