import hashlib
import json
import ssl
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from langchain_core.messages import AIMessage, ToolMessage

from lensnode import agent_runtime
from lensnode.agent_runtime.deliverable import AppendWriteDeduplicationMiddleware, AutomaticDeliverables
from lensnode.agent_tools import (
    _build_append_file_tool,
    _build_deliverable_stager,
)
from lensnode.config import LensNodeConfig
from lensnode.runtime_resources import RuntimeResources


def _config():
    """Build a minimal config pointing at a fake upload URL."""

    return LensNodeConfig(
        name="test-node",
        token="test-token",
        control_ws_url="ws://backend/ws/",
        ai_gateway_url="http://b/api/lens/lensnode/ai-gateway/",
        deliverable_upload_url="http://b/api/lens/lensnode/deliverables/",
        tls_skip_verify=True,
        tls_ca_file=None,
        deliverable_max_bytes=50 * 1024 * 1024,
        workspace_path="/workspace",
        protocol_version="v1",
        agent_version="0.1.0",
        heartbeat_interval_s=15,
        request_timeout_s=30,
        run_idle_timeout_s=180,
        drain_timeout_s=240,
        max_concurrent_runs=1,
        summary_trigger_tokens=48000,
        summary_keep_tokens=16000,
        offload_tool_tokens=5000,
        offload_human_tokens=None,
    )


def _resources(root):
    """Build RuntimeResources rooted at the given scratch dir."""

    return RuntimeResources(
        root=Path(root),
        skill_paths=[],
        context_skill_contents=[],
        skill_environments={},
        mcp_config_path=Path(root) / "mcp.json",
    )


def _install_transport(monkeypatch, handler, client_options=None):
    """Route agent_tools' httpx.Client through a mock transport."""

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client

    def fake_client(*args, **kwargs):
        if client_options is not None:
            client_options.update(kwargs)
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr("lensnode.agent_tools.httpx.Client", fake_client)


def test_automatic_delivery_collects_only_final_directory_and_latest_bytes(monkeypatch, tmp_path):
    """Scratch stays private and edits/deletions cannot publish stale candidates."""

    (tmp_path / "private.txt").write_text("private")
    outputs = tmp_path / "deliverables"
    outputs.mkdir()
    report = outputs / "report.html"
    report.write_text("draft")
    uploads = []

    def handler(request):
        uploads.append(request.read())
        return httpx.Response(201, json={"uuid": f"output-{len(uploads)}"})

    _install_transport(monkeypatch, handler)
    command = {"run_uuid": "run-123"}
    evidence = {}
    collector = AutomaticDeliverables(command, _resources(tmp_path), _config(), evidence=evidence)
    assert collector.collect() == []
    assert command["_deliverables"] == {"deliverables/report.html": "output-1"}
    assert collector.collect() == []
    assert len(uploads) == 1
    report.write_text("final")
    resumed = AutomaticDeliverables(command, _resources(tmp_path), _config(), evidence=evidence)
    assert resumed.collect() == []
    assert command["_deliverables"] == {"deliverables/report.html": "output-2"}
    assert b"final" in uploads[-1]
    assert all(b"private" not in body for body in uploads)
    report.unlink()
    assert resumed.collect() == []
    assert command["_deliverables"] == {}


def test_automatic_delivery_validation_can_recover_and_recheck_discards_batch(monkeypatch, tmp_path):
    """Rejected content never uploads, and rejected batches must be regenerated."""

    outputs = tmp_path / "deliverables"
    outputs.mkdir()
    report = outputs / "report.md"
    report.write_text("unsupported")
    command = {"run_uuid": "run-123", "_validate_deliverable": lambda text: (
        [{"code": "unsupported", "recovery": "Remove unsupported claims."}] if text == "unsupported" else []
    )}
    uploads = []

    def handler(request):
        uploads.append(request.read())
        return httpx.Response(201, json={"uuid": f"output-{len(uploads)}"})

    _install_transport(monkeypatch, handler)
    collector = AutomaticDeliverables(command, _resources(tmp_path), _config())
    assert collector.collect()[0]["code"] == "unsupported"
    assert not uploads
    report.write_text("supported")
    assert collector.collect() == []
    collector.discard()
    assert command["_deliverables"] == {}
    assert not report.exists()
    assert collector.collect() == []
    assert len(uploads) == 1
    (outputs / "final.pdf").write_bytes(b"%PDF-generated-by-shell")
    assert collector.collect() == []
    assert command["_deliverables"] == {"deliverables/final.pdf": "output-2"}


