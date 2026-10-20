"""Activity reports discover capabilities from their bound Plugin runtimes."""

from types import SimpleNamespace

import pytest

from lensnode.plugin_package_loader import PluginPackageLoadError, load_runtime_contract
from lensnode.plugin_tools import build_plugin_tools


@pytest.fixture
def command(tmp_path, monkeypatch):
    """Install a source whose tool names do not encode the Plugin identity."""

    package = tmp_path / "custom_source"
    package.mkdir()
    (package / "runtime.py").write_text(
        """
from langchain.tools import ToolRuntime, tool

PLUGIN_API_VERSION = 1
PLUGIN_KEY = "custom_source"
PLUGIN_VERSION = "1.0.0"
TOOL_METADATA = {"summarize_events": {"durable_evidence": True}, "summarize_changes": {"durable_evidence": True}}

def build_tool(definition, executor):
    def invoke(runtime: ToolRuntime, **arguments) -> str:
        return executor(definition["key"], arguments, runtime)
    return tool(
        definition["key"], description=definition["description"], args_schema=definition["input_schema"]
    )(invoke)

def execute_tool(*args):
    return {"ok": True}

def project_tool_evidence(tool_key, result, connection_uuid):
    return {"complete": True, "records": [{"fact": "Custom source fact"}]}
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("LENS_PLUGIN_ROOTS", str(tmp_path))
    return {
        "_test_workspace": tmp_path,
        "question": "生成昨天的工作报告",
        "runtime_route": "plan_execute",
        "loaded_plugins": [
            {
                "plugin_key": "custom_source",
                "plugin_version": "1.0.0",
                "protocol_version": 1,
                "connection_uuid": "connection",
                "tools": [
                    {
                        "key": key,
                        "description": f"Plugin-owned instructions for {key}.",
                        "capability": "repository.read",
                        "side_effect": "none",
                        "input_schema": {"type": "object", "properties": {}, "required": []},
                    }
                    for key in ("summarize_events", "summarize_changes", "get_details")
                ],
            }
        ],
    }


def test_declared_but_unbound_activity_tools_do_not_enable_report_execution(command):
    """Runtime declarations never grant a capability absent from the frozen binding."""

    command["loaded_plugins"][0]["tools"] = command["loaded_plugins"][0]["tools"][2:]

    from lensnode.plugin_agent import prepare_agent_extensions

    assert not prepare_agent_extensions(command, command["_test_workspace"]).extensions


def test_internal_activity_tools_do_not_enable_model_report_execution(command):
    """Internal capabilities cannot become model tools through a report role."""

    for definition in command["loaded_plugins"][0]["tools"]:
        definition["exposure"] = "internal"

    assert build_plugin_tools(command, SimpleNamespace(), SimpleNamespace()) == []


def test_registered_tools_keep_plugin_instructions_and_activity_metadata(command):
    """The Agent receives the Plugin description together with its execution role."""

    tools = build_plugin_tools(command, SimpleNamespace(), SimpleNamespace())

    assert tools[0].description == "Plugin-owned instructions for summarize_events."
    assert tools[0].metadata["plugin_metadata"]["durable_evidence"] is True
    assert tools[2].metadata["plugin_metadata"].get("durable_evidence", False) is False


@pytest.mark.parametrize("declaration", ['"summarize_events"', '{"summarize_events": True}', '["bad name"]'])
def test_runtime_rejects_invalid_tool_metadata_declarations(tmp_path, declaration):
    """Optional role declarations remain bounded and structurally validated."""

    package = tmp_path / "example"
    package.mkdir()
    (package / "runtime.py").write_text(
        f'PLUGIN_API_VERSION = 1\nPLUGIN_KEY = "example"\nPLUGIN_VERSION = "1.0.0"\n'
        f'TOOL_METADATA = {declaration}\ndef execute_tool(*args):\n    return {{"ok": True}}\n',
        encoding="utf-8",
    )

    with pytest.raises(PluginPackageLoadError, match="metadata"):
        load_runtime_contract("example", "1.0.0", roots=[tmp_path])


@pytest.mark.parametrize(
    "metadata,hook",
    [
        ({"read": {"durable_evidence": "yes"}}, ""),
        ({"read": {"durable_evidence": True}}, ""),
    ],
)
def test_durable_evidence_requires_boolean_metadata_and_a_projector(tmp_path, metadata, hook):
    package = tmp_path / "example"
    package.mkdir()
    (package / "runtime.py").write_text(
        'PLUGIN_API_VERSION = 1\nPLUGIN_KEY = "example"\nPLUGIN_VERSION = "1.0.0"\n'
        f'TOOL_METADATA = {metadata!r}\ndef execute_tool(*args):\n    return {{"ok": True}}\n{hook}'
    )
    with pytest.raises(PluginPackageLoadError):
        load_runtime_contract("example", "1.0.0", roots=[tmp_path])
