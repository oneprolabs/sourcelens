"""Reports preserve original evidence and render source-grounded facts."""

import json
from types import SimpleNamespace

import httpx
import pytest

from lensnode.activity_reports import ActivityLedger, bound_activity_result, finalize_activity_report


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


def test_render_is_deterministic_and_never_turns_old_resolution_into_daily_completion(tmp_path):
    ledger = ActivityLedger(tmp_path / "private")
    result = _result()
    args = {"since": result["since"], "until": result["until"], "projects": ["SL"]}
    ledger.record("jira", "connection", "1.0.0", "jira_activity_summary", args, result)
    first = ledger.report({"jira_activity_summary"}, "zh")
    second = ledger.report({"jira_activity_summary"}, "zh")
    assert first == second
    assert len(first["records"]) == 1
    assert first["records"][0]["event"] == "updated"
    assert "&lt;script&gt;" in first["html"]
    assert "<script>" not in first["html"]
    assert "当前负责人" in first["html"]


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
    report = ledger.report({"gitlab_activity_summary", "jira_activity_summary"}, "zh")
    assert report["complete"] is False
    assert report["records"][0]["event"] == "updated"
    assert "jira_activity_summary" in str(report["limitations"])
    assert "merged" not in report["records"][0]["event"]


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
    report = ledger.report({"github_activity_summary", "jira_activity_summary"}, "zh")
    assert len(report["records"]) == 2
    assert len({record["person_id"] for record in report["records"]}) == 2
    github = next(record for record in report["records"] if record["provider"] == "github")
    assert len(github["resources"]) == 2


def test_report_delivery_replaces_drafts_and_is_idempotent_until_evidence_changes(tmp_path, monkeypatch):
    ledger = ActivityLedger(tmp_path / "private")
    result = _result()
    ledger.record("jira", "connection", "1.0.0", "jira_activity_summary", {}, result)
    resources = SimpleNamespace(root=tmp_path / "scratch")
    resources.root.mkdir()
    config = SimpleNamespace(
        deliverable_max_bytes=10_000_000,
        request_timeout_s=5,
        token="node-token",
        tls_skip_verify=False,
        tls_ca_file=None,
        deliverable_upload_url="http://gateway/upload/",
    )
    command = {"run_uuid": "run-report", "_deliverables": {"old-draft.html": "old-candidate"}}
    uploads = []

    def respond(request):
        body = request.read()
        assert b'name="staged"\r\n\r\ntrue' in body
        uploads.append(body)
        return httpx.Response(200, json={"uuid": f"candidate-{len(uploads)}"})

    real_client = httpx.Client
    monkeypatch.setattr(
        "lensnode.agent_tools.httpx.Client",
        lambda **kwargs: real_client(
            transport=httpx.MockTransport(respond),
            **kwargs,
        ),
    )
    first = finalize_activity_report(ledger, {"jira_activity_summary"}, command, resources, config, None)
    candidates = dict(command["_deliverables"])
    assert first["html"].encode() in uploads[0]
    evidence = json.loads((resources.root / "activity-evidence.json").read_text())
    assert evidence["source_snapshots"][0]["result"] == result
    assert set(candidates) == {"activity-report.html", "activity-evidence.json"}
    finalize_activity_report(ledger, {"jira_activity_summary"}, command, resources, config, None)
    assert len(uploads) == 2
    assert command["_deliverables"] == candidates
    command["_deliverables"].clear()
    finalize_activity_report(ledger, {"jira_activity_summary"}, command, resources, config, None)
    assert len(uploads) == 4
    assert command["_deliverables"] != candidates


def test_evidence_budget_keeps_partial_results_and_marks_lost_tail():
    payload = _result()
    payload["items"] *= 30
    bounded = bound_activity_result(payload, 1000)
    assert len(json.dumps(bounded, separators=(",", ":")).encode()) <= 1000
    assert bounded["possibly_truncated"] is True
    assert len(payload["items"]) == 30


