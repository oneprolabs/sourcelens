"""Reports preserve original evidence and render source-grounded facts."""

import json
from types import SimpleNamespace

import pytest

from lensnode.plugin_package_loader import load_plugin_support

_report_support = load_plugin_support("activity_reports")
ActivityLedger = _report_support.ActivityLedger
bound_activity_result = _report_support.bound_activity_result
build_activity_evidence_tool = _report_support.build_activity_evidence_tool


def _result():
    return {
        "ok": True,
        "since": "2026-10-07T16:00:00Z",
        "until": "2026-10-08T16:00:00Z",
        "items": [
            {
                "key": "SL-42",
                "summary": "<script>alert(1)</script>",
                "assignee": "Alice",
                "assignee_identity": "account-42",
                "status": "Done",
                "updated": "2026-10-08T08:00:00Z",
                "resolution_date": "2026-10-06T08:00:00Z",
                "project": "SL",
            }
        ],
        "possibly_truncated": False,
    }


def test_ledger_survives_reconstruction_and_reuses_exact_source_arguments(tmp_path):
    ledger = ActivityLedger(tmp_path / "private")
    result = _result()
    args = {"projects": ["SL"], "since": result["since"], "until": result["until"]}
    ledger.record("jira", "connection", "1.0.0", "jira_activity_summary", args, result)
    restored = ActivityLedger(tmp_path / "private")
    assert json.loads(restored.cached("jira", "connection", "1.0.0", "jira_activity_summary", args)) == result
    assert restored.cached("jira", "other-connection", "1.0.0", "jira_activity_summary", args) is None


def test_normalized_evidence_never_turns_old_resolution_into_daily_completion(tmp_path):
    ledger = ActivityLedger(tmp_path / "private")
    result = _result()
    args = {"since": result["since"], "until": result["until"], "projects": ["SL"]}
    ledger.record("jira", "connection", "1.0.0", "jira_activity_summary", args, result)
    first = ledger.report({"jira_activity_summary"})
    second = ledger.report({"jira_activity_summary"})
    assert first == second
    assert len(first["records"]) == 1
    assert first["records"][0]["event"] == "updated"
    assert first["records"][0]["title"] == "<script>alert(1)</script>"
    assert "current assignee, not update actor" in first["records"][0]["role"]
    assert "answer" not in first
    assert "html" not in first


def test_partial_and_missing_sources_remain_visible_and_closed_mr_is_not_merged(tmp_path):
    ledger = ActivityLedger(tmp_path / "private")
    result = {
        "ok": True,
        "since": "2026-10-07T16:00:00Z",
        "until": "2026-10-08T16:00:00Z",
        "projects": [
            {
                "project": "team/repo",
                "merge_requests": [
                    {
                        "number": 7,
                        "title": "Close MR",
                        "state": "closed",
                        "merged_at": "",
                        "updated_at": "2026-10-08T08:00:00Z",
                        "author": {"username": "alice"},
                    }
                ],
                "possibly_truncated": {"merge_requests": True},
            }
        ],
    }
    ledger.record("gitlab", "connection", "1.0.0", "gitlab_activity_summary", {}, result)
    report = ledger.report({"gitlab_activity_summary", "jira_activity_summary"})
    assert report["complete"] is False
    assert report["records"][0]["event"] == "updated"
    assert "jira_activity_summary" in str(report["limitations"])
    assert "merged" not in report["records"][0]["event"]


