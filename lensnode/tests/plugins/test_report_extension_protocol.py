"""Report workflows are contributed through the installed Plugin protocol."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from lensnode.plugin_agent import prepare_agent_extensions
from lensnode.plugin_package_loader import load_plugin_support, load_runtime_contract
from lensnode.trajectory import RunTrajectory

support = load_plugin_support("activity_reports")


def test_automatic_host_delivery_is_preserved_across_plugin_context(tmp_path):
    """Sanitized Plugin context cannot drop the report's required file contract."""

    cmd = command("github", question="总结昨天每个人的工作内容")
    source = SimpleNamespace(name="github_activity_summary", metadata={"plugin_key": "github"})
    append = SimpleNamespace(name="append_file", metadata={"automatic_artifact_delivery": True})
    extensions = prepare_agent_extensions(cmd, tmp_path, skills=["Write the report in /deliverables/."],
                                          tools=[source, append])
    policy = extensions.apply_policy(extensions.preferred_route, [source, append, *extensions.tools])
    assert policy.route["evidence_requirement"] == "artifact"
    assert "artifact_delivery" in policy.route["required_capabilities"]


def command(*sources, question="Summarize yesterday's work"):
    """Represent frozen assistant bindings, including a non-summary Tool."""

    return {
        "run_uuid": "run-extension",
        "task": "general_chat",
        "question": question,
        "loaded_plugins": [
            {
                "plugin_key": source,
                "plugin_version": "1.0.0",
                "connection_uuid": f"connection-{source}",
                "protocol_version": 1,
                "tools": [{"key": f"{source}_activity_summary"}, {"key": f"{source}_get_details"}],
            }
            for source in sources
        ],
    }


def result():
    """A verified source snapshot containing no matching work."""

    return {"ok": True, "since": "2026-10-12T00:00:00Z", "until": "2026-10-13T00:00:00Z", "items": []}


def test_three_sources_share_one_workflow_and_preserve_unrelated_tools(tmp_path):
    cmd = command("github", "gitlab", "jira")
    extensions = prepare_agent_extensions(cmd, tmp_path)
    assert len(extensions.extensions) == 1
    assert [tool.name for tool in extensions.tools] == ["read_activity_evidence"]
    tools = [
        SimpleNamespace(name=definition["key"], metadata={"plugin_key": binding["plugin_key"]})
        for binding in cmd["loaded_plugins"]
        for definition in binding["tools"]
    ]
    unrelated = SimpleNamespace(name="query_elsewhere", metadata={"plugin_key": "other"})
    policy = extensions.apply_policy(extensions.preferred_route, [*tools, unrelated, *extensions.tools])
    assert policy.subagents_allowed is False
    assert len(policy.required_sources) == 3
    assert {tool.name for tool in extensions.filter_tools([*tools, unrelated])} == {
        "github_activity_summary",
        "gitlab_activity_summary",
        "jira_activity_summary",
        "query_elsewhere",
    }


@pytest.mark.parametrize("question", ["Explain this class", "Report a bug", "总结工作内容，仅用文字"])
def test_unrelated_or_text_only_requests_create_no_report_state(tmp_path, question):
    extensions = prepare_agent_extensions(command("github", question=question), tmp_path)
    assert not extensions.extensions
    assert not extensions.tools
    assert extensions.preferred_route is None
    assert not (tmp_path / ".sourcelens").exists()


def test_rejected_request_is_not_cached_and_can_be_corrected(tmp_path):
    cmd = command("github")
    extensions = prepare_agent_extensions(cmd, tmp_path)
    calls = []

    def execute(*args):
        calls.append(args)
        return (
            json.dumps({"ok": False, "error": "TOOL_ARGUMENTS_INVALID"})
            if len(calls) == 1
            else json.dumps({**result(), "_evidence": {"complete": True}})
        )

    binding = cmd["loaded_plugins"][0]
    invoke = extensions.wrap_executor(binding, binding["tools"][0], execute)
    runtime = SimpleNamespace(tool_call_id="call-1")
    assert json.loads(invoke("github_activity_summary", {}, runtime))["ok"] is False
    assert json.loads(invoke("github_activity_summary", {}, runtime))["ok"] is True
    assert json.loads(invoke("github_activity_summary", {}, runtime))["ok"] is True
    assert len(calls) == 2


