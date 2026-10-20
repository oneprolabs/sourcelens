"""Generic evidence survives compaction and resume without report policies."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from lensnode.plugin_agent import plugin_state_root, prepare_agent_extensions
from lensnode.plugin_package_loader import load_runtime_contract
from lensnode.tool_evidence import ToolEvidenceStore


def setup_source(tmp_path, *, run="run", connection="connection", provider="jira"):
    """Prepare a real source contract and its generic host evidence adapter."""

    tool_key = f"{provider}_activity_summary"
    binding = {
        "plugin_key": provider,
        "plugin_version": "1.0.0",
        "connection_uuid": connection,
        "tools": [{"key": tool_key, "side_effect": "none"}],
    }
    command = {"run_uuid": run, "loaded_plugins": [binding]}
    extensions = prepare_agent_extensions(command, tmp_path)
    contract = load_runtime_contract(provider, "1.0.0")
    source = {
        "ok": True,
        "since": "2026-10-15T00:00:00Z",
        "until": "2026-10-16T00:00:00Z",
        "items": [
            {"key": "SL-1", "assignee": "Alice", "assignee_identity": "alice", "updated": "2026-10-15T08:00:00Z"}
        ],
    }
    calls = []

    def execute(key, arguments, runtime):
        calls.append(arguments)
        return json.dumps(contract.project_tool_result(key, source, 900_000))

    execute._lens_runtime_identity = contract.runtime_identity
    invoke = extensions.wrap_executor(binding, binding["tools"][0], execute)
    return command, extensions, source, calls, invoke


def test_exact_concurrent_requests_and_resume_reuse_one_authorized_snapshot(tmp_path):
    command, extensions, source, calls, invoke = setup_source(tmp_path)
    runtime = SimpleNamespace(tool_call_id="source")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: json.loads(invoke("jira_activity_summary", {}, runtime)), range(2)))
    assert len(calls) == 1
    assert results[0] == results[1]
    assert results[0]["record_count"] == 1
    assert "records" not in results[0]
    saved = next(iter(extensions.extensions[0].store.entries.values()))["result"]
    assert saved["items"] == source["items"]
    assert saved["_evidence"]["records"][0]["id"] == "SL-1"
    restored = prepare_agent_extensions(command, tmp_path)
    binding = command["loaded_plugins"][0]

    def must_not_refetch(*args):
        pytest.fail("Resume must reuse the saved source snapshot")

    cached = restored.wrap_executor(binding, binding["tools"][0], must_not_refetch)
    assert json.loads(cached("jira_activity_summary", {}, runtime)) == results[0]
    assert restored.completion_checks("Ready")[0]["code"] == "tool_evidence_unread"
    page = json.loads(restored.tools[0].invoke({}))
    assert len(page["records"]) == 1
    assert restored.completion_checks("Ready") == []


@pytest.mark.parametrize("change", ["connection", "run", "arguments"])
def test_snapshots_do_not_cross_binding_run_or_arguments(tmp_path, change):
    _, _, _, calls, invoke = setup_source(tmp_path)
    runtime = SimpleNamespace(tool_call_id="source")
    invoke("jira_activity_summary", {}, runtime)
    if change == "arguments":
        invoke("jira_activity_summary", {"projects": ["other"]}, runtime)
        assert len(calls) == 2
    else:
        _, _, _, other_calls, other = setup_source(tmp_path, **{change: "other"})
        other("jira_activity_summary", {}, runtime)
        assert len(other_calls) == 1


def test_rejected_request_is_not_cached_and_success_can_be_retried(tmp_path):
    command, extensions, source, _, _ = setup_source(tmp_path)
    binding = command["loaded_plugins"][0]
    count = []
    contract = load_runtime_contract("jira", "1.0.0")

    def execute(key, args, runtime):
        count.append(args)
        return json.dumps(
            {"ok": False, "error": "TOOL_ARGUMENTS_INVALID"}
            if len(count) == 1
            else contract.project_tool_result(key, source, 900_000)
        )

    invoke = extensions.wrap_executor(binding, binding["tools"][0], execute)
    runtime = SimpleNamespace(tool_call_id="source")
    assert json.loads(invoke("jira_activity_summary", {}, runtime))["ok"] is False
    assert not extensions.extensions[0].store.has_evidence()
    assert json.loads(invoke("jira_activity_summary", {}, runtime))["evidence_saved"] is True
    assert len(count) == 2


def test_old_raw_ledger_migrates_without_refetch_and_unbound_snapshots_stay_private(tmp_path):
    command, _, source, _, _ = setup_source(tmp_path)
    legacy = plugin_state_root(tmp_path, "run") / "activity-report"
    store = ToolEvidenceStore(legacy)
    store.record("jira", "connection", "1.0.0", "jira_activity_summary", {}, source)
    store.record("jira", "unbound", "1.0.0", "jira_activity_summary", {}, source)
    restored = prepare_agent_extensions(command, tmp_path)
    assert not legacy.exists()
    material = restored.extensions[0].store.material()
    assert len(material["records"]) == 1
    assert material["sources"][0]["connection"] == "connection"
    assert restored.evidence()["review_completeness"] == "incomplete"
    assert json.loads(restored.tools[0].invoke({}))["read_progress"]["complete"] is True


def test_partial_source_does_not_change_another_sources_coverage(tmp_path):
    command, extensions, source, _, invoke = setup_source(tmp_path)
    source["possibly_truncated"] = True
    invoke("jira_activity_summary", {}, SimpleNamespace(tool_call_id="source"))
    store = extensions.extensions[0].store
    second = {"ok": True, "_evidence": {"complete": True, "records": [{"fact": "Second source fact"}]}}
    store.record("custom", "second", "1.0.0", "read_changes", {}, second)
    material = store.material()
    assert material["complete"] is False
    assert [item["coverage"]["complete"] for item in material["sources"]] == [False, True]


def test_snapshot_files_are_private_and_symlink_state_is_rejected(tmp_path):
    _, extensions, _, _, invoke = setup_source(tmp_path)
    invoke("jira_activity_summary", {}, SimpleNamespace(tool_call_id="source"))
    store = extensions.extensions[0].store
    assert store.root.stat().st_mode & 0o777 == 0o700
    assert next(store.root.glob("*.json")).stat().st_mode & 0o777 == 0o600
    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "link"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="root"):
        ToolEvidenceStore(link)


@pytest.mark.parametrize("asynchronous", [False, True])
def test_real_graph_reads_saved_evidence_and_writes_a_model_authored_file(tmp_path, asynchronous):
    from langchain.agents import create_agent
    from langchain.tools import tool
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage, HumanMessage

    _, extensions, _, calls, invoke = setup_source(tmp_path)

    @tool
    def source_changes() -> str:
        """Fetch the authorized source facts."""

        return invoke("jira_activity_summary", {}, SimpleNamespace(tool_call_id="source"))

    @tool
    def write_result(content: str) -> str:
        """Write the authored result after reading source facts."""

        assert extensions.completion_checks(content) == []
        (tmp_path / "result.html").write_text(content)
        return "Saved"

    class Model(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            """Allow deterministic responses inside the real framework graph."""

            return self

    content = "<article>Alice: SL-1 was updated; no verified update actor.</article>"
    model = Model(
        responses=[
            AIMessage(content="", tool_calls=[{"id": "source", "name": "source_changes", "args": {}}]),
            AIMessage(content="", tool_calls=[{"id": "read", "name": "read_tool_evidence", "args": {}}]),
            AIMessage(content="", tool_calls=[{"id": "write", "name": "write_result", "args": {"content": content}}]),
            AIMessage(content="Report saved"),
        ]
    )
    graph = create_agent(model, tools=[source_changes, *extensions.tools, write_result])
    state = {"messages": [HumanMessage(content="Summarize the changes")]}
    result = asyncio.run(graph.ainvoke(state)) if asynchronous else graph.invoke(state)
    assert result["messages"][-1].content == "Report saved"
    assert (tmp_path / "result.html").read_text() == content
    assert len(calls) == 1


def test_side_effect_tools_bypass_snapshot_reuse(tmp_path):
    command, extensions, _, calls, _ = setup_source(tmp_path)
    binding = command["loaded_plugins"][0]

    def execute(*args):
        calls.append(args)
        return '{"ok": true}'

    invoke = extensions.wrap_executor(binding, {**binding["tools"][0], "side_effect": "write"}, execute)
    invoke("jira_activity_summary", {}, SimpleNamespace(tool_call_id="one"))
    invoke("jira_activity_summary", {}, SimpleNamespace(tool_call_id="two"))
    assert len(calls) == 2
    assert not extensions.extensions[0].store.has_evidence()


def test_upstream_incompleteness_survives_an_optimistic_projector(tmp_path, monkeypatch):
    command, extensions, _, _, _ = setup_source(tmp_path)
    binding = command["loaded_plugins"][0]
    monkeypatch.setattr(
        "lensnode.tool_evidence.load_runtime_contract",
        lambda *args: SimpleNamespace(project_tool_evidence=lambda *args: {"complete": True, "records": []}),
    )
    invoke = extensions.wrap_executor(
        binding, binding["tools"][0], lambda *args: '{"ok": true, "_evidence": {"complete": false}}'
    )
    result = json.loads(invoke("jira_activity_summary", {}, SimpleNamespace(tool_call_id="one")))
    assert result["_evidence"]["complete"] is False
    assert extensions.extensions[0].store.material()["complete"] is False


@pytest.mark.parametrize("projection", [None, {"records": {}, "complete": True}, {"records": [], "complete": 1}])
def test_malformed_projector_cannot_save_evidence(tmp_path, monkeypatch, projection):
    from lensnode.plugin_package_loader import PluginPackageLoadError

    command, extensions, _, _, _ = setup_source(tmp_path)
    binding = command["loaded_plugins"][0]
    monkeypatch.setattr(
        "lensnode.tool_evidence.load_runtime_contract",
        lambda *args: SimpleNamespace(project_tool_evidence=lambda *args: projection),
    )
    invoke = extensions.wrap_executor(binding, binding["tools"][0], lambda *args: '{"ok": true}')
    with pytest.raises(PluginPackageLoadError):
        invoke("jira_activity_summary", {}, SimpleNamespace(tool_call_id="one"))
    assert not extensions.extensions[0].store.has_evidence()


def test_private_evidence_is_removed_by_existing_checkpoint_cleanup(tmp_path, monkeypatch):
    from lensnode.checkpoint import cleanup_run_checkpoint

    _, extensions, _, _, invoke = setup_source(tmp_path)
    invoke("jira_activity_summary", {}, SimpleNamespace(tool_call_id="source"))
    root = extensions.extensions[0].store.root
    assert root.exists()
    monkeypatch.setenv("LENSNODE_CHECKPOINT_ENABLED", "false")
    cleanup_run_checkpoint("run", tmp_path)
    assert not root.exists()


def test_review_state_rejects_a_root_replaced_with_a_symlink(tmp_path):
    _, extensions, _, _, invoke = setup_source(tmp_path)
    invoke("jira_activity_summary", {}, SimpleNamespace(tool_call_id="source"))
    store = extensions.extensions[0].store
    material = store.material()
    original = store.root.with_name("original")
    store.root.rename(original)
    store.root.symlink_to(original, target_is_directory=True)
    with pytest.raises(ValueError, match="review state"):
        store.read_progress(material, (0, 1))


def test_resumed_evidence_does_not_reuse_the_rejected_drafts_verdict():
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
    assert _post_run_decision_gates(state, "Corrected report")["answer_supported"] == "supported"
    assert len(calls) == 1


@pytest.mark.parametrize("coverage", ["missing", "complete", "partial"])
@pytest.mark.parametrize("host_incomplete", [False, True])
@pytest.mark.parametrize("stop_reason", [None, "token_budget_wrapup"])
def test_terminal_runtime_preserves_business_report_and_deliverables(
    tmp_path, monkeypatch, coverage, host_incomplete, stop_reason
):
    from datetime import datetime, timezone

    from lensnode.agent_runtime import runtime as runtime_module

    evidence = {"ok": True, "since": "2026-10-15T00:00:00Z", "until": "2026-10-16T00:00:00Z", "items": []}
    evidence["possibly_truncated"] = coverage == "partial"
    command = {
        "question": "总结 10/8，每个人的工作内容",
        "task": "general_chat",
        "run_uuid": "run-report",
        "loaded_plugins": [
            {
                "plugin_key": "jira",
                "plugin_version": "1.0.0",
                "connection_uuid": "connection",
                "tools": [{"key": "jira_activity_summary"}],
            }
        ],
        "_deliverables": {"business-report.html": "business-candidate"},
    }
    from lensnode.plugin_agent import prepare_agent_extensions

    extensions = prepare_agent_extensions(command, tmp_path)
    extensions.apply_policy({"execution_mode": "plan_execute"}, extensions.tools)
    if coverage != "missing":
        extensions.extensions[0].store.record(
            "jira",
            "connection",
            "1.0.0",
            "jira_activity_summary",
            {},
            {
                **evidence,
                "_evidence": load_runtime_contract("jira", "1.0.0").project_tool_evidence(
                    "jira_activity_summary", evidence, "connection"
                ),
            },
        )
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
    assert result["outcome"] == "completed"
    assert "reason" not in result["termination_detail"]
    if coverage == "partial" or host_incomplete:
        assert result["termination_detail"]["warnings"] == ["evidence_incomplete"]
        assert result["termination_detail"]["assessment"] == {
            "execution": "completed", "coverage": "incomplete", "review": "not_requested",
        }


def test_pages_fill_the_budget_and_targeted_reads_preserve_snapshot_progress(tmp_path):
    from lensnode.tool_evidence import MAX_EVIDENCE_PAGE_CHARS, build_tool_evidence_reader

    store = ToolEvidenceStore(tmp_path / "evidence")
    records = [{"id": str(index), "title": "x" * 1100} for index in range(50)]
    store.record(
        "custom", "connection", "1", "changes", {}, {"ok": True, "_evidence": {"complete": False, "records": records}}
    )
    reader = build_tool_evidence_reader(store)
    first = json.loads(reader.invoke({}))
    assert 18 <= len(first["records"]) <= 21
    assert len(json.dumps(first, ensure_ascii=False, separators=(",", ":"))) <= MAX_EVIDENCE_PAGE_CHARS
    first_id = first["records"][0]["evidence_id"]
    before = first["read_progress"]
    targeted = json.loads(reader.invoke({"record_ids": [first_id]}))
    assert [record["id"] for record in targeted["records"]] == ["0"]
    assert targeted["read_progress"] == before
    unknown = json.loads(reader.invoke({"record_ids": ["not-a-saved-id"]}))
    assert unknown["error"] == "TOOL_EVIDENCE_IDS_UNKNOWN"
    pages = [first]
    while pages[-1]["next_page"]:
        pages.append(json.loads(reader.invoke(pages[-1]["next_page"]["arguments"])))
    assert len(pages) == 3
    assert [record["id"] for page in pages for record in page["records"]] == [str(index) for index in range(50)]
    assert pages[-1]["read_progress"]["complete"] is True
    assert store.material()["complete"] is False
    restored = ToolEvidenceStore(store.root)
    assert restored.material()["records"][0]["evidence_id"] == first_id
    assert json.loads(build_tool_evidence_reader(restored).invoke({"record_ids": [first_id]}))["read_progress"][
        "complete"
    ]


def test_targeted_reads_cannot_certify_unread_records(tmp_path):
    from lensnode.tool_evidence import build_tool_evidence_reader

    store = ToolEvidenceStore(tmp_path / "evidence")
    store.record(
        "custom",
        "connection",
        "1",
        "changes",
        {},
        {"ok": True, "_evidence": {"complete": True, "records": [{"id": "a"}, {"id": "b"}, {"id": "c"}]}},
    )
    evidence_id = store.material()["records"][2]["evidence_id"]
    result = json.loads(build_tool_evidence_reader(store).invoke({"record_ids": [evidence_id]}))
    assert result["read_progress"]["complete"] is False
    assert result["read_progress"]["next_offset"] == 0
