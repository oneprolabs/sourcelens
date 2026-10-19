"""Guard the backend's read-only mirror of the host gate phases.

The authoritative gate semantics live here (``GATE_REGISTRY`` / ``GATE_PHASE``);
the backend keeps a minimal mirror only to label gates in the admin API.  The
two packages cannot share a module, so this test reads the backend literal and
fails when the mirror drifts.
"""

import ast
from pathlib import Path

from lensnode.agent_runtime.decision_gates import (
    GATE_PHASE,
    GATE_REGISTRY,
    PHASE_ORDER,
)
from lensnode.agent_runtime.evidence_material import DEFAULT_REVIEW_STATE_CHARS


def _backend_constant(name, path="plugins/decisions.py"):
    source = (
        Path(__file__).resolve().parents[2]
        / "backend"
        / "lens"
        / path
    ).read_text()
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and any(
            getattr(target, "id", "") == name for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} is missing from the backend mirror")


def test_backend_gate_phase_mirror_matches_the_host_registry():
    assert _backend_constant("GATE_ACTIVE_PHASE") == GATE_PHASE
    assert _backend_constant("GATE_PHASES") == {
        key: spec["phase"] for key, spec in GATE_REGISTRY.items()
    }
    assert _backend_constant("GATE_PHASE_ORDER") == PHASE_ORDER


def test_backend_review_capacity_matches_runtime_and_fits_binding_validation():
    """A backend-frozen default must not silently restore the old review truncation."""

    assert _backend_constant("DEFAULT_REVIEW_STATE_CHARS", "decisions.py") == DEFAULT_REVIEW_STATE_CHARS
    assert _backend_constant("MAX_STATE_CHARS") >= DEFAULT_REVIEW_STATE_CHARS
    assert _backend_constant("DECISION_STATE_CHARS_MAX", "plugins/registry.py") >= DEFAULT_REVIEW_STATE_CHARS