@pytest.mark.parametrize("asynchronous", [False, True])
def test_graph_finishes_from_all_saved_sources_without_a_second_model_turn(tmp_path, asynchronous):
    import asyncio

    from langchain.agents import create_agent
    from langchain.tools import tool
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage, HumanMessage

    from lensnode.activity_reports import ActivityReportMiddleware

    ledger = ActivityLedger(tmp_path / "private")

    @tool
    def jira_activity_summary() -> str:
        """Retrieve the requested activity snapshot."""

        result = _result()
        ledger.record("jira", "connection", "1.0.0", "jira_activity_summary", {}, result)
        return json.dumps(result)

    class Model(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            """Allow real graph assembly with deterministic fake responses."""

            return self

    model = Model(
        responses=[
            AIMessage(content="", tool_calls=[{"id": "source", "name": "jira_activity_summary", "args": {}}]),
            AIMessage(content="This draft must never be generated."),
        ]
    )
    graph = create_agent(
        model,
        tools=[jira_activity_summary],
        middleware=[
            ActivityReportMiddleware(
                ledger,
                {"jira_activity_summary"},
                lambda: ledger.report({"jira_activity_summary"}, "zh"),
            )
        ],
    )
    initial = {"messages": [HumanMessage(content="总结 10/8，每个人的工作内容")]}
    result = asyncio.run(graph.ainvoke(initial)) if asynchronous else graph.invoke(initial)
    assert model.i == 1
    assert "当期更新" in result["messages"][-1].content
    assert "This draft must never" not in result["messages"][-1].content
    assert "当前查询的活动快照" in result["messages"][-1].content


def test_terminal_runtime_replaces_model_draft_and_marks_incomplete_sources_partial(tmp_path, monkeypatch):
    from datetime import datetime, timezone

    from lensnode.agent_runtime import runtime as runtime_module

    ledger = ActivityLedger(tmp_path / "private")
    evidence = _result()
    evidence["possibly_truncated"] = True
    ledger.record("jira", "connection", "1.0.0", "jira_activity_summary", {}, evidence)
    report = ledger.report({"jira_activity_summary"}, "zh")
    command = {
        "question": "总结 10/8，每个人的工作内容",
        "task": "general_chat",
        "loaded_plugins": [{"tools": [{"key": "jira_activity_summary"}]}],
        "_deliverables": {"old-draft.html": "old"},
    }

    def finalize(*args):
        command["_deliverables"].clear()
        command["_deliverables"].update({"activity-report.html": "verified", "activity-evidence.json": "evidence"})
        return report

    monkeypatch.setattr(runtime_module, "finalize_activity_report", finalize)
    monkeypatch.setattr(
        runtime_module, "_run_agent_with_turn_limit", lambda *args, **kwargs: ("Unverified model draft", False, "")
    )
    outputs = []
    state = SimpleNamespace(
        command=command,
        question=command["question"],
        max_turns=0,
        activity_ledger=ledger,
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
        model=SimpleNamespace(stop_reason=None, token_usage={}),
        resources=SimpleNamespace(root=tmp_path),
        started_at=datetime.now(timezone.utc),
        run_span="run",
        new_span=lambda *args: "span",
        enter_span=lambda *args: None,
        exit_span=lambda *args: None,
        emit_agent_event=lambda *args: None,
        emit_output=lambda text, **kwargs: outputs.append((text, kwargs)),
        runtime_evidence={},
        evidence_requirement="artifact",
        required_capabilities=[],
        capability_middleware=None,
        decision_policy=None,
        consulted_sources=SimpleNamespace(citations=lambda *args: []),
    )
    result = runtime_module.LensDeepAgentRuntime(SimpleNamespace())._execute_agent(state)
    assert result["outcome"] == "partial"
    assert result["termination_detail"]["error_type"] == "verification"
    assert "Unverified model draft" not in result["answer"]
    assert result["answer"] == report["answer"]
    assert outputs[-1][1]["reset"] is True
    assert "old-draft.html" not in command["_deliverables"]


def test_invalid_source_links_are_not_rendered_as_active_links(tmp_path):
    ledger = ActivityLedger(tmp_path / "private")
    result = _result()
    result["items"][0]["url"] = "https://[invalid"
    ledger.record("jira", "connection", "1.0.0", "jira_activity_summary", {}, result)
    assert '<a href="https://' not in ledger.report({"jira_activity_summary"})["html"]


def test_resumed_report_does_not_reuse_the_rejected_model_drafts_verdict(tmp_path):
    from lensnode.agent_runtime.runtime import _post_run_decision_gates

    calls = []
    state = SimpleNamespace(
        activity_ledger=ActivityLedger(tmp_path / "private"),
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
    from lensnode.activity_reports import activity_ledger_root
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
