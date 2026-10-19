"""Host checks for unread report evidence and unsupported personnel conclusions."""

import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from lensnode.agent_runtime.evidence_gate import EvidenceGateMiddleware
from lensnode.agent_runtime.outcomes import _finalize_runtime_outcome
from lensnode.plugin_agent import prepare_agent_extensions
from lensnode.plugin_package_loader import load_plugin_support, load_runtime_contract

support = load_plugin_support("activity_reports")


def report(tmp_path, count=3, partial=False):
    """Prepare real extension state with a bounded Jira source snapshot."""

    command = {
        "run_uuid": "guard-run", "question": "总结每个人昨天的工作内容", "task": "general_chat",
        "loaded_plugins": [{"plugin_key": "jira", "plugin_version": "1.0.0", "connection_uuid": "jira",
                            "tools": [{"key": "jira_activity_summary"}]}],
    }
    extensions = prepare_agent_extensions(command, tmp_path)
    extensions.apply_policy(extensions.preferred_route, extensions.tools)
    ledger = extensions.extensions[0].ledger
    source = {
        "ok": True, "since": "2026-10-15T00:00:00Z", "until": "2026-10-16T00:00:00Z",
        "possibly_truncated": partial,
        "items": [{"key": f"SL-{index}", "assignee": "Alice", "assignee_identity": "alice",
                   "updated": "2026-10-15T08:00:00Z", "resolution_date": "2026-10-15T08:00:00Z"}
                  for index in range(count)],
    }
    ledger.record("jira", "jira", "1.0.0", "jira_activity_summary", {}, source)
    return command, extensions, source


def test_duplicate_and_skipped_pages_do_not_complete_review_and_resume_keeps_cursor(tmp_path):
    command, extensions, _ = report(tmp_path)
    read = extensions.tools[0]
    read.invoke({"offset": 0, "limit": 1})
    read.invoke({"offset": 0, "limit": 1})
    read.invoke({"offset": 2, "limit": 1})
    evidence = extensions.evidence()
    assert evidence["evidence_completeness"] == "complete"
    assert evidence["review_completeness"] == "incomplete"
    assert extensions.completion_checks("报告已完成")[0]["code"] == "activity_evidence_unread"
    restored = prepare_agent_extensions(command, tmp_path)
    restored.apply_policy(restored.preferred_route, restored.tools)
    restored.tools[0].invoke({"offset": 1, "limit": 1})
    assert restored.evidence()["review_completeness"] == "complete"
    assert restored.completion_checks("报告已完成") == []


def test_new_source_snapshot_invalidates_previously_consumed_pages(tmp_path):
    _, extensions, source = report(tmp_path)
    extensions.tools[0].invoke({})
    assert extensions.completion_checks("报告已完成") == []
    source["items"][0]["summary"] = "A corrected source record"
    extensions.extensions[0].ledger.record("jira", "jira", "1.0.0", "jira_activity_summary", {}, source)
    assert extensions.completion_checks("报告已完成")[0]["code"] == "activity_evidence_unread"


@pytest.mark.parametrize("text", ["Alice 零完成。", "Alice completed nothing.", "Alice 的产出偏低。",
                                      "<table><tr><td>Alice</td><td>零完成</td></tr></table>",
                                      "Alice 长期未推进。", "Alice has low productivity."])
def test_incomplete_evidence_does_not_support_unqualified_personnel_claims(tmp_path, text):
    _, extensions, _ = report(tmp_path, partial=True)
    extensions.tools[0].invoke({})
    issues = extensions.completion_checks(text)
    assert any(issue["code"] == "activity_claim_unsupported" for issue in issues)


def test_qualified_absence_and_positive_observations_remain_reportable(tmp_path):
    _, extensions, _ = report(tmp_path, partial=True)
    extensions.tools[0].invoke({})
    assert extensions.completion_checks("Alice 名下有已解决工单。GitHub 来源不完整，不能据此判断零完成或产出偏低。") == []
    assert extensions.completion_checks("本次检索未观察到 Alice 的提交，不代表没有工作。") == []


@pytest.mark.parametrize("text", [
    "来源不完整，不能据此判断 Bob。Alice 零完成。",
    "Alice completed nothing. There is no evidence about Bob.",
    "<p>不能据此判断 Bob</p><p>Alice <b>零</b>完成</p>",
])
def test_caveat_for_another_sentence_does_not_hide_a_negative_claim(tmp_path, text):
    _, extensions, _ = report(tmp_path, partial=True)
    extensions.tools[0].invoke({})
    assert any(issue["code"] == "activity_claim_unsupported" for issue in extensions.completion_checks(text))


