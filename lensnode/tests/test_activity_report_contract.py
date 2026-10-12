"""Activity summaries use bounded retrieval and reusable report evidence."""

import pytest

from lensnode.agent_runtime.system_prompts import (
    _activity_report_expected_tools,
    _general_chat_system_prompt,
    _is_activity_report,
)


@pytest.mark.parametrize(
    "question",
    [
        "总结 10/8，每个人的工作内容",
        "Summarize yesterday's work by person",
        "生成昨天的工程报告",
    ],
)
def test_work_summaries_receive_batch_contract_without_a_planned_route(question):
    command = {
        "question": question,
        "runtime_route": "direct_execute",
        "loaded_plugins": [{"tools": [{"key": "jira_activity_summary"}]}],
    }
    assert _is_activity_report(command)
    prompt = _general_chat_system_prompt(command, [])
    assert "once per available source" in prompt
    assert "evidence ledger" in prompt
    assert "deterministic template" in prompt
    assert "Use its returned grounded answer, then stop" not in prompt
    assert "Do not generate HTML yourself" not in prompt
    assert "Follow the bound report Skill's output format" in prompt


def test_code_summary_does_not_trigger_activity_report_routing():
    assert not _is_activity_report(
        {
            "question": "总结这个类的设计",
            "loaded_plugins": [{"tools": [{"key": "jira_activity_summary"}]}],
        }
    )


@pytest.mark.parametrize("question", ["生成这个故障的根因报告", "Report a bug in this class", "分析测试报告"])
def test_unrelated_reports_do_not_trigger_activity_report_routing(question):
    assert not _is_activity_report(
        {"question": question, "loaded_plugins": [{"tools": [{"key": "jira_activity_summary"}]}]}
    )


@pytest.mark.parametrize(
    "question",
    [
        "Summarize only Alice's work yesterday",
        "总结昨天 Alice 的工作内容",
        "总结这周和上周每个人的工作内容",
        "Summarize this week and last week's work by person",
        "总结 10/7、10/8，每个人的工作内容",
        "总结 10/8，每个人的工作内容，仅项目 SL",
        "总结 10/8，每个人的工作内容，分别查询 60 个项目",
    ],
)
def test_filtered_or_multiple_period_reports_keep_the_model_completion_flow(question):
    command = {"question": question, "loaded_plugins": [{"tools": [{"key": "jira_activity_summary"}]}]}
    assert _is_activity_report(command)
    prompt = _general_chat_system_prompt(command, [])
    assert "each requested period" in prompt
    assert "Apply requested person and resource filters" in prompt
    assert "read_activity_evidence" in prompt


def test_named_source_reports_do_not_require_unrequested_bound_sources():
    command = {
        "question": "总结昨天 GitHub 每个人的工作内容",
        "loaded_plugins": [
            {
                "tools": [
                    {"key": key}
                    for key in (
                        "github_activity_summary",
                        "gitlab_activity_summary",
                        "jira_activity_summary",
                    )
                ]
            }
        ],
    }
    assert _activity_report_expected_tools(command) == {"github_activity_summary"}
    command["question"] = "总结昨天每个人的工作内容"
    assert len(_activity_report_expected_tools(command)) == 3
