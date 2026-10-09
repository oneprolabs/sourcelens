"""Activity summaries use bounded retrieval and reusable report evidence."""

import pytest

from lensnode.agent_runtime.system_prompts import _general_chat_system_prompt, _is_activity_report


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