def test_complete_activity_counts_still_do_not_establish_productivity_or_zero_work(tmp_path):
    _, extensions, _ = report(tmp_path)
    extensions.tools[0].invoke({})
    assert extensions.completion_checks("Alice 零完成，产出偏低。")


def test_empty_evidence_page_counts_as_reviewed(tmp_path):
    _, extensions, _ = report(tmp_path, count=0)
    assert extensions.evidence()["review_completeness"] == "incomplete"
    extensions.tools[0].invoke({})
    assert extensions.evidence()["review_completeness"] == "complete"


@pytest.mark.parametrize("read_pages,filename,content", [
    (False, "report.html", "<p>Alice 名下已解决 SL-1。</p>"),
    (True, "report.html", "<table><tr><td>Alice</td><td>零完成</td></tr></table>"),
    (True, "report.xhtml", "<p>Alice 零完成。</p>"),
    (True, "report.json", '{"conclusion":"Alice completed nothing."}'),
])
def test_unread_or_unsupported_html_is_rejected_before_any_upload(tmp_path, monkeypatch, read_pages, filename, content):
    from lensnode.agent_tools import _build_save_deliverable_tool

    command, extensions, _ = report(tmp_path, partial=True)
    if read_pages:
        extensions.tools[0].invoke({})
    command["_validate_deliverable"] = extensions.completion_checks
    command["_deliverables"] = {filename: "old-output"}
    (tmp_path / filename).write_text(content)

    def no_network(*args, **kwargs):
        raise AssertionError("Unsupported artifact must never reach the upload service")

    monkeypatch.setattr("lensnode.agent_tools.httpx.Client", no_network)
    tool = _build_save_deliverable_tool(command, SimpleNamespace(root=tmp_path),
                                       SimpleNamespace(deliverable_max_bytes=10000), None)
    result = json.loads(tool.invoke({"path": filename}))
    assert result["ok"] is False
    assert result["error"] == "COMPLETION_CHECK_FAILED"
    assert command["_deliverables"] == {}


def test_activity_evidence_tool_is_marked_as_inline_bounded_result(tmp_path):
    _, extensions, _ = report(tmp_path)
    assert extensions.tools[0].metadata["bounded_inline_result"] is True


def test_large_ledger_metadata_does_not_reduce_every_page_to_one_record(tmp_path, monkeypatch):
    _, extensions, _ = report(tmp_path, count=122, partial=True)
    ledger = extensions.extensions[0].ledger
    evidence = ledger.report({"jira_activity_summary"})
    evidence["observed_events"] = [{"detail": "event" * 10000}]
    for coverage in evidence["source_coverage"].values():
        coverage["details"] = "coverage" * 10000
    monkeypatch.setattr(ledger, "report", lambda *args: evidence)
    offset = 0
    pages = []
    while offset is not None:
        raw = extensions.tools[0].invoke({"offset": offset, "limit": 100})
        assert len(raw) <= support.MAX_ACTIVITY_PAGE_CHARS
        page = json.loads(raw)
        assert page["ok"] is True
        assert len(page["records"]) >= min(10, 122 - offset)
        pages.append(page)
        offset = page["next_offset"]
    assert len(pages) <= 10
    assert sum(len(page["records"]) for page in pages) == 122
    assert pages[-1]["next_page"] is None
    assert pages[-1]["read_progress"]["complete"] is True
    assert extensions.completion_checks("已观察到 Alice 名下解决的工单，来源存在缺口。") == []


def test_page_size_includes_progress_and_rejected_pages_do_not_mark_evidence_read(tmp_path, monkeypatch):
    _, extensions, _ = report(tmp_path, count=1)
    ledger = extensions.extensions[0].ledger
    evidence = ledger.report({"jira_activity_summary"})
    monkeypatch.setattr(ledger, "report", lambda *args: evidence)
    evidence["records"][0]["title"] = "x" * support.MAX_ACTIVITY_PAGE_CHARS
    rejected = json.loads(extensions.tools[0].invoke({}))
    assert rejected["error"] == "ACTIVITY_EVIDENCE_PAGE_TOO_LARGE"
    assert ledger.read_progress(evidence)["complete"] is False
    evidence["records"][0]["title"] = ""
    raw = extensions.tools[0].invoke({})
    monkeypatch.setattr(support, "MAX_ACTIVITY_PAGE_CHARS", len(raw) - 1)
    ledger.root.joinpath("review", "progress.json").unlink()
    rejected = json.loads(extensions.tools[0].invoke({}))
    if rejected["ok"]:
        assert len(support._json(rejected)) <= support.MAX_ACTIVITY_PAGE_CHARS
    else:
        assert ledger.read_progress(evidence)["complete"] is False


