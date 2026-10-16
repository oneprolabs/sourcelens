"""Activity reports discover capabilities from their bound Plugin runtimes."""

import json
from types import SimpleNamespace

import pytest

from lensnode.plugin_package_loader import load_plugin_support

_report_support = load_plugin_support("activity_reports")
ActivityLedger = _report_support.ActivityLedger
_high_confidence_report_route = _report_support._high_confidence_report_route
_report_scoped_tools = _report_support._report_scoped_tools
_activity_report_batch_tools = _report_support._activity_report_batch_tools
_report_execution_guidance = _report_support._report_execution_guidance
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
TOOL_METADATA = {"summarize_events": {"activity_report": True}, "summarize_changes": {"activity_report": True}}

def build_tool(definition, executor):
    def invoke(runtime: ToolRuntime, **arguments) -> str:
        return executor(definition["key"], arguments, runtime)
    return tool(
        definition["key"], description=definition["description"], args_schema=definition["input_schema"]
    )(invoke)

def execute_tool(*args):
    return {"ok": True}
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("LENS_PLUGIN_ROOTS", str(tmp_path))
    return {
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


def test_bound_custom_activity_tools_enable_report_route_and_filter_only_their_source(command):
    """A new source joins report execution without editing core tool-name lists."""

    assert _activity_report_batch_tools(command) == {"summarize_events", "summarize_changes"}
    assert _high_confidence_report_route(command)["route"] == "plan_execute"
    metadata = {"plugin_key": "custom_source"}
    tools = [
        SimpleNamespace(name=key, metadata=metadata) for key in ("summarize_events", "summarize_changes", "get_details")
    ]
    unrelated = SimpleNamespace(name="get_details_elsewhere", metadata={"plugin_key": "other"})

    assert _report_scoped_tools(command, [*tools, unrelated]) == [*tools[:2], unrelated]


def test_declared_but_unbound_activity_tools_do_not_enable_report_execution(command):
    """Runtime declarations never grant a capability absent from the frozen binding."""

    command["loaded_plugins"][0]["tools"] = command["loaded_plugins"][0]["tools"][2:]

    assert _activity_report_batch_tools(command) == set()
    assert _high_confidence_report_route(command) is None


def test_internal_activity_tools_do_not_enable_model_report_execution(command):
    """Internal capabilities cannot become model tools through a report role."""

    for definition in command["loaded_plugins"][0]["tools"]:
        definition["exposure"] = "internal"

    assert _activity_report_batch_tools(command) == set()
    assert build_plugin_tools(command, SimpleNamespace(), SimpleNamespace()) == []


def test_registered_tools_keep_plugin_instructions_and_activity_metadata(command):
    """The Agent receives the Plugin description together with its execution role."""

    tools = build_plugin_tools(command, SimpleNamespace(), SimpleNamespace())

    assert tools[0].description == "Plugin-owned instructions for summarize_events."
    assert tools[0].metadata["plugin_metadata"]["activity_report"] is True
    assert tools[2].metadata["plugin_metadata"].get("activity_report", False) is False
    prompt = _report_execution_guidance(command)
    assert "summarize_events" in prompt
    assert "owner/*" not in prompt
    assert "TOOL_ARGUMENTS_INVALID" in prompt


def test_activity_ledger_preserves_a_registered_custom_source(tmp_path):
    """Evidence storage accepts a source selected by registration, regardless of its name."""

    ledger = ActivityLedger(tmp_path)
    result = {
        "ok": True,
        "since": "2026-10-12T00:00:00Z",
        "until": "2026-10-13T00:00:00Z",
        "items": [{"number": 1, "author": "dev", "title": "Resolved request", "updated_at": "2026-10-12T12:00:00Z"}],
    }
    ledger.record("custom_source", "connection", "1.0.0", "summarize_events", {}, result)

    evidence = ledger.report({"summarize_events"})
    assert evidence["complete"] is True
    assert evidence["source_tools"] == ["summarize_events"]
    assert evidence["records"][0]["provider"] == "custom_source"
    assert evidence["records"][0]["title"] == "Resolved request"
    assert json.loads(ledger.cached("custom_source", "connection", "1.0.0", "summarize_events", {})) == result


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
