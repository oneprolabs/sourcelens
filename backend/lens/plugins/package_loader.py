"""Load trusted, versioned Plugin control contracts from fixed entrypoints."""

import hashlib
import importlib.util
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


class PluginPackageLoadError(RuntimeError):
    """Raised when installed Plugin code violates the host contract."""


@dataclass(frozen=True)
class PluginControlContract:
    """Control-plane implementations exported by one Plugin release."""

    datasource_provider: object
    tool_provider: object
    rpc_handler: object


def load_control_contract(plugin):
    """Return the validated control contract for one Plugin release."""

    path = plugin.path / "control.py"
    module = _load_control_module(
        str(path),
        plugin.key,
        plugin.version,
        _entrypoint_digest(path),
    )
    if (
        getattr(module, "PLUGIN_API_VERSION", None) != 1
        or getattr(module, "PLUGIN_KEY", None) != plugin.key
        or getattr(module, "PLUGIN_VERSION", None) != plugin.version
    ):
        raise PluginPackageLoadError("plugin control identity is invalid")
    datasource_provider = getattr(module, "DATASOURCE_PROVIDER", None)
    tool_provider = getattr(module, "TOOL_PROVIDER", None)
    rpc_handler = getattr(module, "execute_rpc", None)
    _validate_datasource_provider(datasource_provider)
    _validate_tool_provider(tool_provider)
    return PluginControlContract(
        datasource_provider=datasource_provider,
        tool_provider=tool_provider,
        rpc_handler=rpc_handler,
    )


@lru_cache(maxsize=128)
def _load_control_module(
    path_value,
    plugin_key,
    plugin_version,
    content_digest,
):
    """Execute one fixed regular file under an opaque module identity."""

    path = Path(path_value)
    resolved = path.resolve(strict=True)
    if path.is_symlink() or not resolved.is_file():
        raise PluginPackageLoadError("plugin control entrypoint is invalid")
    module_name = (
        f"_sourcelens_plugin_{plugin_key}_{plugin_version}_{content_digest}"
        .replace("-", "_")
        .replace(".", "_")
    )
    spec = importlib.util.spec_from_file_location(module_name, resolved)
    if spec is None or spec.loader is None:
        raise PluginPackageLoadError("plugin control entrypoint is invalid")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise PluginPackageLoadError("plugin control failed to load") from exc
    return module


def _entrypoint_digest(path):
    """Return a content identity for one fixed regular entrypoint."""

    try:
        resolved = path.resolve(strict=True)
        content = resolved.read_bytes()
    except OSError as exc:
        raise PluginPackageLoadError(
            "plugin control entrypoint is invalid"
        ) from exc
    if path.is_symlink() or not resolved.is_file():
        raise PluginPackageLoadError("plugin control entrypoint is invalid")
    return hashlib.sha256(content).hexdigest()[:16]


def _validate_datasource_provider(provider):
    """Validate the structural Datasource Provider V1 interface."""

    methods = (
        "discover_resources",
        "http_origins",
        "validate_connection",
        "validate_connection_scope",
        "validate_datasource_config",
        "validate_datasource_source_type",
        "validate_live_connection",
    )
    if provider is None or any(
        not callable(getattr(provider, method, None)) for method in methods
    ):
        raise PluginPackageLoadError(
            "plugin datasource provider contract is invalid"
        )


def _validate_tool_provider(provider):
    """Validate the structural Tool Provider V1 interface."""

    if provider is None or not callable(
        getattr(provider, "validate_request", None)
    ):
        raise PluginPackageLoadError(
            "plugin tool provider contract is invalid"
        )