@pytest.mark.parametrize("asynchronous", [False, True])
def test_consumed_pages_remain_inline_after_real_filesystem_offload(tmp_path, asynchronous):
    import asyncio

    from deepagents.backends import FilesystemBackend
    from deepagents.middleware.filesystem import FilesystemMiddleware
    from langchain_core.messages import ToolMessage

    from lensnode.agent_runtime.offload import _preserve_inline_tool_results

    _, extensions, _ = report(tmp_path, count=30)
    tool = extensions.tools[0]
    _preserve_inline_tool_results()
    middleware = FilesystemMiddleware(backend=FilesystemBackend(root_dir=tmp_path, virtual_mode=True),
                                      tool_token_limit_before_evict=100)
    request = SimpleNamespace(tool=tool, tool_call={"name": tool.name, "id": "page", "args": {}})

    def read(request):
        return ToolMessage(name=tool.name, tool_call_id="page", content=tool.invoke({}))

    async def aread(request):
        return read(request)

    output = asyncio.run(middleware.awrap_tool_call(request, aread)) if asynchronous else middleware.wrap_tool_call(request, read)
    payload = json.loads(output.content)
    assert payload["records"]
    assert len(output.content) <= support.MAX_ACTIVITY_PAGE_CHARS + 256
    assert payload["read_progress"]["complete"] is False
    assert payload["next_offset"] == len(payload["records"])


def test_unread_pages_are_partial_without_configured_decision_model(tmp_path):
    _, extensions, _ = report(tmp_path)
    evidence = extensions.evidence()
    outcome, detail = _finalize_runtime_outcome(
        capability_middleware=None, evidence_requirement="tool_result", required_capabilities=[],
        truncated=False, stop_reason=None, execution_gate_enabled=False, runtime_evidence=evidence,
    )
    assert outcome == "partial"
    assert detail["reason"] == "review_material_incomplete"


def test_host_check_gets_one_bounded_repair_without_decision_gate():
    pending = [{"code": "activity_evidence_unread", "recovery": "Call read_activity_evidence offset=1."}]
    gate = EvidenceGateMiddleware(SimpleNamespace(has_evidence_gates=lambda: False), "report",
                                  completion_check=lambda answer: pending)
    state = {"messages": [AIMessage(content="Done")]}
    assert gate.after_model(state, None)["jump_to"] == "model"
    assert gate.after_model(state, None) is None
    pending.clear()
    assert gate.after_model(state, None) is None


def test_report_middleware_exists_without_decision_gates_and_refreshes_progress(tmp_path):
    from lensnode.agent_runtime.runtime import _build_evidence_middleware

    _, extensions, _ = report(tmp_path)
    state = SimpleNamespace(agent_extensions=extensions, command={}, question="report", runtime_evidence={},
                            decision_policy=None)
    middleware = _build_evidence_middleware(state)
    assert middleware is not None
    final = {"messages": [AIMessage(content="Done")]}
    assert middleware.after_model(final, None)["jump_to"] == "model"
    extensions.tools[0].invoke({})
    assert middleware.after_model(final, None) is None
    assert state.runtime_evidence["review_completeness"] == "complete"


@pytest.mark.parametrize("answer", ["Alice 零完成。", "Alice completed nothing."])
def test_failed_completion_check_cannot_repeat_the_rejected_assertion(answer):
    from lensnode.agent_runtime.runtime import _enforce_completion_status

    checks = [{"code": "activity_claim_unsupported"}]
    result = _enforce_completion_status(answer, {"answer_language": "zh-CN"}, checks)
    assert answer not in result
    if answer.isascii():
        assert result.startswith("The result could not be verified.")
    else:
        assert "核验未通过" in result
    assert _enforce_completion_status(answer, {}, []) == answer


@pytest.mark.parametrize("stop_reason", ["token_budget_wrapup", "evidence_convergence_limit"])
@pytest.mark.parametrize("evidence", [
    {"completion_issues": [{"code": "activity_evidence_unread"}], "review_completeness": "incomplete"},
    {"artifact_required": True},
    {"review_completeness": "incomplete"},
    {"evidence_completeness": "incomplete"},
])
def test_verification_failure_keeps_the_execution_limit_that_prevented_completion(stop_reason, evidence):
    outcome, detail = _finalize_runtime_outcome(
        capability_middleware=None, evidence_requirement="artifact", truncated=True,
        stop_reason=stop_reason, execution_gate_enabled=False, runtime_evidence=evidence,
    )
    assert outcome == "partial"
    assert detail["trigger"] == stop_reason
    assert detail["error_type"] == "verification"


