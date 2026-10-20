"""Host evidence-progress checks leave report semantics to the model."""

import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from lensnode import tool_evidence
from lensnode.agent_runtime.evidence_gate import EvidenceGateMiddleware
from lensnode.agent_runtime.outcomes import _finalize_runtime_outcome
from lensnode.plugin_agent import prepare_agent_extensions
from lensnode.plugin_package_loader import load_plugin_support, load_runtime_contract

support = load_plugin_support("activity_evidence")


def report(tmp_path, count=3, partial=False):
    """Prepare real extension state with a bounded Jira source snapshot."""

    command = {
        "run_uuid": "guard-run", "question": "总结每个人昨天的工作内容", "task": "general_chat",
        "loaded_plugins": [{"plugin_key": "jira", "plugin_version": "1.0.0", "connection_uuid": "jira",
                            "tools": [{"key": "jira_activity_summary"}]}],
    }
    extensions = prepare_agent_extensions(command, tmp_path)
    extensions.apply_policy({"route": "direct_execute"}, extensions.tools)
    ledger = extensions.extensions[0].store
    source = {
        "ok": True, "since": "2026-10-15T00:00:00Z", "until": "2026-10-16T00:00:00Z",
        "possibly_truncated": partial,
        "items": [{"key": f"SL-{index}", "assignee": "Alice", "assignee_identity": "alice",
                   "updated": "2026-10-15T08:00:00Z", "resolution_date": "2026-10-15T08:00:00Z"}
                  for index in range(count)],
    }
    projected = {**source, "_evidence": support.activity_evidence("jira", source, "jira")}
    ledger.record("jira", "jira", "1.0.0", "jira_activity_summary", {}, projected)
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
    assert extensions.completion_checks("报告已完成")[0]["code"] == "tool_evidence_unread"
    restored = prepare_agent_extensions(command, tmp_path)
    restored.apply_policy({"route": "direct_execute"}, restored.tools)
    restored.tools[0].invoke({"offset": 1, "limit": 1})
    assert restored.evidence()["review_completeness"] == "complete"
    assert restored.completion_checks("报告已完成") == []


def test_new_source_snapshot_invalidates_previously_consumed_pages(tmp_path):
    _, extensions, source = report(tmp_path)
    extensions.tools[0].invoke({})
    assert extensions.completion_checks("报告已完成") == []
    source["items"][0]["summary"] = "A corrected source record"
    projected = {**source, "_evidence": support.activity_evidence("jira", source, "jira")}
    extensions.extensions[0].store.record("jira", "jira", "1.0.0", "jira_activity_summary", {}, projected)
    assert extensions.completion_checks("报告已完成")[0]["code"] == "tool_evidence_unread"


def test_evidence_continuation_fills_the_first_gap_and_stops_at_complete(tmp_path):
    """A short page and speculative offsets cannot force a second full traversal."""

    _, extensions, _ = report(tmp_path, count=8)
    read = extensions.tools[0]
    first = json.loads(read.invoke({"offset": 0, "limit": 2}))
    later = json.loads(read.invoke({"offset": 3, "limit": 5}))
    assert first["next_offset"] == 2
    assert later["next_page"]["arguments"]["offset"] == 2
    gap = json.loads(read.invoke(later["next_page"]["arguments"]))
    assert len(gap["records"]) == 1
    assert gap["read_progress"]["complete"] is True
    assert gap["next_offset"] is None
    assert gap["next_page"] is None
    complete = json.loads(read.invoke({}))
    assert complete["records"] == []
    assert complete["read_progress"]["complete"] is True


def test_default_evidence_read_continues_from_actual_bounded_page(tmp_path, monkeypatch):
    """Omitting offset selects the first unread range, including after resume."""

    command, extensions, _ = report(tmp_path, count=8)
    monkeypatch.setattr(tool_evidence, "MAX_EVIDENCE_PAGE_CHARS", 6000)
    first = json.loads(extensions.tools[0].invoke({"limit": 100}))
    assert 0 < len(first["records"]) < 8
    restored = prepare_agent_extensions(command, tmp_path)
    second = json.loads(restored.tools[0].invoke({"limit": 100}))
    assert {r["id"] for r in first["records"]}.isdisjoint(r["id"] for r in second["records"])
    assert second["read_progress"]["complete"] or (
        second["read_progress"]["next_offset"] > first["read_progress"]["next_offset"]
    )


@pytest.mark.parametrize("partial", [False, True])
@pytest.mark.parametrize("answer", [
    "Alice completed nothing.",
    "<table><tr><td>Alice</td><td>no activity</td></tr></table>",
])
def test_report_wording_is_owned_by_the_model_not_host_keyword_checks(tmp_path, partial, answer):
    """Host checks depend on evidence progress, not semantic claims or negations."""

    _, extensions, _ = report(tmp_path, partial=partial)
    assert [issue["code"] for issue in extensions.completion_checks(answer)] == ["tool_evidence_unread"]
    extensions.tools[0].invoke({})
    assert extensions.completion_checks(answer) == []


