import hashlib
import json
import ssl
from pathlib import Path
from types import SimpleNamespace

import httpx
from langchain_core.messages import AIMessage

from lensnode import agent_runtime
from lensnode.agent_tools import (
    _build_append_file_tool,
    _build_save_deliverable_tool,
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


def test_save_deliverable_uploads_file(monkeypatch, tmp_path):
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
    tool = _build_save_deliverable_tool(
        {"run_uuid": "run-123"},
        _resources(tmp_path),
        _config(),
        lambda name, detail: events.append((name, detail)),
    )

    payload = json.loads(tool.invoke({"path": "report.html"}))

    assert payload["ok"] is True
    assert payload["filename"] == "report.html"
    assert captured["url"].endswith("/api/lens/lensnode/deliverables/")
    assert captured["auth"] == "Bearer test-token"
    assert client_options["verify"].verify_mode == ssl.CERT_NONE
    assert b"run-123" in captured["body"]
    assert b"<h1>ok</h1>" in captured["body"]
    assert ("tool.save_deliverable.done", None) not in events
    assert any(name == "tool.save_deliverable.done" for name, _ in events)


def test_save_deliverable_stages_and_replaces_the_same_normalized_path(monkeypatch, tmp_path):
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
    tool = _build_save_deliverable_tool(command, _resources(tmp_path), _config(), None)

    assert json.loads(tool.invoke({"path": "./report.html"}))["ok"] is True
    report.write_text("corrected", encoding="utf-8")
    assert json.loads(tool.invoke({"path": "report.html"}))["ok"] is True

    assert command["_deliverables"] == {"report.html": "output-2"}
    assert b"draft" in uploads[0]
    assert b"corrected" in uploads[1]
    assert all(b'name="staged"\r\n\r\ntrue\r\n' in body for body in uploads)


def test_save_deliverable_preserves_distinct_files_in_the_current_batch(monkeypatch, tmp_path):
    (tmp_path / "report.md").write_text("report", encoding="utf-8")
    (tmp_path / "appendix.md").write_text("appendix", encoding="utf-8")
    uploads = []

    def handler(request):
        uploads.append(request.read())
        return httpx.Response(201, json={"uuid": f"output-{len(uploads)}"})

    _install_transport(monkeypatch, handler)
    command = {"run_uuid": "run-123"}
    tool = _build_save_deliverable_tool(command, _resources(tmp_path), _config(), None)

    assert json.loads(tool.invoke({"path": "report.md"}))["ok"] is True
    assert json.loads(tool.invoke({"path": "appendix.md"}))["ok"] is True

    assert command["_deliverables"] == {"report.md": "output-1", "appendix.md": "output-2"}


def test_save_deliverable_requires_a_publication_uuid(monkeypatch, tmp_path):
    """A successful HTTP upload alone must not claim final delivery success."""

    (tmp_path / "report.md").write_text("report", encoding="utf-8")
    _install_transport(monkeypatch, lambda request: httpx.Response(201, json={"ok": True}))
    command = {"run_uuid": "run-123"}
    tool = _build_save_deliverable_tool(command, _resources(tmp_path), _config(), None)

    payload = json.loads(tool.invoke({"path": "report.md"}))

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
    tool = _build_save_deliverable_tool(command, _resources(tmp_path), _config(), None)
    middleware = agent_runtime._build_evidence_middleware(state)
    assert json.loads(tool.invoke({"path": "draft.md"}))["ok"] is True

    recheck = middleware.after_model({"messages": [AIMessage(content="Unsupported draft.")]}, None)

    assert recheck["jump_to"] == "model"
    assert command["_deliverables"] is runtime_evidence["_deliverables"]
    assert manifest == {}
    assert checkpoints == [{}]

    assert json.loads(tool.invoke({"path": "final.md"}))["ok"] is True
    policy.verdict = "supported"
    assert middleware.after_model({"messages": [AIMessage(content="Supported final answer.")]}, None) is None
    assert manifest == {"final.md": "output-2"}
    assert runtime_evidence["_deliverables"] == {"final.md": "output-2"}


def test_save_deliverable_ignores_runtime_instance_id(monkeypatch, tmp_path):
    (tmp_path / "report.html").write_text("ok", encoding="utf-8")
    captured = {}

    def handler(request):
        captured["body"] = request.read()
        return httpx.Response(201, json={"ok": True, "uuid": "output-1"})

    _install_transport(monkeypatch, handler)
    tool = _build_save_deliverable_tool(
        {
            "run_uuid": "parent-run",
            "runtime_instance_id": "parent-run-subagent-1",
        },
        _resources(tmp_path),
        _config(),
        None,
    )

    payload = json.loads(tool.invoke({"path": "report.html"}))

    assert payload["ok"] is True
    assert b"\r\nparent-run\r\n" in captured["body"]
    assert b"parent-run-subagent-1" not in captured["body"]


def test_save_deliverable_rejects_escape(monkeypatch, tmp_path):
    def handler(request):
        raise AssertionError("upload must not be attempted")

    _install_transport(monkeypatch, handler)
    tool = _build_save_deliverable_tool(
        {"run_uuid": "run-123"},
        _resources(tmp_path),
        _config(),
        None,
    )

    payload = json.loads(tool.invoke({"path": "../secret.txt"}))

    assert payload["ok"] is False
    assert payload["error"] == "PATH_NOT_ALLOWED"


def test_save_deliverable_rejects_host_tmp_output(
    monkeypatch, tmp_path
):
    output = Path("/tmp") / f"sourcelens-test-{tmp_path.name}.pptx"
    output.write_bytes(b"pptx")
    captured = {}

    def handler(request):
        captured["body"] = request.read()
        return httpx.Response(201, json={"ok": True, "uuid": "output-1"})

    _install_transport(monkeypatch, handler)
    tool = _build_save_deliverable_tool(
        {"run_uuid": "run-123"},
        _resources(tmp_path),
        _config(),
        None,
    )

    try:
        payload = json.loads(tool.invoke({"path": str(output)}))
    finally:
        output.unlink(missing_ok=True)

    assert payload["ok"] is False
    assert payload["error"] == "FILE_NOT_FOUND"
    assert captured == {}


def test_save_deliverable_resolves_absolute_virtual_scratch_path(
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
    tool = _build_save_deliverable_tool(
        {"run_uuid": "run-123"},
        _resources(tmp_path),
        _config(),
        None,
    )

    payload = json.loads(tool.invoke({"path": "/tmp/report.html"}))

    assert payload["ok"] is True
    assert b"<h1>scratch</h1>" in captured["body"]


def test_save_deliverable_rejects_oversized(monkeypatch, tmp_path):
    (tmp_path / "big.html").write_text("x" * 100, encoding="utf-8")

    def handler(request):
        raise AssertionError("upload must not be attempted")

    _install_transport(monkeypatch, handler)
    cfg = _config()
    object.__setattr__(cfg, "deliverable_max_bytes", 10)
    tool = _build_save_deliverable_tool(
        {"run_uuid": "run-123"},
        _resources(tmp_path),
        cfg,
        None,
    )

    payload = json.loads(tool.invoke({"path": "big.html"}))

    assert payload["ok"] is False
    assert payload["error"] == "FILE_TOO_LARGE"


def test_save_deliverable_missing_file(monkeypatch, tmp_path):
    def handler(request):
        raise AssertionError("upload must not be attempted")

    _install_transport(monkeypatch, handler)
    tool = _build_save_deliverable_tool(
        {"run_uuid": "run-123"},
        _resources(tmp_path),
        _config(),
        None,
    )

    payload = json.loads(tool.invoke({"path": "nope.html"}))

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