def test_unread_evidence_failure_explains_why_the_report_was_withheld():
    from lensnode.agent_runtime.runtime import _enforce_completion_status

    result = _enforce_completion_status("报告已完成", {}, [{"code": "activity_evidence_unread"}])
    assert "工作记录未完成核对" in result
    assert "附件未发布" in result


@pytest.mark.parametrize("provider,kind,event,actor_field", [
    ("github", "issues", "closed", "closed_by"),
    ("gitlab", "merge_requests", "merged", "merged_by"),
    ("jira", "issues", "resolved", "resolved_by"),
])
def test_event_actor_is_distinct_from_author_and_current_assignee(provider, kind, event, actor_field):
    item = {"key": "SL-1", "number": 1, "author": {"username": "alice"}, "assignee": "Bob",
            "assignee_identity": "bob", actor_field: {"username": "carol", "name": "Carol"},
            "updated_at": "2026-10-15T09:00:00Z", "updated": "2026-10-15T09:00:00Z",
            {"closed": "closed_at", "merged": "merged_at", "resolved": "resolution_date"}[event]:
                "2026-10-15T08:00:00Z"}
    record = support._record(provider, "conn", "repo", kind, item,
                             "2026-10-15T00:00:00Z", "2026-10-16T00:00:00Z")
    assert record["event_actor"]["identity"] == "carol"
    assert record["event_actor"]["verified"] is True
    assert record["roles"]["author"]["identity"] == "alice"
    assert record["roles"]["assignee"]["identity"] == "bob"
    del item[actor_field]
    record = support._record(provider, "conn", "repo", kind, item,
                             "2026-10-15T00:00:00Z", "2026-10-16T00:00:00Z")
    assert record["event_actor"] is None


def test_display_name_only_is_never_a_verified_actor():
    item = {"number": 1, "author": {"username": "alice", "name": "Alice"},
            "merged_by": {"name": "Carol"}, "merged_at": "2026-10-15T08:00:00Z"}
    record = support._record("gitlab", "conn", "repo", "merge_requests", item,
                             "2026-10-15T00:00:00Z", "2026-10-16T00:00:00Z")
    assert record["event_actor"]["verified"] is False
    assert record["roles"]["author"]["name"] == "Alice"


def test_update_actor_and_jira_creator_remain_distinct_from_current_assignee():
    item = {"key": "SL-1", "assignee": "Bob", "assignee_identity": "bob",
            "creator": "Alice", "creator_identity": "alice", "reporter": "Dan", "reporter_identity": "dan",
            "updated_by": {"accountId": "carol", "displayName": "Carol"},
            "updated": "2026-10-15T08:00:00Z"}
    record = support._record("jira", "conn", "repo", "issues", item,
                             "2026-10-15T00:00:00Z", "2026-10-16T00:00:00Z")
    assert record["event_actor"]["identity"] == "carol"
    assert record["roles"]["creator"]["identity"] == "alice"
    assert record["roles"]["reporter"]["identity"] == "dan"
    assert record["person"] == "Bob"


def test_provider_projections_preserve_available_event_actors_without_inventing_missing_ones():
    github = load_runtime_contract("github", "1.0.0").execute_tool.__globals__
    gitlab = load_runtime_contract("gitlab", "1.0.0").execute_tool.__globals__
    jira = load_runtime_contract("jira", "1.0.0").execute_tool.__globals__
    assert github["_activity_issue_summary"]({"closed_by": {"login": "carol"}})["closed_by"] == "carol"
    assert github["_activity_pull_request_summary"]({"merged_by": {"login": "carol"}})["merged_by"] == "carol"
    assert gitlab["_activity_merge_request"]({"merge_user": {"username": "carol"}})["merged_by"]["username"] == "carol"
    assert gitlab["_activity_issue"]({"closed_by": {"username": "carol"}})["closed_by"]["username"] == "carol"
    projected = jira["_activity_issue"]({"key": "SL-1", "fields": {
        "creator": {"accountId": "alice-id", "displayName": "Alice"},
        "reporter": {"accountId": "bob-id", "displayName": "Bob"},
    }}, "SL")
    assert projected["creator_identity"] == "alice-id"
    assert projected["reporter_identity"] == "bob-id"
    assert not projected.get("updated_by")
