"""Source projections preserve coverage without a report workflow."""

import json

from lensnode.plugin_package_loader import load_runtime_contract

from lensnode.tool_evidence import ToolEvidenceStore


def result():
    """A verified source snapshot containing no matching work."""

    return {"ok": True, "since": "2026-10-12T00:00:00Z", "until": "2026-10-13T00:00:00Z", "items": []}


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
    ledger = ToolEvidenceStore(tmp_path / "private")
    ledger.record(
        "github",
        "connection",
        "1.0.0",
        "github_activity_summary",
        {},
        {**projected, "_evidence": contract.project_tool_evidence("github_activity_summary", projected, "connection")},
    )
    assert ledger.material()["complete"] is False


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
