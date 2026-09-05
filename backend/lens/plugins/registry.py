"""Discover trusted built-in plugin manifests from controlled directories."""

import json
import re
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

PLUGIN_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
PLUGIN_VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
TOOL_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
RESOURCE_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
CONNECTION_WRITE_TARGET_PATTERN = re.compile(
    r"^(endpoint|secret_value|config\.[a-z][a-z0-9_-]{0,63}|"
    r"allowed_scope\.[a-z][a-z0-9_-]{0,63})$"
)
SUPPORTED_PROTOCOL_VERSION = 1
SUPPORTED_CAPABILITY_FAMILIES = frozenset({"plugin"})
ALLOWED_HANDLERS = frozenset(
    {
        "python_v1",
    }
)
READ_ONLY_TOOL_CAPABILITIES = frozenset(
    {"issue.read", "jira.issue.search", "repository.read"}
)
DATASOURCE_SOURCE_TYPES = frozenset({"feishu", "git", "jira"})
SCHEMA_TYPES = frozenset({"array", "boolean", "integer", "string"})
SCHEMA_FORMATS = frozenset(
    {
        "password",
        "provider-resource",
        "provider-resource-option",
        "repository-path",
        "uri",
    }
)
PLUGIN_ICON_SUFFIXES = frozenset({".png", ".svg", ".webp"})
PLUGIN_ICON_MAX_BYTES = 256 * 1024
GUIDANCE_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
GUIDANCE_MAX_TOPICS = 24
GUIDANCE_MAX_WHEN_TO_USE = 8
GUIDANCE_MAX_TOOLS_PER_TOPIC = 32


class PluginRegistryError(ValueError):
    """Raised when an installed plugin manifest is invalid or unsafe."""


class PluginNotFoundError(PluginRegistryError):
    """Raised when no installed Plugin matches one identity."""


@dataclass(frozen=True)
class InstalledPlugin:
    """One validated Plugin package available to the platform."""

    key: str
    version: str
    protocol_version: int
    capability_family: str
    display_name: str
    description: str
    icon: str
    datasource_source_type: str | None
    datasource: dict | None
    connection_schema: dict
    datasource_schema: dict | None
    control_handler: str
    runtime_handler: str
    datasource_handler: str | None
    tools: tuple
    assistant_guidance: dict
    path: Path


@dataclass(frozen=True)
class InstalledPluginTool:
    """One validated read-only tool declared by an installed Plugin."""

    key: str
    description: str
    capability: str
    capability_family: str
    side_effect: str
    input_schema: dict


def discover_plugins():
    """Return validated Plugin packages from controlled roots."""

    plugins = []
    identities = set()
    roots = getattr(settings, "LENS_PLUGIN_ROOTS", ["/opt/sourcelens/plugins"])
    visited_roots = set()
    for root_value in roots:
        root = Path(root_value).resolve()
        if root in visited_roots:
            continue
        visited_roots.add(root)
        if not root.exists():
            continue
        if not root.is_dir():
            raise PluginRegistryError("plugin root must be a directory")
        for key_dir in sorted(root.iterdir()):
            if not key_dir.is_dir() or key_dir.is_symlink():
                continue
            if key_dir.name == "__pycache__":
                continue
            package_dirs = [key_dir]
            if not (key_dir / "plugin.json").is_file():
                package_dirs.extend(
                    path
                    for path in sorted(key_dir.iterdir())
                    if path.is_dir()
                    and not path.is_symlink()
                    and path.name != "__pycache__"
                )
            for plugin_dir in package_dirs:
                plugin = _load_plugin(root, plugin_dir, key_dir.name)
                identity = (plugin.key, plugin.version)
                if identity in identities:
                    raise PluginRegistryError(
                        "duplicate plugin key and version"
                    )
                identities.add(identity)
                plugins.append(plugin)
    return plugins


def latest_plugin(plugin_key):
    """Return the latest installed package for one Plugin key."""

    matches = [
        plugin for plugin in discover_plugins() if plugin.key == plugin_key
    ]
    if not matches:
        raise PluginNotFoundError("installed plugin is required")
    return max(
        matches,
        key=lambda plugin: tuple(
            int(part) for part in plugin.version.split(".")
        ),
    )


