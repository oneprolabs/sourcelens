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


def _backend_constant(name):
    source = (
        Path(__file__).resolve().parents[2]
        / "backend"
        / "lens"
        / "plugins"
        / "decisions.py"
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
