"""Installed Plugins own report recognition and routing constraints."""

from types import SimpleNamespace

from lensnode import agent_runtime
from lensnode.plugin_agent import prepare_agent_extensions
from lensnode.plugin_package_loader import load_plugin_support

_report_support = load_plugin_support("activity_reports")


def _report_prompt(command, skills):
    """Inject Plugin instructions through the host's generic prompt interface."""

    from lensnode.agent_runtime.system_prompts import _system_prompt

    return _system_prompt(
        {},
        {**command, "task": "general_chat"},
        skills,
        runtime_guidance=(_report_support._report_execution_guidance(command),),
    )


def test_general_chat_prompt_prefers_batch_activity_tool_for_reports():
    prompt = _report_prompt(
        {
            "question": "获取昨天的工作报告",
            "runtime_route": "plan_execute",
            "loaded_plugins": [
                {"plugin_key": "github", "plugin_version": "1.0.0", "tools": [{"key": "github_activity_summary"}]}
            ],
        },
        ["Produce an engineering activity report."],
    )

    assert "github_activity_summary" in prompt
    assert "each requested period" in prompt
    assert "Apply requested person and resource filters" in prompt


def test_report_execution_contract_includes_every_available_batch_source():
    prompt = _report_prompt(
        {
            "question": "生成昨天的工程报告",
            "runtime_route": "plan_execute",
            "loaded_plugins": [
                {"plugin_key": "jira", "plugin_version": "1.0.0", "tools": [{"key": "jira_activity_summary"}]},
                {"plugin_key": "gitlab", "plugin_version": "1.0.0", "tools": [{"key": "gitlab_activity_summary"}]},
            ],
        },
        ["Produce an engineering activity report."],
    )

    assert "jira_activity_summary" in prompt
    assert "gitlab_activity_summary" in prompt
    assert "each requested period" in prompt


def test_explicit_activity_report_uses_high_confidence_plan_route():
    decision = _report_support._high_confidence_report_route(
        {
            "question": "获取昨天的工作报告",
            "loaded_plugins": [
                {"plugin_key": "github", "plugin_version": "1.0.0", "tools": [{"key": "github_activity_summary"}]}
            ],
        }
    )

    assert decision == {
        "intent": "action",
        "complexity": "complex",
        "route": "plan_execute",
        "required_capabilities": ["plugin"],
        "evidence_requirement": "tool_result",
    }


def test_jira_or_gitlab_batch_tool_enables_high_confidence_report_route():
    for tool_key in ("jira_activity_summary", "gitlab_activity_summary"):
        decision = _report_support._high_confidence_report_route(
            {
                "question": "生成昨天的工作报告",
                "loaded_plugins": [
                    {
                        "plugin_key": tool_key.split("_", 1)[0],
                        "plugin_version": "1.0.0",
                        "tools": [{"key": tool_key}],
                    }
                ],
            }
        )

        assert decision["route"] == "plan_execute"


def test_activity_report_hides_non_batch_github_tools():
    builtin = SimpleNamespace(name="append_file", metadata={"automatic_artifact_delivery": True})
    batch = SimpleNamespace(
        name="github_activity_summary",
        metadata={"plugin_key": "github"},
    )
    detail = SimpleNamespace(
        name="github_pull_request_get",
        metadata={"plugin_key": "github"},
    )
    jira = SimpleNamespace(
        name="jira_issue_list",
        metadata={"plugin_key": "jira"},
    )

    tools = _report_support._report_scoped_tools(
        {
            "question": "获取昨天的工作报告",
            "runtime_route": "plan_execute",
            "loaded_plugins": [
                {"plugin_key": "github", "plugin_version": "1.0.0", "tools": [{"key": "github_activity_summary"}]}
            ],
        },
        [builtin, batch, detail, jira],
    )

    assert tools == [builtin, batch, jira]


def test_activity_report_hides_non_batch_tools_for_each_batch_source():
    builtin = SimpleNamespace(name="append_file", metadata={"automatic_artifact_delivery": True})
    jira_batch = SimpleNamespace(
        name="jira_activity_summary",
        metadata={"plugin_key": "jira"},
    )
    jira_detail = SimpleNamespace(
        name="jira_get_issue",
        metadata={"plugin_key": "jira"},
    )
    gitlab_batch = SimpleNamespace(
        name="gitlab_activity_summary",
        metadata={"plugin_key": "gitlab"},
    )
    gitlab_detail = SimpleNamespace(
        name="gitlab_search_code",
        metadata={"plugin_key": "gitlab"},
    )

    tools = _report_support._report_scoped_tools(
        {
            "question": "获取昨天的工作报告",
            "runtime_route": "plan_execute",
            "loaded_plugins": [
                {"plugin_key": "jira", "plugin_version": "1.0.0", "tools": [{"key": "jira_activity_summary"}]},
                {"plugin_key": "gitlab", "plugin_version": "1.0.0", "tools": [{"key": "gitlab_activity_summary"}]},
            ],
        },
        [builtin, jira_batch, jira_detail, gitlab_batch, gitlab_detail],
    )

    assert tools == [builtin, jira_batch, gitlab_batch]


def test_activity_report_disables_repository_subagents(tmp_path):
    runtime = agent_runtime.LensDeepAgentRuntime(SimpleNamespace())
    state = SimpleNamespace(
        command={
            "task": "general_chat",
            "question": "获取昨天的工作报告",
            "runtime_route": "plan_execute",
            "loaded_plugins": [
                {"plugin_key": "github", "plugin_version": "1.0.0", "tools": [{"key": "github_activity_summary"}]}
            ],
        },
        runtime_mode=SimpleNamespace(general_chat=True),
        route_decision={"route": "plan_execute"},
    )

    state.agent_extensions = prepare_agent_extensions(state.command, tmp_path)
    state.agent_extensions.apply_policy(
        {"route": "plan_execute", "intent": "action", "evidence_requirement": "tool_result"}, []
    )
    assert runtime._subagents_enabled(state) is False