def test_automatic_delivery_rejects_symlinks_and_upload_failures(monkeypatch, tmp_path):
    """Output collection never follows links, even to other private scratch files."""

    outputs = tmp_path / "deliverables"
    outputs.mkdir()
    (tmp_path / "secret.txt").write_text("private")
    (outputs / "link.txt").symlink_to(tmp_path / "secret.txt")
    uploads = []

    def handler(request):
        uploads.append(request.read())
        return httpx.Response(503)

    _install_transport(monkeypatch, handler)
    command = {"run_uuid": "run-123"}
    collector = AutomaticDeliverables(command, _resources(tmp_path), _config())
    assert collector.collect()
    assert uploads == []
    (outputs / "link.txt").unlink()
    (outputs / "report.md").write_text("report")
    assert collector.collect()[0]["code"] == "artifact_delivery_failed"
    assert command["_deliverables"] == {}


def test_automatic_delivery_revalidates_unchanged_bytes_against_new_evidence(monkeypatch, tmp_path):
    """A cached upload cannot bypass changed evidence completeness checks."""

    outputs = tmp_path / "deliverables"
    outputs.mkdir()
    (outputs / "report.md").write_text("report")
    command = {"run_uuid": "run-123"}
    _install_transport(monkeypatch, lambda request: httpx.Response(201, json={"uuid": "output-1"}))
    collector = AutomaticDeliverables(command, _resources(tmp_path), _config())
    assert collector.collect() == []
    command["_validate_deliverable"] = lambda text: [{"code": "activity_evidence_unread"}]
    assert collector.collect() == [{"code": "activity_evidence_unread"}]
    assert command["_deliverables"] == {}


def test_automatic_delivery_recheck_allows_append_chunks_to_be_regenerated(tmp_path):
    """Discarding an output batch also discards its append idempotency receipts."""

    collector = AutomaticDeliverables({}, _resources(tmp_path), _config())
    append = _build_append_file_tool(_resources(tmp_path), None)
    args = {"path": "/deliverables/report.md", "chunk_id": "section-001", "content": "draft"}
    assert json.loads(append.invoke(args))["ok"]
    collector.discard()
    args["content"] = "corrected"
    assert json.loads(append.invoke(args))["ok"]
    assert (tmp_path / "deliverables" / "report.md").read_text() == "corrected"


def test_model_tools_expose_automatic_delivery_without_save_tool(tmp_path):
    """Both runtime modes advertise host delivery without a separate save tool."""

    from lensnode.agent_tools import build_agent_tools, build_general_chat_tools
    from lensnode.agent_runtime.routing import _available_route_capabilities

    for builder in (build_agent_tools, build_general_chat_tools):
        tools = builder({}, _resources(tmp_path), _config())
        assert "save_deliverable" not in {tool.name for tool in tools}
        assert "artifact_delivery" in _available_route_capabilities(tools, has_bound_skills=False)