def test_report_repairs_can_revalidate_the_same_path_without_blocking_upload_capability():
    from lensnode.agent_runtime.capabilities import CapabilityBoundaryMiddleware

    boundary = CapabilityBoundaryMiddleware(required_capabilities=["artifact_delivery"])
    request = SimpleNamespace(tool=SimpleNamespace(name="save_deliverable"), tool_call={
        "name": "save_deliverable", "id": "save", "args": {"path": "report.html"},
    })
    calls = []

    def reject(_request):
        calls.append("rejected before upload")
        return ToolMessage(name="save_deliverable", tool_call_id="save", status="error",
                           content='{"ok":false,"error":"COMPLETION_CHECK_FAILED"}')

    for _ in range(3):
        assert json.loads(boundary.wrap_tool_call(request, reject).content)["error"] == "COMPLETION_CHECK_FAILED"
    assert len(calls) == 3
    assert not boundary.successful_capabilities
    accepted = boundary.wrap_tool_call(request, lambda _request: ToolMessage(
        name="save_deliverable", tool_call_id="save", content='{"ok":true,"uuid":"file"}',
    ))
    assert json.loads(accepted.content)["ok"] is True
    assert "artifact_delivery" in boundary.successful_capabilities


def test_empty_evidence_page_counts_as_reviewed(tmp_path):
    _, extensions, _ = report(tmp_path, count=0)
    assert extensions.evidence()["review_completeness"] == "incomplete"
    extensions.tools[0].invoke({})
    assert extensions.evidence()["review_completeness"] == "complete"


@pytest.mark.parametrize("filename,content", [
    ("report.html", "<p>Alice completed nothing.</p>"),
    ("report.xhtml", "<p>Alice has low productivity.</p>"),
    ("report.json", '{"conclusion":"Alice completed nothing."}'),
])
def test_unread_evidence_is_rejected_before_any_upload(tmp_path, monkeypatch, filename, content):
    from lensnode.agent_tools import _build_deliverable_stager

    command, extensions, _ = report(tmp_path, partial=True)
    command["_validate_deliverable"] = extensions.completion_checks
    command["_deliverables"] = {filename: "old-output"}
    (tmp_path / filename).write_text(content)

    def no_network(*args, **kwargs):
        raise AssertionError("Unread evidence must never reach the upload service")

    monkeypatch.setattr("lensnode.agent_tools.httpx.Client", no_network)
    tool = _build_deliverable_stager(command, SimpleNamespace(root=tmp_path),
                                       SimpleNamespace(deliverable_max_bytes=10000), None)
    result = json.loads(tool(filename))
    assert result["ok"] is False
    assert result["error"] == "COMPLETION_CHECK_FAILED"
    assert command["_deliverables"] == {}


def test_activity_evidence_tool_is_marked_as_inline_bounded_result(tmp_path):
    _, extensions, _ = report(tmp_path)
    assert extensions.tools[0].metadata["bounded_inline_result"] is True


def test_large_ledger_metadata_does_not_reduce_every_page_to_one_record(tmp_path, monkeypatch):
    _, extensions, _ = report(tmp_path, count=122, partial=True)
    ledger = extensions.extensions[0].store
    evidence = ledger.material()
    evidence["sources"][0]["coverage"]["details"] = "coverage" * 10000
    monkeypatch.setattr(ledger, "material", lambda *args: evidence)
    offset = 0
    pages = []
    while offset is not None:
        raw = extensions.tools[0].invoke({"offset": offset, "limit": 100})
        assert len(raw) <= tool_evidence.MAX_EVIDENCE_PAGE_CHARS
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
    ledger = extensions.extensions[0].store
    evidence = ledger.material()
    monkeypatch.setattr(ledger, "material", lambda *args: evidence)
    evidence["records"][0]["title"] = "x" * tool_evidence.MAX_EVIDENCE_PAGE_CHARS
    rejected = json.loads(extensions.tools[0].invoke({}))
    assert rejected["error"] == "TOOL_EVIDENCE_PAGE_TOO_LARGE"
    assert ledger.read_progress(evidence)["complete"] is False
    evidence["records"][0]["title"] = ""
    raw = extensions.tools[0].invoke({})
    monkeypatch.setattr(tool_evidence, "MAX_EVIDENCE_PAGE_CHARS", len(raw) - 1)
    next(ledger.root.rglob("progress.json")).unlink()
    rejected = json.loads(extensions.tools[0].invoke({}))
    if rejected["ok"]:
        assert len(support._json(rejected)) <= tool_evidence.MAX_EVIDENCE_PAGE_CHARS
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
    assert len(output.content) <= tool_evidence.MAX_EVIDENCE_PAGE_CHARS + 256
    assert payload["read_progress"]["complete"] is False
    assert payload["next_offset"] == len(payload["records"])