@pytest.mark.parametrize("provider", ["github", "gitlab", "jira"])
def test_activity_progress_preserves_the_tool_parent_for_saved_and_cached_evidence(tmp_path, provider):
    """Successful source snapshots and cache hits must not degrade trace health."""

    frames = []
    trajectory = RunTrajectory("run-extension", frames.append)
    model_id = trajectory.start_call("model", "agent", call_id="model")

    def emit_event(name, detail):
        detail = trajectory.tool_event_detail(detail)
        trajectory.record(
            "step.event",
            {"name": name, **detail},
            call_id=detail.get("call_id"),
            parent_call_id=detail.get("parent_call_id"),
        )

    cmd = command(provider)
    extensions = prepare_agent_extensions(cmd, tmp_path, emit_event=emit_event)
    binding = cmd["loaded_plugins"][0]
    tool_name = f"{provider}_activity_summary"
    calls = []
    invoke = extensions.wrap_executor(
        binding,
        binding["tools"][0],
        lambda *args: calls.append(args) or json.dumps({**result(), "_evidence": {"complete": True}}),
    )
    for call_id in ("fetch", "cached"):
        trajectory.start_call("tool", tool_name, call_id=call_id, parent_call_id=model_id)
        with trajectory.tool_call_scope(call_id):
            assert json.loads(invoke(tool_name, {}, SimpleNamespace(tool_call_id=call_id)))["ok"] is True
        trajectory.finish_call(call_id, "completed")

    progress = [frame["events"][0] for frame in frames if frame["events"][0]["event_type"] == "step.event"]
    assert [event["payload"]["name"] for event in progress] == [
        "tool.activity_evidence.saved", "tool.plugin.cache_hit",
    ]
    assert [event["call_id"] for event in progress] == ["fetch", "cached"]
    assert all(event["parent_call_id"] == model_id for event in progress)
    assert all("diagnostics" not in event["payload"] for event in progress)
    identity = load_runtime_contract(provider, "1.0.0")
    for event in progress:
        assert event["payload"]["plugin_version"] == "1.0.0"
        assert event["payload"]["runtime_identity"]["runtime_sha256"] == identity.runtime_sha256
        assert event["payload"]["runtime_identity"]["support_sha256"] == identity.support_sha256
    assert trajectory.health == "healthy"
    assert len(calls) == 1


def test_identical_parallel_requests_share_a_single_source_snapshot(tmp_path):
    cmd = command("github")
    extensions = prepare_agent_extensions(cmd, tmp_path)
    calls = []
    binding = cmd["loaded_plugins"][0]
    invoke = extensions.wrap_executor(
        binding,
        binding["tools"][0],
        lambda *args: calls.append(args) or json.dumps({**result(), "_evidence": {"complete": True}}),
    )
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                lambda index: invoke("github_activity_summary", {}, SimpleNamespace(tool_call_id=str(index))), range(4)
            )
        )
    assert all(json.loads(item)["ok"] for item in results)
    assert len(calls) == 1


def test_active_report_returns_small_receipts_and_keeps_full_partial_sources(tmp_path):
    """Fresh and cached calls direct the model to the ledger without losing source facts."""

    cmd = command("jira")
    extensions = prepare_agent_extensions(cmd, tmp_path)
    extension = extensions.extensions[0]
    extension.active = True
    payload = {
        **result(),
        "items": [{"key": f"SL-{index}", "summary": "x" * 2000, "assignee": "Alice",
                   "updated": "2026-10-12T08:00:00Z"} for index in range(80)],
        "_evidence": {"complete": False},
    }
    calls = []
    binding = cmd["loaded_plugins"][0]
    invoke = extensions.wrap_executor(
        binding, binding["tools"][0], lambda *args: calls.append(args) or json.dumps(payload)
    )
    receipts = [json.loads(invoke("jira_activity_summary", {}, SimpleNamespace(tool_call_id="source")))
                for _ in range(2)]
    assert receipts[0] == receipts[1]
    assert len(json.dumps(receipts[0])) < 2000
    assert receipts[0]["next_page"] == {"tool": "read_activity_evidence", "arguments": {"offset": 0}}
    assert receipts[0]["total_records"] == 80
    assert receipts[0]["_evidence"]["complete"] is False
    assert "items" not in receipts[0]
    assert len(calls) == 1
    assert json.loads(extension.ledger.cached("jira", "connection-jira", "1.0.0", "jira_activity_summary", {})) == payload
    assert extension.evidence()["review_completeness"] == "incomplete"