def test_bundled_script_output_is_collected_without_changing_skill_cwd(monkeypatch, tmp_path):
    """Document scripts can target the Run output directory through a structured arg."""

    from lensnode.agent_tools import build_general_chat_tools

    skill = tmp_path / "skills" / "document"
    scripts = skill / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "generate.py").write_text(
        "import os, sys\nfrom pathlib import Path\n"
        "assert Path.cwd().name == 'document'\n"
        "assert Path(sys.argv[1]).parent == Path(os.environ['SOURCELENS_DELIVERABLE_DIR'])\n"
        "Path(sys.argv[1]).write_bytes(b'%PDF-generated-by-skill')\nprint('generated')\n"
    )
    resources = _resources(tmp_path)
    resources.skill_paths.append(skill)
    command = {"run_uuid": "run-123"}
    collector = AutomaticDeliverables(command, resources, _config())
    tools = build_general_chat_tools(command, resources, _config())
    script = next(tool for tool in tools if tool.name == "run_skill_script")
    result = json.loads(script.invoke({"skill": "document", "script": "scripts/generate.py",
                                       "args": ["{{SOURCELENS_DELIVERABLE_DIR}}/report.pdf"]}))
    assert result["ok"]
    _install_transport(monkeypatch, lambda request: httpx.Response(201, json={"uuid": "output-1"}))
    assert collector.collect() == []
    assert command["_deliverables"] == {"deliverables/report.pdf": "output-1"}


def test_failed_automatic_delivery_has_bounded_model_corrections(tmp_path):
    """Missing files cannot restart the model indefinitely, even without a Decision."""

    collector = AutomaticDeliverables({}, _resources(tmp_path), _config())
    state = SimpleNamespace(command=collector.command, runtime_evidence={"artifact_required": True},
                            question="Create a report", deliverable_collector=collector)
    gate = agent_runtime._build_evidence_middleware(state)
    messages = {"messages": [AIMessage(content="Attached.")]}
    assert gate.after_model(messages, None)["jump_to"] == "model"
    assert gate.after_model(messages, None)["jump_to"] == "model"
    assert gate.after_model(messages, None) is None
    assert state.runtime_evidence["delivery_issues"][0]["code"] == "artifact_delivery_failed"


def test_normalized_attachment_notice_preserves_graph_review_coverage():
    """Host notice replacement must retain offloaded evidence at terminal review."""

    from lensnode.agent_runtime.decision_policy import ControlDecisionPolicy, GateDecisionPolicy
    from lensnode.agent_runtime.evidence_gate import EvidenceGateMiddleware

    class Runner:
        def evaluate_choice(self, *args, **kwargs):
            return "supported"

    policy = GateDecisionPolicy(ControlDecisionPolicy(), Runner(), {"answer_supported": {"max_state_chars": 80000}})
    evidence = {"_deliverables": {"deliverables/report.html": "output-1"},
                "evidence_completeness": "complete", "review_completeness": "complete"}
    gate = EvidenceGateMiddleware(policy, "Create a report", evidence=evidence, prepare_deliverables=lambda: [],
                                  completion_check=lambda answer: (
                                      [{"code": "activity_claim_unsupported"}] if "zero work" in answer else []
                                  ))
    graph_state = {"messages": [
        ToolMessage(name="read_file", tool_call_id="read", content=(
            "Tool result too large, the result was saved in the filesystem. Preview: Alice updated PR 1."
        )),
        AIMessage(content="Alice did zero work."),
    ]}
    update = gate.after_model(graph_state, None)
    notice = update["messages"][0].content
    state = SimpleNamespace(decision_policy=policy, runtime_evidence=evidence, evidence_middleware=gate,
                            question="Create a report")
    verdict = agent_runtime._post_run_decision_gates(state, notice)
    assert verdict["review_completeness"] == "incomplete"
    assert verdict["answer_supported"] == "inconclusive"
    assert evidence["_deliverables"] == {"deliverables/report.html": "output-1"}