def test_repository_discovery_gaps_remain_partial_in_saved_evidence(tmp_path):
    """An empty wildcard result after failed discovery is not evidence of no work."""

    from lensnode.agent_runtime.evidence_gate import _source_coverage_incomplete

    result = {
        "ok": True,
        "since": "2026-10-07T16:00:00Z",
        "until": "2026-10-08T16:00:00Z",
        "repositories": [],
        "repositories_truncated": True,
        "source_coverage_complete": False,
        "coverage": {"discovery_complete": False, "reasons": ["discovery_failed"]},
    }
    ledger = ActivityLedger(tmp_path / "private")
    ledger.record(
        "github", "connection", "1.0.0", "github_activity_summary", {"repositories": ["oneprolabs/*"]}, result
    )

    projected = _report_support.project_activity_result(
        "summary", result, 900_000, {"summary": {"activity_report": True}}
    )
    assert projected["_evidence"]["complete"] is False
    assert _source_coverage_incomplete(json.dumps(projected)) is True
    assert ledger.report({"github_activity_summary"})["complete"] is False


def test_exact_commit_duplicates_are_grouped_but_display_names_do_not_merge_providers(tmp_path):
    ledger = ActivityLedger(tmp_path / "private")
    window = {"since": "2026-10-07T16:00:00Z", "until": "2026-10-08T16:00:00Z"}
    commit = {"sha": "abc", "author": "Alice", "login": "alice", "authored_at": "2026-10-08T08:00:00Z"}
    ledger.record(
        "github",
        "connection",
        "1.0.0",
        "github_activity_summary",
        {},
        {
            **window,
            "ok": True,
            "repositories": [{"repository": repo, "commits": [commit]} for repo in ["team/one", "team/two"]],
        },
    )
    result = _result()
    ledger.record("jira", "connection", "1.0.0", "jira_activity_summary", {}, result)
    report = ledger.report({"github_activity_summary", "jira_activity_summary"})
    assert len(report["records"]) == 2
    assert len({record["person_id"] for record in report["records"]}) == 2
    github = next(record for record in report["records"] if record["provider"] == "github")
    assert len(github["resources"]) == 2


def test_evidence_budget_keeps_partial_results_and_marks_lost_tail():
    payload = _result()
    payload["items"] *= 30
    bounded = bound_activity_result(payload, 1000)
    assert len(json.dumps(bounded, separators=(",", ":")).encode()) <= 1000
    assert bounded["possibly_truncated"] is True
    assert len(payload["items"]) == 30


