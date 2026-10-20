"""Source tools supply evidence without selecting a report workflow."""

from types import SimpleNamespace

import pytest

from lensnode.plugin_agent import prepare_agent_extensions
from lensnode.plugin_package_loader import load_runtime_contract


@pytest.mark.parametrize("provider", ["github", "gitlab", "jira"])
def test_source_plugin_does_not_register_report_workflow(provider):
    contract = load_runtime_contract(provider, "1.0.0")

    assert contract.agent_extensions is None


@pytest.mark.parametrize("question", ["生成昨天的日报", "总结昨天的工作，仅用文字", "查看活动记录"])
def test_source_evidence_preserves_route_tools_and_subagent_policy(tmp_path, question):
    command = {
        "run_uuid": "source-boundary",
        "task": "general_chat",
        "question": question,
        "loaded_plugins": [
            {
                "plugin_key": "jira",
                "plugin_version": "1.0.0",
                "connection_uuid": "connection",
                "tools": [{"key": "jira_activity_summary"}, {"key": "jira_get_issue"}],
            }
        ],
    }
    source = SimpleNamespace(name="jira_activity_summary")
    detail = SimpleNamespace(name="jira_get_issue")
    extensions = prepare_agent_extensions(command, tmp_path, skills=["Write /deliverables/report.html."])
    route = {"route": "direct_execute", "evidence_requirement": "tool_result", "required_capabilities": ["plugin"]}
    policy = extensions.apply_policy(route, [source, detail, *extensions.tools])

    assert policy.route == route
    assert policy.subagents_allowed is True
    assert policy.required_sources == ()
    assert extensions.filter_tools([source, detail]) == [source, detail]
    assert extensions.preferred_route is None
    assert extensions.evidence() == {}
    assert extensions.completion_checks("Waiting for a source query") == []