@pytest.mark.parametrize("hard_cap", [False, True])
@pytest.mark.parametrize("with_guidance", [False, True])
def test_automatic_delivery_survives_budget_reserve_without_save_call(monkeypatch, tmp_path, hard_cap, with_guidance):
    """The host stages a pending write even when no further model call is affordable."""

    import threading
    from langchain.agents import create_agent
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.tools import tool
    from lensnode.agent_runtime.execution import _run_agent_with_turn_limit
    from lensnode.gateway_model import RunTokenBudgetExhaustedError

    event = threading.Event()
    calls = []
    evidence = {"artifact_required": True}
    collector = AutomaticDeliverables({}, _resources(tmp_path), _config(), evidence=evidence)
    _install_transport(monkeypatch, lambda request: httpx.Response(201, json={"uuid": "output-1"}))

    @tool
    def write_file() -> str:
        """Generate the final report."""

        calls.append("write")
        (tmp_path / "deliverables" / "report.html").write_text("<h1>Report</h1>")
        return "Updated file /deliverables/report.html"

    class Model(FakeMessagesListChatModel):
        def bind_tools(self, *args, **kwargs):
            return self

        def _generate(self, *args, **kwargs):
            event.set()
            calls.append("model")
            if hard_cap and (tmp_path / "deliverables" / "report.html").exists():
                raise RunTokenBudgetExhaustedError("Hard cap reached")
            return super()._generate(*args, **kwargs)

    policy = SimpleNamespace(has_evidence_gates=lambda: True,
                             post_run_checks=lambda *args: {"answer_supported": "supported"})
    state = SimpleNamespace(command=collector.command, runtime_evidence=evidence,
                            question="Create a report", deliverable_collector=collector, decision_policy=policy)
    middleware = [agent_runtime._build_evidence_middleware(state)] if with_guidance else []
    graph = create_agent(Model(responses=[
        AIMessage(content="", tool_calls=[{"id": "write", "name": "write_file", "args": {}}]),
        AIMessage(content="Report ready."),
    ]), tools=[write_file], middleware=middleware)
    def exhausted_synthesis(*args, **kwargs):
        """Reproduce a gateway cap that also prevents the final synthesis call."""

        raise RunTokenBudgetExhaustedError("Hard cap reached")

    result = _run_agent_with_turn_limit(
        graph, [{"role": "user", "content": "Create a report"}], 26,
        token_budget_wrapup_event=event, execution_evidence=evidence,
        model=SimpleNamespace(stop_reason=None, invoke=exhausted_synthesis),
        finalize_deliverables=collector.collect,
    )
    assert calls == ["model", "write", "model"]
    if hard_cap:
        assert result[0].strip()
        assert result[1:] == (True, "token_budget_wrapup")
    else:
        assert result == ("Report ready.", False, None)
    if with_guidance:
        assert evidence["_answer_review"]["model_calls"] == 2
    assert collector.command["_deliverables"] == {"deliverables/report.html": "output-1"}


@pytest.mark.parametrize("unsupported_notice", [False, True])
@pytest.mark.parametrize("at_delivery_limit", [False, True])
def test_real_graph_automatically_delivers_without_save_tool(
    monkeypatch, tmp_path, unsupported_notice, at_delivery_limit
):
    """A generated file reaches the manifest when the model answers normally."""

    from langchain.agents import create_agent
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.tools import tool

    _install_transport(monkeypatch, lambda request: httpx.Response(201, json={"uuid": "output-1"}))
    def checks(text):
        """Reject a chat-only claim while accepting the independently checked file."""

        return [{"code": "activity_claim_unsupported"}] if "zero work" in text else []

    command = {"run_uuid": "run-123", "_validate_deliverable": checks}
    evidence = {"artifact_required": True}
    collector = AutomaticDeliverables(command, _resources(tmp_path), _config(), evidence=evidence)

    @tool
    def write_file() -> str:
        """Write the final report in the private output directory."""

        (tmp_path / "deliverables" / "report.html").write_text("<h1>Report</h1>")
        return "Updated /deliverables/report.html"

    class Model(FakeMessagesListChatModel):
        def bind_tools(self, *args, **kwargs):
            return self

    from lensnode.plugin_agent import AgentExtension, AgentExtensions

    class Extension(AgentExtension):
        def completion_checks(self, answer):
            return checks(answer)

    if at_delivery_limit:
        evidence["_answer_review"] = {"convergence_nudges": 2, "delivery_turns": 7}
    policy = SimpleNamespace(has_evidence_gates=lambda: True,
                             post_run_checks=lambda *args: {"answer_supported": "supported"})
    state = SimpleNamespace(command=command, runtime_evidence=evidence, question="Create a report",
                            deliverable_collector=collector, agent_extensions=AgentExtensions((Extension(),)),
                            decision_policy=policy)
    graph = create_agent(Model(responses=[
        AIMessage(content="", tool_calls=[{"id": "write", "name": "write_file", "args": {}}]),
        AIMessage(content="Alice did zero work." if unsupported_notice else "The report is attached."),
    ]), tools=[write_file], middleware=[agent_runtime._build_evidence_middleware(state)])
    result = graph.invoke({"messages": [{"role": "user", "content": "Create a report"}]})
    if unsupported_notice:
        assert "zero work" not in result["messages"][-1].content
        assert "attached" in result["messages"][-1].content
    else:
        assert result["messages"][-1].content == "The report is attached."
    assert command["_deliverables"] == {"deliverables/report.html": "output-1"}
    if at_delivery_limit:
        assert evidence["_answer_review"]["delivery_turns"] == 9
        assert evidence["_answer_review"]["publication_grace_used"] == 1