def test_unread_pages_are_partial_without_configured_decision_model(tmp_path):
    _, extensions, _ = report(tmp_path)
    evidence = extensions.evidence()
    evidence["completion_issues"] = extensions.completion_checks("A report.")
    assert evidence["completion_issues"][0]["code"] == "tool_evidence_unread"
    outcome, detail = _finalize_runtime_outcome(
        capability_middleware=None, evidence_requirement="tool_result", required_capabilities=[],
        truncated=False, stop_reason=None, execution_gate_enabled=False, runtime_evidence=evidence,
    )
    assert outcome == "partial"
    assert detail["completion_issues"][0]["code"] == "tool_evidence_unread"


def test_host_check_gets_one_bounded_repair_without_decision_gate():
    pending = [{"code": "tool_evidence_unread", "recovery": "Call read_tool_evidence offset=1."}]
    gate = EvidenceGateMiddleware(SimpleNamespace(has_evidence_gates=lambda: False), "report",
                                  completion_check=lambda answer: pending)
    state = {"messages": [AIMessage(content="Done")]}
    assert gate.after_model(state, None)["jump_to"] == "model"
    assert gate.after_model(state, None) is None
    pending.clear()
    assert gate.after_model(state, None) is None


def test_convergence_trigger_survives_unread_report_failure():
    _, detail = _finalize_runtime_outcome(
        capability_middleware=None, evidence_requirement="artifact", required_capabilities=[],
        truncated=False, stop_reason=None, runtime_evidence={
            "convergence_exhausted": True, "review_completeness": "incomplete",
            "completion_issues": [{"code": "tool_evidence_unread"}],
        },
    )
    assert detail["trigger"] == "evidence_convergence_limit"
    assert detail["completion_issues"] == [{"code": "tool_evidence_unread"}]


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

    checks = [{"code": "completion_check_failed"}]
    result = _enforce_completion_status(answer, {"answer_language": "zh-CN"}, checks)
    assert answer not in result
    if answer.isascii():
        assert result.startswith("The result could not be verified.")
    else:
        assert "核验未通过" in result
    assert _enforce_completion_status(answer, {}, []) == answer


@pytest.mark.parametrize("stop_reason", ["token_budget_wrapup", "evidence_convergence_limit"])
@pytest.mark.parametrize("evidence", [
    {"completion_issues": [{"code": "tool_evidence_unread"}], "review_completeness": "incomplete"},
    {"artifact_required": True},
    {"review_completeness": "incomplete"},
    {"evidence_completeness": "incomplete"},
])
def test_execution_limit_is_preserved_independently_of_coverage_or_review(stop_reason, evidence):
    outcome, detail = _finalize_runtime_outcome(
        capability_middleware=None, evidence_requirement="artifact", truncated=True,
        stop_reason=stop_reason, execution_gate_enabled=False, runtime_evidence=evidence,
    )
    assert outcome == "partial"
    assert detail["trigger"] == stop_reason
    if evidence.get("completion_issues") or evidence.get("artifact_required"):
        assert detail["error_type"] == "verification"
    else:
        assert detail["reason"] == stop_reason
        assert detail["assessment"]["execution"] == "partial"


def test_unread_evidence_failure_explains_why_the_report_was_withheld():
    from lensnode.agent_runtime.runtime import _enforce_completion_status

    result = _enforce_completion_status("报告已完成", {}, [{"code": "tool_evidence_unread"}])
    assert "来源记录未完成核对" in result
    assert "附件未发布" in result


def test_single_record_page_drops_small_optional_metadata_before_rejecting(tmp_path, monkeypatch):
    """A readable record must advance the cursor even when coverage metadata does not fit."""

    _, extensions, _ = report(tmp_path, count=1)
    ledger = extensions.extensions[0].store
    evidence = ledger.material()
    evidence["records"][0]["title"] = "x" * 21000
    evidence["sources"][0]["coverage"]["details"] = "x" * 4500
    monkeypatch.setattr(ledger, "material", lambda *args: evidence)
    raw = extensions.tools[0].invoke({})
    page = json.loads(raw)
    assert page["ok"] is True
    assert len(raw) <= tool_evidence.MAX_EVIDENCE_PAGE_CHARS
    assert page["records"] == evidence["records"]
    assert page["source_details_omitted"] is True
    assert page["read_progress"]["complete"] is True
    assert ledger.read_progress(evidence)["complete"] is True