@pytest.mark.parametrize("asynchronous", [False, True])
def test_graph_continues_from_source_evidence_to_business_report_file(tmp_path, asynchronous):
    import asyncio

    from langchain.agents import create_agent
    from langchain.tools import tool
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage, HumanMessage

    ledger = ActivityLedger(tmp_path / "private")
    report = tmp_path / "deliverables" / "report.html"

    @tool
    def jira_activity_summary() -> str:
        """Retrieve the requested activity snapshot."""

        result = _result()
        ledger.record("jira", "connection", "1.0.0", "jira_activity_summary", {}, result)
        return json.dumps(result)

    @tool
    def write_file(content: str) -> str:
        """Generate the business report for automatic host delivery."""

        report.parent.mkdir(exist_ok=True)
        report.write_text(content)
        return "Updated file /deliverables/report.html"

    class Model(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            """Allow real graph assembly with deterministic fake responses."""

            return self

    answer = "Alice: work in progress; detailed report generated."
    model = Model(
        responses=[
            AIMessage(content="", tool_calls=[{"id": "source", "name": "jira_activity_summary", "args": {}}]),
            AIMessage(content="", tool_calls=[{"id": "evidence", "name": "read_activity_evidence", "args": {}}]),
            AIMessage(
                content="",
                tool_calls=[{
                    "id": "write",
                    "name": "write_file",
                    "args": {"content": "<h1>Alice: work in progress</h1>"},
                }],
            ),
            AIMessage(content=answer),
        ]
    )
    graph = create_agent(
        model,
        tools=[
            jira_activity_summary,
            build_activity_evidence_tool(ledger, {"jira_activity_summary"}),
            write_file,
        ],
    )
    initial = {"messages": [HumanMessage(content="总结 10/8，每个人的工作内容")]}
    result = asyncio.run(graph.ainvoke(initial)) if asynchronous else graph.invoke(initial)
    assert result["messages"][-1].content == answer
    assert report.read_text() == "<h1>Alice: work in progress</h1>"
    assert ledger.read_progress(ledger.report({"jira_activity_summary"}))["complete"] is True


@pytest.mark.parametrize("coverage", ["missing", "complete", "partial"])
@pytest.mark.parametrize("host_incomplete", [False, True])
@pytest.mark.parametrize("stop_reason", [None, "token_budget_wrapup"])
def test_terminal_runtime_preserves_business_report_and_deliverables(
    tmp_path, monkeypatch, coverage, host_incomplete, stop_reason
):
    from datetime import datetime, timezone

    from lensnode.agent_runtime import runtime as runtime_module

    evidence = _result()
    evidence["possibly_truncated"] = coverage == "partial"
    command = {
        "question": "总结 10/8，每个人的工作内容",
        "task": "general_chat",
        "run_uuid": "run-report",
        "loaded_plugins": [{
            "plugin_key": "jira", "plugin_version": "1.0.0", "connection_uuid": "connection",
            "tools": [{"key": "jira_activity_summary"}],
        }],
        "_deliverables": {"business-report.html": "business-candidate"},
    }
    from lensnode.plugin_agent import prepare_agent_extensions

    extensions = prepare_agent_extensions(command, tmp_path)
    extensions.apply_policy(extensions.preferred_route, extensions.tools)
    if coverage != "missing":
        extensions.extensions[0].ledger.record("jira", "connection", "1.0.0", "jira_activity_summary", {}, evidence)
        extensions.tools[0].invoke({})
    monkeypatch.setattr(
        runtime_module,
        "_run_agent_with_turn_limit",
        lambda *args, **kwargs: ("Business summary based on retrieved evidence", False, None),
    )
    outputs = []
    state = SimpleNamespace(
        command=command,
        kwargs={},
        question=command["question"],
        max_turns=0,
        agent_extensions=extensions,
        runtime_mode=SimpleNamespace(execution_gates=False, general_chat=False),
        initial_messages=[],
        resume_state=None,
        agent=None,
        checkpoint_thread=None,
        resume_from_graph_checkpoint=False,
        checkpoint_ready=False,
        initial_checkpoint_seeded=False,
        token_budget_wrapup_event=None,
        cancel_event=None,
        config=SimpleNamespace(),
        model=SimpleNamespace(stop_reason=stop_reason, token_usage={}),
        resources=SimpleNamespace(root=tmp_path),
        started_at=datetime.now(timezone.utc),
        run_span="run",
        new_span=lambda *args: "span",
        enter_span=lambda *args: None,
        exit_span=lambda *args: None,
        emit_agent_event=lambda *args: None,
        emit_output=lambda text, **kwargs: outputs.append((text, kwargs)),
        runtime_evidence={"evidence_completeness": "incomplete"} if host_incomplete else {},
        evidence_requirement="artifact",
        evidence_middleware=None,
        required_capabilities=[],
        capability_middleware=None,
        decision_policy=None,
        consulted_sources=SimpleNamespace(citations=lambda *args: []),
    )
    result = runtime_module.LensDeepAgentRuntime(SimpleNamespace())._execute_agent(state)
    assert result["answer"] == "Business summary based on retrieved evidence"
    assert result["stop_reason"] is None
    assert not outputs
    assert command["_deliverables"] == {"business-report.html": "business-candidate"}
    assert result["outcome"] == ("partial" if coverage == "partial" or host_incomplete else "completed")
    if coverage == "partial" or host_incomplete:
        assert result["termination_detail"]["error_type"] == "verification"


def test_source_coverage_is_not_forced_incomplete_when_snapshot_has_no_gaps(tmp_path):
    ledger = ActivityLedger(tmp_path / "private")
    result = _result()
    ledger.record("jira", "connection", "1.0.0", "jira_activity_summary", {}, result)
    report = ledger.report({"jira_activity_summary"})
    assert report["complete"] is True
    assert report["limitations"] == []


def test_resumed_report_does_not_reuse_the_rejected_model_drafts_verdict(tmp_path):
    from lensnode.agent_runtime.runtime import _post_run_decision_gates

    calls = []
    state = SimpleNamespace(
        agent_extensions=SimpleNamespace(refresh_evidence=True),
        resume_state=SimpleNamespace(decision_gates={"answer_supported": "unsupported"}),
        decision_policy=SimpleNamespace(
            post_run_checks=lambda *args: calls.append(args) or {"answer_supported": "supported"}
        ),
        question="q",
        runtime_evidence={},
        checkpoint_ready=False,
    )
    assert _post_run_decision_gates(state, "New deterministic report")["answer_supported"] == "supported"
    assert len(calls) == 1


def test_private_source_evidence_is_removed_with_terminal_checkpoint_cleanup(tmp_path, monkeypatch):
    activity_ledger_root = _report_support.activity_ledger_root
    from lensnode.checkpoint import cleanup_run_checkpoint

    ledger = ActivityLedger(activity_ledger_root(tmp_path, "run-report"))
    ledger.record("jira", "connection", "1.0.0", "jira_activity_summary", {}, _result())
    monkeypatch.setenv("LENSNODE_CHECKPOINT_ENABLED", "false")
    cleanup_run_checkpoint("run-report", tmp_path)
    assert not ledger.root.exists()


def test_closed_mr_date_is_not_reported_as_an_update_or_merge(tmp_path):
    ledger = ActivityLedger(tmp_path / "private")
    result = _result()
    result.pop("items")
    result["projects"] = [
        {
            "project": "team/repo",
            "merge_requests": [
                {
                    "number": 7,
                    "title": "Closed",
                    "author": {"username": "alice"},
                    "updated_at": "2026-10-09T08:00:00Z",
                    "closed_at": "2026-10-08T08:00:00Z",
                    "merged_at": "",
                }
            ],
        }
    ]
    ledger.record("gitlab", "connection", "1.0.0", "gitlab_activity_summary", {}, result)
    report = ledger.report({"gitlab_activity_summary"})
    assert report["records"][0]["event"] == "closed"
    assert report["records"][0]["timestamp"] == "2026-10-08T08:00:00Z"


def test_display_name_alone_does_not_establish_a_shared_source_identity(tmp_path):
    ledger = ActivityLedger(tmp_path / "private")
    result = _result()
    result.pop("items")
    result["projects"] = [
        {
            "project": "team/repo",
            "issues": [
                {"number": number, "title": "Work", "author": {"name": "Alice"}, "updated_at": "2026-10-08T08:00:00Z"}
                for number in [1, 2]
            ],
        }
    ]
    ledger.record("gitlab", "connection", "1.0.0", "gitlab_activity_summary", {}, result)
    report = ledger.report({"gitlab_activity_summary"})
    assert len({record["person_id"] for record in report["records"]}) == 2


def test_evidence_read_is_paged_and_does_not_publish_or_finish(tmp_path):
    ledger = ActivityLedger(tmp_path / "private")
    result = _result()
    result["items"] = [dict(result["items"][0], key=f"SL-{number}") for number in range(3)]
    ledger.record("jira", "connection", "1.0.0", "jira_activity_summary", {}, result)
    read = build_activity_evidence_tool(ledger, {"jira_activity_summary"})
    first = json.loads(read.invoke({"limit": 2}))
    last = json.loads(read.invoke({"offset": first["next_offset"], "limit": 2}))
    assert len(first["records"]) == 2
    assert len(last["records"]) == 1
    assert last["next_offset"] is None
    assert first["source_coverage_complete"] is True
    assert "answer" not in first
    assert "files" not in first
    assert not list(tmp_path.glob("*.html"))
    assert json.loads(read.invoke({"offset": -1}))["error"] == "ACTIVITY_EVIDENCE_PAGE_INVALID"


def test_evidence_page_exposes_all_observed_events_and_independent_source_coverage(tmp_path):
    """A partial first page cannot hide a resolution or contaminate another source's coverage."""

    ledger = ActivityLedger(tmp_path / "private")
    jira = _result()
    jira["items"] = [dict(jira["items"][0], key=f"SL-{number}") for number in range(114)]
    jira["items"][-1]["resolution_date"] = "2026-10-08T09:35:59Z"
    ledger.record("jira", "connection", "1.0.0", "jira_activity_summary", {}, jira)
    github = {
        "ok": True, "since": jira["since"], "until": jira["until"],
        "repositories": [{"repository": "team/repo", "errors": ["PR response too large"]}],
    }
    ledger.record("github", "connection", "1.0.0", "github_activity_summary", {}, github)
    page = json.loads(build_activity_evidence_tool(ledger, {
        "jira_activity_summary", "github_activity_summary"
    }).invoke({"limit": 80}))

    assert page["total_records"] == 114
    assert page["next_page"] == {
        "tool": "read_activity_evidence", "arguments": {"offset": len(page["records"]), "limit": 80}
    }
    assert page["page_complete"] is False
    assert page["source_coverage"]["jira_activity_summary"]["complete"] is True
    assert page["source_coverage"]["github_activity_summary"]["complete"] is False
    facts = page["observed_events"]
    assert next(fact for fact in facts if fact["event"] == "resolved")["record_ids"] == ["SL-113"]
    assert sum(fact["count"] for fact in facts) == 114


def test_empty_out_of_range_evidence_page_is_not_a_complete_dataset(tmp_path):
    ledger = ActivityLedger(tmp_path / "private")
    ledger.record("jira", "connection", "1.0.0", "jira_activity_summary", {}, _result())
    page = json.loads(build_activity_evidence_tool(ledger, {"jira_activity_summary"}).invoke({"offset": 10}))
    assert page["ok"] is False
    assert page["error"] == "ACTIVITY_EVIDENCE_PAGE_INVALID"


def test_evidence_read_without_sources_keeps_clarification_flow(tmp_path):
    ledger = ActivityLedger(tmp_path / "private")
    read = build_activity_evidence_tool(ledger, {"jira_activity_summary"})
    assert json.loads(read.invoke({})) == {"ok": False, "error": "ACTIVITY_EVIDENCE_REQUIRED"}


def test_saved_partial_evidence_is_still_incomplete_for_answer_verification(tmp_path):
    from lensnode.agent_runtime.evidence_gate import _source_coverage_incomplete

    ledger = ActivityLedger(tmp_path / "private")
    result = _result()
    result["possibly_truncated"] = True
    ledger.record("jira", "connection", "1.0.0", "jira_activity_summary", {}, result)
    read = build_activity_evidence_tool(ledger, {"jira_activity_summary"})
    assert _source_coverage_incomplete(read.invoke({})) is True


def test_multiple_requested_windows_remain_separate_evidence_without_forced_partial(tmp_path):
    ledger = ActivityLedger(tmp_path / "private")
    first = _result()
    second = _result()
    second.update(since="2026-10-08T16:00:00Z", until="2026-10-09T16:00:00Z")
    second["items"][0]["updated"] = "2026-10-09T08:00:00Z"
    for result in (first, second):
        args = {"since": result["since"], "until": result["until"], "projects": ["SL"]}
        ledger.record("jira", "connection", "1.0.0", "jira_activity_summary", args, result)
    evidence = json.loads(build_activity_evidence_tool(ledger, {"jira_activity_summary"}).invoke({}))
    assert len(evidence["windows"]) == 2
    assert len(evidence["records"]) == 2
    assert evidence["source_coverage_complete"] is True