def test_deliverable_stager_uploads_file(monkeypatch, tmp_path):
    (tmp_path / "report.html").write_text("<h1>ok</h1>", encoding="utf-8")
    captured = {}
    client_options = {}

    def handler(request):
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = request.read()
        return httpx.Response(201, json={"ok": True, "uuid": "output-1"})

    _install_transport(monkeypatch, handler, client_options)
    events = []
    tool = _build_deliverable_stager(
        {"run_uuid": "run-123"},
        _resources(tmp_path),
        _config(),
        lambda name, detail: events.append((name, detail)),
    )

    payload = json.loads(tool("report.html"))

    assert payload["ok"] is True
    assert payload["filename"] == "report.html"
    assert captured["url"].endswith("/api/lens/lensnode/deliverables/")
    assert captured["auth"] == "Bearer test-token"
    assert client_options["verify"].verify_mode == ssl.CERT_NONE
    assert b"run-123" in captured["body"]
    assert b"<h1>ok</h1>" in captured["body"]
    assert ("deepagents.artifact.stage.done", None) not in events
    assert any(name == "deepagents.artifact.stage.done" for name, _ in events)


def test_failed_replacement_upload_cannot_publish_the_previous_file(monkeypatch, tmp_path):
    """A stale attachment UUID does not prove the revised report was delivered."""

    (tmp_path / "report.html").write_text("<p>Revised report</p>")
    command = {"run_uuid": "run-123", "_deliverables": {"report.html": "old-output"}}
    _install_transport(monkeypatch, lambda request: httpx.Response(503))
    tool = _build_deliverable_stager(command, _resources(tmp_path), _config(), None)
    payload = json.loads(tool("report.html"))
    assert payload["ok"] is False
    assert payload["error"] == "DELIVERY_FAILED"
    assert command["_deliverables"] == {}


def test_missing_replacement_cannot_publish_the_previous_file(tmp_path):
    """Deleting a staged report before resaving cannot keep its obsolete candidate."""

    command = {"run_uuid": "run-123", "_deliverables": {"report.html": "old-output"}}
    tool = _build_deliverable_stager(command, _resources(tmp_path), _config(), None)
    payload = json.loads(tool("report.html"))
    assert payload["error"] == "FILE_NOT_FOUND"
    assert command["_deliverables"] == {}