def installed_plugin(plugin_key, version=None):
    """Return the latest installed package or one exact version."""

    matches = [
        plugin
        for plugin in discover_plugins()
        if plugin.key == plugin_key
    ]
    if version is None:
        if matches:
            return max(matches, key=lambda item: _semver(item.version))
    else:
        for plugin in matches:
            if plugin.version == version:
                return plugin
    raise PluginNotFoundError("installed plugin version is required")


def _semver(value):
    """Return the numeric ordering key for a validated semantic version."""

    return tuple(int(part) for part in value.split("."))


def _load_plugin(root, plugin_dir, expected_key=None):
    """Load one manifest after validating its controlled directory identity."""

    manifest_path = plugin_dir / "plugin.json"
    resolved_path = manifest_path.resolve()
    if root not in resolved_path.parents:
        raise PluginRegistryError("plugin manifest is outside configured root")
    if not resolved_path.is_file():
        raise PluginRegistryError("plugin manifest is required")
    try:
        manifest = json.loads(resolved_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PluginRegistryError(
            "plugin manifest must be valid JSON"
        ) from exc
    if not isinstance(manifest, dict):
        raise PluginRegistryError("plugin manifest must be an object")

    key = manifest.get("key")
    version = manifest.get("version")
    protocol_version = manifest.get("protocol_version")
    handlers = manifest.get("handlers")
    if not isinstance(key, str) or not PLUGIN_KEY_PATTERN.fullmatch(key):
        raise PluginRegistryError("plugin key is invalid")
    if not isinstance(version, str) or not PLUGIN_VERSION_PATTERN.fullmatch(
        version
    ):
        raise PluginRegistryError("plugin version is invalid")
    if key != (expected_key or plugin_dir.name):
        raise PluginRegistryError(
            "plugin manifest does not match directory identity"
        )
    if expected_key and plugin_dir.parent != root:
        if version != plugin_dir.name:
            raise PluginRegistryError(
                "plugin manifest does not match version directory"
            )
    if protocol_version != SUPPORTED_PROTOCOL_VERSION:
        raise PluginRegistryError("plugin protocol version is unsupported")
    if not isinstance(handlers, dict):
        raise PluginRegistryError("plugin handlers are required")
    control_handler = handlers.get("control")
    runtime_handler = handlers.get("runtime")
    datasource_handler = handlers.get("datasource")
    has_datasource = any(
        (
            datasource_handler is not None,
            manifest.get("datasource_source_type") is not None,
            manifest.get("datasource") is not None,
            manifest.get("datasource_schema") is not None,
        )
    )
    if has_datasource and control_handler == "python_v1":
        datasource_handler = "python_v1"
    elif control_handler is None:
        control_handler = datasource_handler
    if runtime_handler not in ALLOWED_HANDLERS:
        raise PluginRegistryError("plugin runtime handler is not allowed")
    if has_datasource and datasource_handler not in ALLOWED_HANDLERS:
        raise PluginRegistryError("plugin datasource handler is not allowed")
    if control_handler not in ALLOWED_HANDLERS:
        raise PluginRegistryError("plugin control handler is not allowed")
    if control_handler == "python_v1" or runtime_handler == "python_v1":
        _validate_python_entrypoint(plugin_dir, "control")
        _validate_python_entrypoint(plugin_dir, "runtime")
    capability_family = manifest.get("capability_family", "plugin")
    if capability_family not in SUPPORTED_CAPABILITY_FAMILIES:
        raise PluginRegistryError(
            "plugin capability family is not allowed"
        )
    tools = _validate_tools(
        manifest.get("tools") or [],
        capability_family=capability_family,
    )
    assistant_guidance = _validate_assistant_guidance(
        manifest.get("assistant_guidance"),
        tools,
    )
    display_name = _bounded_manifest_text(
        manifest.get("display_name") or key,
        "plugin display name",
        160,
    )
    description = _bounded_manifest_text(
        manifest.get("description") or "",
        "plugin description",
        1000,
        required=False,
    )
    icon = _validate_plugin_icon(plugin_dir, manifest.get("icon"))
    connection_schema = _validate_form_schema(
        manifest.get("connection_schema"),
        "connection",
    )
    datasource_source_type = None
    datasource_schema = None
    datasource = None
    if has_datasource:
        datasource_source_type = manifest.get(
            "datasource_source_type",
            "git",
        )
        if datasource_source_type not in DATASOURCE_SOURCE_TYPES:
            raise PluginRegistryError(
                "plugin datasource source type is invalid"
            )
        datasource_schema = _validate_form_schema(
            manifest.get("datasource_schema"),
            "datasource",
        )
        datasource = _validate_datasource_definition(
            manifest.get("datasource"),
            datasource_source_type,
            datasource_schema,
        )
    return InstalledPlugin(
        key=key,
        version=version,
        protocol_version=protocol_version,
        capability_family=capability_family,
        display_name=display_name,
        description=description,
        icon=icon,
        datasource_source_type=datasource_source_type,
        datasource=datasource,
        connection_schema=connection_schema,
        datasource_schema=datasource_schema,
        control_handler=control_handler,
        runtime_handler=runtime_handler,
        datasource_handler=datasource_handler,
        tools=tools,
        assistant_guidance=assistant_guidance,
        path=plugin_dir.resolve(),
    )


def _validate_assistant_guidance(value, tools):
    """Return bounded, advisory guidance for model Plugin discovery."""

    if value is None:
        return {"summary": "", "when_to_use": [], "topics": []}
    if not isinstance(value, dict):
        raise PluginRegistryError("plugin assistant guidance is invalid")
    summary = _bounded_manifest_text(
        value.get("summary") or "",
        "plugin assistant guidance summary",
        600,
        required=False,
    )
    when_to_use = value.get("when_to_use") or []
    if (
        not isinstance(when_to_use, list)
        or len(when_to_use) > GUIDANCE_MAX_WHEN_TO_USE
    ):
        raise PluginRegistryError(
            "plugin assistant guidance triggers are invalid"
        )
    normalized_when_to_use = [
        _bounded_manifest_text(
            item,
            "plugin assistant guidance trigger",
            240,
        )
        for item in when_to_use
    ]
    tool_keys = {tool.key for tool in tools}
    topics = value.get("topics") or []
    if not isinstance(topics, list) or len(topics) > GUIDANCE_MAX_TOPICS:
        raise PluginRegistryError(
            "plugin assistant guidance topics are invalid"
        )
    normalized_topics = []
    seen_topics = set()
    for topic in topics:
        if not isinstance(topic, dict):
            raise PluginRegistryError(
                "plugin assistant guidance topic is invalid"
            )
        key = topic.get("key")
        if (
            not isinstance(key, str)
            or not GUIDANCE_KEY_PATTERN.fullmatch(key)
            or key in seen_topics
        ):
            raise PluginRegistryError(
                "plugin assistant guidance topic key is invalid"
            )
        topic_tools = topic.get("tool_keys") or []
        if (
            not isinstance(topic_tools, list)
            or not topic_tools
            or len(topic_tools) > GUIDANCE_MAX_TOOLS_PER_TOPIC
            or len(
                {
                    tool_key
                    for tool_key in topic_tools
                    if isinstance(tool_key, str)
                }
            )
            != len(topic_tools)
            or any(
                not isinstance(tool_key, str)
                or tool_key not in tool_keys
                for tool_key in topic_tools
            )
        ):
            raise PluginRegistryError(
                "plugin assistant guidance topic tools are invalid"
            )
        details = _bounded_manifest_text(
            topic.get("details") or "",
            "plugin assistant guidance details",
            6000,
            required=False,
        )
        topic_summary = _bounded_manifest_text(
            topic.get("summary") or "",
            "plugin assistant guidance topic summary",
            600,
        )
        normalized_topics.append(
            {
                "key": key,
                "summary": topic_summary,
                "details": details,
                "tool_keys": list(topic_tools),
            }
        )
        seen_topics.add(key)
    return {
        "summary": summary,
        "when_to_use": normalized_when_to_use,
        "topics": normalized_topics,
    }


def _validate_plugin_icon(plugin_dir, value):
    """Return one package-relative icon path after boundary validation."""

    if value in (None, ""):
        return ""
    if not isinstance(value, str) or len(value) > 240:
        raise PluginRegistryError("plugin icon is invalid")
    package_root = plugin_dir.resolve()
    icon_path = (plugin_dir / value).resolve()
    if package_root not in icon_path.parents:
        raise PluginRegistryError("plugin icon is outside its package")
    if icon_path.is_symlink() or not icon_path.is_file():
        raise PluginRegistryError("plugin icon file is required")
    if icon_path.suffix.lower() not in PLUGIN_ICON_SUFFIXES:
        raise PluginRegistryError("plugin icon type is unsupported")
    if icon_path.stat().st_size > PLUGIN_ICON_MAX_BYTES:
        raise PluginRegistryError("plugin icon is too large")
    return icon_path.relative_to(package_root).as_posix()


def _validate_datasource_definition(value, source_type, schema):
    """Return one safe datasource definition with legacy manifest fallback."""

    if value is None:
        return {
            "key": "default",
            "display_name": "Datasource",
            "description": "",
            "source_type": source_type,
            "config_schema": schema,
            "resources": [],
            "runtime": {
                "supports_incremental": False,
                "supports_cancel": True,
                "output": "workspace",
            },
        }
    if not isinstance(value, dict):
        raise PluginRegistryError("plugin datasource definition is invalid")
    key = value.get("key")
    if not isinstance(key, str) or not PLUGIN_KEY_PATTERN.fullmatch(key):
        raise PluginRegistryError("plugin datasource key is invalid")
    if value.get("source_type") != source_type:
        raise PluginRegistryError("plugin datasource source type is invalid")
    resources = value.get("resources") or []
    if not isinstance(resources, list) or len(resources) > 20:
        raise PluginRegistryError("plugin datasource resources are invalid")
    normalized_resources = []
    for resource in resources:
        if not isinstance(resource, dict):
            raise PluginRegistryError("plugin datasource resource is invalid")
        resource_key = resource.get("key")
        if (
            not isinstance(resource_key, str)
            or not RESOURCE_KEY_PATTERN.fullmatch(resource_key)
        ):
            raise PluginRegistryError("plugin datasource resource is invalid")
        normalized_resources.append({
            "key": resource_key,
            "display_name": _bounded_manifest_text(
                resource.get("display_name") or resource_key,
                "plugin datasource resource display name",
                160,
            ),
            "depends_on": resource.get("depends_on") or "",
        })
    resource_keys = {item["key"] for item in normalized_resources}
    for item in normalized_resources:
        dependency = item["depends_on"]
        if dependency and dependency not in resource_keys:
            raise PluginRegistryError(
                "plugin datasource resource dependency is invalid"
            )
    for field in schema.get("properties", {}).values():
        resource_key = field.get("resource")
        if resource_key is None:
            continue
        if resource_key not in resource_keys:
            raise PluginRegistryError(
                "plugin datasource schema resource is not declared"
            )
        if field.get("format") != "provider-resource-option":
            continue
        dependency_field = schema["properties"].get(field.get("depends_on"))
        resource = next(
            item for item in normalized_resources
            if item["key"] == resource_key
        )
        if (
            not isinstance(dependency_field, dict)
            or dependency_field.get("resource") != resource.get("depends_on")
        ):
            raise PluginRegistryError(
                "plugin datasource schema dependency is invalid"
            )
    runtime = value.get("runtime") or {}
    if (
        not isinstance(runtime, dict)
        or runtime.get("output") not in {"workspace", "documents"}
    ):
        raise PluginRegistryError("plugin datasource runtime is invalid")
    return {
        "key": key,
        "display_name": _bounded_manifest_text(
            value.get("display_name") or key,
            "plugin datasource display name",
            160,
        ),
        "description": _bounded_manifest_text(
            value.get("description") or "",
            "plugin datasource description",
            1000,
            required=False,
        ),
        "source_type": source_type,
        "config_schema": schema,
        "resources": normalized_resources,
        "runtime": {
            "supports_incremental": bool(runtime.get("supports_incremental")),
            "supports_cancel": bool(runtime.get("supports_cancel")),
            "output": runtime["output"],
        },
    }


def _validate_tools(value, capability_family="plugin"):
    """Return trusted read-only tool declarations from one manifest."""

    if not isinstance(value, list):
        raise PluginRegistryError("plugin tools must be a list")
    tools = []
    identities = set()
    for item in value:
        if not isinstance(item, dict):
            raise PluginRegistryError("plugin tool must be an object")
        key = item.get("key")
        description = item.get("description")
        capability = item.get("capability")
        tool_capability_family = item.get(
            "capability_family",
            capability_family,
        )
        side_effect = item.get("side_effect")
        if (
            not isinstance(key, str)
            or not TOOL_KEY_PATTERN.fullmatch(key)
            or key in identities
        ):
            raise PluginRegistryError("plugin tool is not allowed")
        if (
            not isinstance(description, str)
            or not description.strip()
            or len(description) > 1000
        ):
            raise PluginRegistryError("plugin tool description is invalid")
        if capability not in READ_ONLY_TOOL_CAPABILITIES:
            raise PluginRegistryError("plugin tool capability is not allowed")
        if tool_capability_family not in SUPPORTED_CAPABILITY_FAMILIES:
            raise PluginRegistryError(
                "plugin tool capability family is not allowed"
            )
        if side_effect != "none":
            raise PluginRegistryError("plugin tool side effect is not allowed")
        schema = _validate_tool_schema(
            key,
            item.get("input_schema"),
        )
        identities.add(key)
        tools.append(
            InstalledPluginTool(
                key=key,
                description=description.strip(),
                capability=capability,
                capability_family=tool_capability_family,
                side_effect=side_effect,
                input_schema=schema,
            )
        )
    return tuple(tools)


def _validate_tool_schema(tool_key, value):
    """Validate the bounded V1 JSON schema subset for model tool inputs."""

    if not isinstance(value, dict) or value.get("type") != "object":
        raise PluginRegistryError("plugin tool input schema is invalid")
    properties = value.get("properties") or {}
    required = value.get("required") or []
    if not isinstance(properties, dict) or not isinstance(required, list):
        raise PluginRegistryError("plugin tool input schema is invalid")
    if (
        len(properties) > 20
        or len(set(required)) != len(required)
        or set(required).difference(properties)
    ):
        raise PluginRegistryError("plugin tool input schema is not allowed")
    normalized = {}
    for name, field in properties.items():
        if (
            not isinstance(name, str)
            or not PLUGIN_KEY_PATTERN.fullmatch(name)
            or not isinstance(field, dict)
            or field.get("type")
            not in {"array", "boolean", "integer", "string"}
        ):
            raise PluginRegistryError("plugin tool field schema is invalid")
        safe_field = {"type": field["type"]}
        description = field.get("description")
        if description:
            safe_field["description"] = _bounded_manifest_text(
                description,
                "plugin tool field description",
                500,
            )
        for limit_name in ("minLength", "maxLength", "minimum", "maximum"):
            limit = field.get(limit_name)
            if isinstance(limit, int) and not isinstance(limit, bool):
                safe_field[limit_name] = limit
        if field["type"] == "array":
            items = field.get("items")
            if not isinstance(items, dict) or items.get("type") != "string":
                raise PluginRegistryError(
                    "plugin tool array item schema is invalid"
                )
            safe_field["items"] = {"type": "string"}
            for limit_name in ("minItems", "maxItems"):
                limit = field.get(limit_name)
                if isinstance(limit, int) and not isinstance(limit, bool):
                    safe_field[limit_name] = limit
            if field.get("uniqueItems") is True:
                safe_field["uniqueItems"] = True
        normalized[name] = safe_field
    return {
        "type": "object",
        "properties": normalized,
        "required": list(required),
        "additionalProperties": False,
    }


def _validate_python_entrypoint(plugin_dir, name):
    """Require a bounded regular Python file at one fixed package path."""

    path = plugin_dir / f"{name}.py"
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise PluginRegistryError(
            f"plugin {name} entrypoint is required"
        ) from exc
    if (
        path.is_symlink()
        or not resolved.is_file()
        or plugin_dir.resolve() not in resolved.parents
        or resolved.stat().st_size > 1_000_000
    ):
        raise PluginRegistryError(f"plugin {name} entrypoint is invalid")


def _validate_form_schema(value, label):
    """Return a bounded JSON Schema subset for administrator forms."""

    if value is None:
        return {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        }
    if not isinstance(value, dict) or value.get("type") != "object":
        raise PluginRegistryError(f"plugin {label} schema is invalid")
    properties = value.get("properties") or {}
    required = value.get("required") or []
    if (
        not isinstance(properties, dict)
        or len(properties) > 20
        or not isinstance(required, list)
        or len(set(required)) != len(required)
        or set(required).difference(properties)
    ):
        raise PluginRegistryError(f"plugin {label} schema is invalid")
    normalized = {}
    for key, field in properties.items():
        if (
            not isinstance(key, str)
            or not PLUGIN_KEY_PATTERN.fullmatch(key)
            or not isinstance(field, dict)
            or field.get("type") not in SCHEMA_TYPES
        ):
            raise PluginRegistryError(f"plugin {label} field is invalid")
        field_format = field.get("format")
        if field_format is not None and field_format not in SCHEMA_FORMATS:
            raise PluginRegistryError(
                f"plugin {label} field format is invalid"
            )
        safe_field = {
            "type": field["type"],
            "title": _bounded_manifest_text(
                field.get("title") or key,
                f"plugin {label} field title",
                160,
            ),
        }
        description = field.get("description")
        if description:
            safe_field["description"] = _bounded_manifest_text(
                description,
                f"plugin {label} field description",
                500,
            )
        if field_format is not None:
            safe_field["format"] = field_format
        resource = field.get("resource")
        if resource is not None:
            if (
                not isinstance(resource, str)
                or not RESOURCE_KEY_PATTERN.fullmatch(resource)
            ):
                raise PluginRegistryError(
                    f"plugin {label} field resource is invalid"
                )
            safe_field["resource"] = resource
        depends_on = field.get("depends_on")
        if depends_on is not None:
            if (
                not isinstance(depends_on, str)
                or not PLUGIN_KEY_PATTERN.fullmatch(depends_on)
            ):
                raise PluginRegistryError(
                    f"plugin {label} field dependency is invalid"
                )
            safe_field["depends_on"] = depends_on
        if field_format == "provider-resource":
            if resource is None or depends_on is not None:
                raise PluginRegistryError(
                    f"plugin {label} field resource is invalid"
                )
        elif field_format == "provider-resource-option":
            if resource is None or depends_on is None:
                raise PluginRegistryError(
                    f"plugin {label} field dependency is invalid"
                )
        elif resource is not None or depends_on is not None:
            raise PluginRegistryError(
                f"plugin {label} field resource is invalid"
            )
        if "default" in field and isinstance(
            field["default"],
            (str, int, bool),
        ):
            safe_field["default"] = field["default"]
        if field["type"] == "array":
            items = field.get("items")
            if not isinstance(items, dict) or items.get("type") != "string":
                raise PluginRegistryError(
                    f"plugin {label} array field is invalid"
                )
            safe_field["items"] = {"type": "string"}
            item_format = items.get("format")
            if item_format is not None:
                if item_format != "uri":
                    raise PluginRegistryError(
                        f"plugin {label} array item format is invalid"
                    )
                safe_field["items"]["format"] = item_format
            for limit_name in ("minItems", "maxItems"):
                limit = field.get(limit_name)
                if isinstance(limit, int) and not isinstance(limit, bool):
                    safe_field[limit_name] = limit
            minimum = safe_field.get("minItems", 0)
            maximum = safe_field.get("maxItems")
            if minimum < 0 or (
                maximum is not None and maximum < minimum
            ):
                raise PluginRegistryError(
                    f"plugin {label} array limits are invalid"
                )
        write_to = field.get("write_to")
        if write_to is not None:
            if (
                label != "connection"
                or not isinstance(write_to, str)
                or not CONNECTION_WRITE_TARGET_PATTERN.fullmatch(write_to)
            ):
                raise PluginRegistryError(
                    f"plugin {label} field write target is invalid"
                )
            safe_field["write_to"] = write_to
        normalized[key] = safe_field
    for field in normalized.values():
        depends_on = field.get("depends_on")
        if depends_on is None:
            continue
        dependency = normalized.get(depends_on)
        if (
            dependency is None
            or dependency.get("format") != "provider-resource"
        ):
            raise PluginRegistryError(
                f"plugin {label} field dependency is invalid"
            )
    return {
        "type": "object",
        "properties": normalized,
        "required": list(required),
        "additionalProperties": False,
    }


def _bounded_manifest_text(value, label, limit, required=True):
    """Return one bounded display string from an installed manifest."""

    if not isinstance(value, str):
        raise PluginRegistryError(f"{label} is invalid")
    text = value.strip()
    if (required and not text) or len(text) > limit:
        raise PluginRegistryError(f"{label} is invalid")
    return text