def test_followup_preserves_named_source_scope_and_migrates_old_run_evidence(tmp_path):
    cmd = command("github", "jira", question="为什么没有生成 html 呢")
    cmd["history"] = [{"role": "user", "content": "总结昨天 GitHub 的工作内容"}]
    legacy = tmp_path / ".sourcelens" / "activity-evidence" / hashlib.sha256(cmd["run_uuid"].encode()).hexdigest()
    old = support.ActivityLedger(legacy)
    old.record("github", "connection-github", "1.0.0", "github_activity_summary", {}, result())
    extensions = prepare_agent_extensions(cmd, tmp_path, skills=["Write the file to /deliverables/report.html."])
    assert not legacy.exists()
    assert len(extensions.extensions) == 1
    tools = [
        SimpleNamespace(name="append_file", metadata={"automatic_artifact_delivery": True}),
        *extensions.tools,
        SimpleNamespace(name="github_activity_summary", metadata={"plugin_key": "github"}),
    ]
    policy = extensions.apply_policy(
        {"intent": "clarification", "route": "direct_answer", "evidence_requirement": "none"}, tools
    )
    assert policy.route["evidence_requirement"] == "artifact"
    assert policy.required_sources == ("plugin:github_activity_summary",)
    binding = cmd["loaded_plugins"][0]
    invoke = extensions.wrap_executor(binding, binding["tools"][0], lambda *args: pytest.fail("refetched old evidence"))
    restored = json.loads(invoke("github_activity_summary", {}, SimpleNamespace(tool_call_id="resume")))
    assert restored["next_page"]["tool"] == "read_activity_evidence"
    assert restored["_evidence"]["complete"] is True
    assert extensions.evidence()["evidence_completeness"] == "complete"


def test_source_projector_maps_coverage_after_bounding_without_core_domain_knowledge():
    contract = load_runtime_contract("github", "1.0.0")
    payload = {**result(), "items": [{"title": "x" * 200}] * 20}
    projected = contract.project_tool_result("github_activity_summary", payload, 800)
    assert len(json.dumps(projected, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()) <= 800
    assert projected["_evidence"]["complete"] is False
    assert len(payload["items"]) == 20
    assert contract.project_tool_result("github_repository_get", {"ok": True}, 800) == {"ok": True}


def test_explicit_protocol_coverage_gap_survives_projection_and_saved_evidence(tmp_path):
    """An upstream evidence limitation cannot be promoted to complete by a projector."""

    contract = load_runtime_contract("github", "1.0.0")
    payload = {**result(), "_evidence": {"complete": False}}
    projected = contract.project_tool_result("github_activity_summary", payload, 900_000)
    assert projected["_evidence"]["complete"] is False
    ledger = support.ActivityLedger(tmp_path / "private")
    ledger.record("github", "connection", "1.0.0", "github_activity_summary", {}, projected)
    assert ledger.report({"github_activity_summary"})["complete"] is False


def test_coverage_envelope_stays_inside_the_budget_when_true_becomes_false():
    """The final false marker is one byte longer and must remain deliverable."""

    contract = load_runtime_contract("github", "1.0.0")
    projected = contract.project_tool_result(
        "github_activity_summary",
        {"ok": True, "items": [{"title": "x" * 10}] * 10},
        100,
    )
    assert len(json.dumps(projected, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()) <= 100
    assert projected["_evidence"]["complete"] is False