def test_identical_write_after_append_is_deduplicated(tmp_path):
    resources = _resources(tmp_path)
    append = _build_append_file_tool(resources, None)
    content = "section one\nsection two\n"
    assert json.loads(
        append.invoke(
            {
                "path": "report.md",
                "chunk_id": "translation-001",
                "content": content,
            }
        )
    )["ok"] is True

    events = []
    middleware = AppendWriteDeduplicationMiddleware(
        tmp_path,
        lambda name, detail: events.append((name, detail)),
    )
    request = SimpleNamespace(
        tool_call={
            "name": "write_file",
            "id": "write-1",
            "args": {"file_path": "report.md", "content": content},
        }
    )
    called = []

    result = middleware.wrap_tool_call(
        request,
        lambda _request: called.append(True)
        or ToolMessage(content="written", tool_call_id="write-1"),
    )

    assert result.status == "success"
    assert "deduplicated" in result.content
    assert called == []
    assert any(
        name == "deepagents.agent.write_deduplicated" for name, _ in events
    )


def test_deliverable_stager_stages_and_replaces_the_same_normalized_path(monkeypatch, tmp_path):
    report = tmp_path / "report.html"
    report.write_text("draft", encoding="utf-8")
    uploads = []

    def handler(request):
        uploads.append(request.read())
        return httpx.Response(
            201,
            json={"code": 0, "message": "success", "data": {"uuid": f"output-{len(uploads)}"}},
        )

    _install_transport(monkeypatch, handler)
    command = {"run_uuid": "run-123"}
    tool = _build_deliverable_stager(command, _resources(tmp_path), _config(), None)

    assert json.loads(tool("./report.html"))["ok"] is True
    report.write_text("corrected", encoding="utf-8")
    assert json.loads(tool("report.html"))["ok"] is True

    assert command["_deliverables"] == {"report.html": "output-2"}
    assert b"draft" in uploads[0]
    assert b"corrected" in uploads[1]
    assert all(b'name="staged"\r\n\r\ntrue\r\n' in body for body in uploads)


def test_deliverable_stager_preserves_distinct_files_in_the_current_batch(monkeypatch, tmp_path):
    (tmp_path / "report.md").write_text("report", encoding="utf-8")
    (tmp_path / "appendix.md").write_text("appendix", encoding="utf-8")
    uploads = []

    def handler(request):
        uploads.append(request.read())
        return httpx.Response(201, json={"uuid": f"output-{len(uploads)}"})

    _install_transport(monkeypatch, handler)
    command = {"run_uuid": "run-123"}
    tool = _build_deliverable_stager(command, _resources(tmp_path), _config(), None)

    assert json.loads(tool("report.md"))["ok"] is True
    assert json.loads(tool("appendix.md"))["ok"] is True

    assert command["_deliverables"] == {"report.md": "output-1", "appendix.md": "output-2"}


def test_deliverable_stager_requires_a_publication_uuid(monkeypatch, tmp_path):
    """A successful HTTP upload alone must not claim final delivery success."""

    (tmp_path / "report.md").write_text("report", encoding="utf-8")
    _install_transport(monkeypatch, lambda request: httpx.Response(201, json={"ok": True}))
    command = {"run_uuid": "run-123"}
    tool = _build_deliverable_stager(command, _resources(tmp_path), _config(), None)

    payload = json.loads(tool("report.md"))

    assert payload["ok"] is False
    assert payload["error"] == "DELIVERY_FAILED"
    assert not command.get("_deliverables")


def test_evidence_recheck_discards_draft_batch_and_persists_the_replacement(monkeypatch, tmp_path):
    (tmp_path / "draft.md").write_text("unsupported draft", encoding="utf-8")
    (tmp_path / "final.md").write_text("supported replacement", encoding="utf-8")
    uploads = []

    def handler(request):
        uploads.append(request.read())
        return httpx.Response(201, json={"uuid": f"output-{len(uploads)}"})

    class _Policy:
        """Return a controllable complete-evidence grounding verdict."""

        verdict = "unsupported"

        def has_evidence_gates(self):
            return True

        def post_run_checks(self, question, answer, evidence=None):
            return {"answer_supported": self.verdict, "evidence_completeness": "complete"}

    _install_transport(monkeypatch, handler)
    manifest = {}
    command = {"run_uuid": "run-123", "_deliverables": manifest}
    runtime_evidence = {"_deliverables": manifest}
    checkpoints = []
    policy = _Policy()
    state = SimpleNamespace(
        command=command,
        runtime_evidence=runtime_evidence,
        question="Explain the incident.",
        decision_policy=policy,
        persist_execution_state=lambda: checkpoints.append(dict(runtime_evidence["_deliverables"])),
    )
    tool = _build_deliverable_stager(command, _resources(tmp_path), _config(), None)
    middleware = agent_runtime._build_evidence_middleware(state)
    assert json.loads(tool("draft.md"))["ok"] is True

    recheck = middleware.after_model({"messages": [AIMessage(content="Unsupported draft.")]}, None)

    assert recheck["jump_to"] == "model"
    assert command["_deliverables"] is runtime_evidence["_deliverables"]
    assert manifest == {}
    assert checkpoints == [{}]

    assert json.loads(tool("final.md"))["ok"] is True
    policy.verdict = "supported"
    assert middleware.after_model({"messages": [AIMessage(content="Supported final answer.")]}, None) is None
    assert manifest == {"final.md": "output-2"}
    assert runtime_evidence["_deliverables"] == {"final.md": "output-2"}


def test_deliverable_stager_ignores_runtime_instance_id(monkeypatch, tmp_path):
    (tmp_path / "report.html").write_text("ok", encoding="utf-8")
    captured = {}

    def handler(request):
        captured["body"] = request.read()
        return httpx.Response(201, json={"ok": True, "uuid": "output-1"})

    _install_transport(monkeypatch, handler)
    tool = _build_deliverable_stager(
        {
            "run_uuid": "parent-run",
            "runtime_instance_id": "parent-run-subagent-1",
        },
        _resources(tmp_path),
        _config(),
        None,
    )

    payload = json.loads(tool("report.html"))

    assert payload["ok"] is True
    assert b"\r\nparent-run\r\n" in captured["body"]
    assert b"parent-run-subagent-1" not in captured["body"]


def test_deliverable_stager_rejects_escape(monkeypatch, tmp_path):
    def handler(request):
        raise AssertionError("upload must not be attempted")

    _install_transport(monkeypatch, handler)
    tool = _build_deliverable_stager(
        {"run_uuid": "run-123"},
        _resources(tmp_path),
        _config(),
        None,
    )

    payload = json.loads(tool("../secret.txt"))

    assert payload["ok"] is False
    assert payload["error"] == "PATH_NOT_ALLOWED"


def test_deliverable_stager_rejects_host_tmp_output(
    monkeypatch, tmp_path
):
    output = Path("/tmp") / f"sourcelens-test-{tmp_path.name}.pptx"
    output.write_bytes(b"pptx")
    captured = {}

    def handler(request):
        captured["body"] = request.read()
        return httpx.Response(201, json={"ok": True, "uuid": "output-1"})

    _install_transport(monkeypatch, handler)
    tool = _build_deliverable_stager(
        {"run_uuid": "run-123"},
        _resources(tmp_path),
        _config(),
        None,
    )

    try:
        payload = json.loads(tool(str(output)))
    finally:
        output.unlink(missing_ok=True)

    assert payload["ok"] is False
    assert payload["error"] == "FILE_NOT_FOUND"
    assert captured == {}


def test_deliverable_stager_resolves_absolute_virtual_scratch_path(
    monkeypatch, tmp_path
):
    virtual_output = tmp_path / "tmp" / "report.html"
    virtual_output.parent.mkdir()
    virtual_output.write_text("<h1>scratch</h1>", encoding="utf-8")
    captured = {}

    def handler(request):
        captured["body"] = request.read()
        return httpx.Response(201, json={"ok": True, "uuid": "output-1"})

    _install_transport(monkeypatch, handler)
    tool = _build_deliverable_stager(
        {"run_uuid": "run-123"},
        _resources(tmp_path),
        _config(),
        None,
    )

    payload = json.loads(tool("/tmp/report.html"))

    assert payload["ok"] is True
    assert b"<h1>scratch</h1>" in captured["body"]


def test_deliverable_stager_rejects_oversized(monkeypatch, tmp_path):
    (tmp_path / "big.html").write_text("x" * 100, encoding="utf-8")

    def handler(request):
        raise AssertionError("upload must not be attempted")

    _install_transport(monkeypatch, handler)
    cfg = _config()
    object.__setattr__(cfg, "deliverable_max_bytes", 10)
    tool = _build_deliverable_stager(
        {"run_uuid": "run-123"},
        _resources(tmp_path),
        cfg,
        None,
    )

    payload = json.loads(tool("big.html"))

    assert payload["ok"] is False
    assert payload["error"] == "FILE_TOO_LARGE"


def test_deliverable_stager_missing_file(monkeypatch, tmp_path):
    def handler(request):
        raise AssertionError("upload must not be attempted")

    _install_transport(monkeypatch, handler)
    tool = _build_deliverable_stager(
        {"run_uuid": "run-123"},
        _resources(tmp_path),
        _config(),
        None,
    )

    payload = json.loads(tool("nope.html"))

    assert payload["ok"] is False
    assert payload["error"] == "FILE_NOT_FOUND"


def test_append_file_writes_idempotent_limited_chunks(tmp_path):
    tool = _build_append_file_tool(_resources(tmp_path), None)

    assert tool.metadata == {"operation": "write", "idempotent": True}

    first = json.loads(
        tool.invoke(
            {
                "path": "translation.md",
                "chunk_id": "translation-001",
                "content": "first section\n",
            }
        )
    )
    duplicate = json.loads(
        tool.invoke(
            {
                "path": "translation.md",
                "chunk_id": "translation-001",
                "content": "first section\n",
            }
        )
    )
    second = json.loads(
        tool.invoke(
            {
                "path": "translation.md",
                "chunk_id": "translation-002",
                "content": "second section\n",
            }
        )
    )

    assert first == {"ok": True, "duplicate": False}
    assert duplicate == {"ok": True, "duplicate": True}
    assert second == {"ok": True, "duplicate": False}
    assert (tmp_path / "translation.md").read_text(encoding="utf-8") == (
        "first section\nsecond section\n"
    )


def test_append_file_rejects_conflicting_or_oversized_chunk(tmp_path):
    tool = _build_append_file_tool(_resources(tmp_path), None)
    tool.invoke(
        {
            "path": "translation.md",
            "chunk_id": "translation-001",
            "content": "first section\n",
        }
    )

    conflict = json.loads(
        tool.invoke(
            {
                "path": "translation.md",
                "chunk_id": "translation-001",
                "content": "changed section\n",
            }
        )
    )
    oversized = json.loads(
        tool.invoke(
            {
                "path": "translation.md",
                "chunk_id": "translation-002",
                "content": "x" * (25 * 1024),
            }
        )
    )

    assert conflict["ok"] is False
    assert conflict["error"] == "CHUNK_CONFLICT"
    assert oversized["ok"] is False
    assert oversized["error"] == "CHUNK_TOO_LARGE"


def test_append_file_recovers_chunk_written_before_manifest_commit(tmp_path):
    content = "first section\n"
    (tmp_path / "translation.md").write_text(content, encoding="utf-8")
    (tmp_path / ".sourcelens-append-chunks.json").write_text(
        json.dumps(
            {
                "chunks": {
                    "translation.md:translation-001": {
                        "byte_size": len(content.encode("utf-8")),
                        "offset": 0,
                        "sha256": hashlib.sha256(
                            content.encode("utf-8")
                        ).hexdigest(),
                        "state": "pending",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    tool = _build_append_file_tool(_resources(tmp_path), None)

    recovered = json.loads(
        tool.invoke(
            {
                "path": "translation.md",
                "chunk_id": "translation-001",
                "content": content,
            }
        )
    )

    assert recovered == {"ok": True, "duplicate": True}
    assert (tmp_path / "translation.md").read_text(encoding="utf-8") == content
