"""Structural validation for Decision Plugin gate bindings.

The host owns gate semantics (fallback direction, thresholds, phases) in the
LensNode registry.  This module only validates the structure a manifest and a
binding must satisfy before any Run is dispatched.
"""

from .registry import PluginRegistryError

GATE_CONFIG_KEYS = frozenset({"threshold", "margin", "max_state_chars"})
ANALYSIS_CONFIG_KEYS = frozenset()
THRESHOLD_KINDS = frozenset({"noul", "score"})
DEFAULT_THRESHOLD = 0.5
DEFAULT_MARGIN = 0.1
DEFAULT_MAX_STATE_CHARS = 4000
MIN_STATE_CHARS = 100
MAX_STATE_CHARS = 100000

# Read-only mirror of the host gate phases for the admin API.
#
# The authoritative table lives in LensNode
# (``lensnode/lensnode/agent_runtime/decision_gates.py``: ``GATE_REGISTRY`` /
# ``GATE_PHASE``); the two packages cannot share a module, so this mirror is
# guarded by a cross-package test
# (``lensnode/tests/test_decision_gate_phases.py``).  Drift is low severity:
# a stale phase only mislabels a gate in the admin UI, the runtime still falls
# back to the inner policy.
GATE_ACTIVE_PHASE = "P3"
GATE_PHASES = {
    "search_needed": "P1",
    "evidence_requirement": "P3",
    "evidence_sufficient": "P3",
    "answer_supported": "P3",
}
# Mirrors LensNode's ``PHASE_ORDER`` (``decision_gates.py``); guarded by
# ``lensnode/tests/test_decision_gate_phases.py``.  A gate is active once the
# release reaches its phase, so earlier phases stay active (P1 under P3).
GATE_PHASE_ORDER = {"P1": 1, "P3": 3}


def gate_is_active(phase):
    """Return whether one gate phase is active for the current release.

    Mirrors the LensNode ordering rule
    (``PHASE_ORDER[phase] > PHASE_ORDER[GATE_PHASE]`` -> inactive): a gate
    stays active once its phase is reached.
    """

    return GATE_PHASE_ORDER.get(phase, 99) <= GATE_PHASE_ORDER.get(
        GATE_ACTIVE_PHASE, 0
    )


def control_decision(plugin, key):
    """Return one control Decision declared by an installed Plugin."""

    for decision in getattr(plugin, "decisions", ()) or ():
        if decision.get("key") == key and decision.get("mode") == "control":
            return decision
    return None


def validate_decision_gates(plugin, gates, capability=None):
    """Return normalized control-gate config for one Plugin binding."""

    if gates is None:
        return {}
    if not isinstance(gates, dict):
        raise PluginRegistryError("decision gates must be an object")
    if not gates:
        return {}
    if getattr(plugin, "plugin_type", "integration") != "decision":
        raise PluginRegistryError("plugin does not provide decisions")
    normalized = {}
    for key, config in gates.items():
        decision = control_decision(plugin, key)
        if decision is None:
            raise PluginRegistryError("decision gate is not declared")
        normalized[key] = _normalize_gate_config(
            decision,
            config,
            capability,
        )
    return normalized


def default_control_gates(plugin, capability):
    """Return the manifest-default control gates for one Assistant mode.

    Auto mode enables every control Decision whose ``applies_to`` matches the
    Assistant capability, each filled from the manifest ``defaults`` (or the
    built-in fallbacks).  Manual bindings skip this and use their stored
    config instead.
    """

    gates = {}
    for decision in getattr(plugin, "decisions", ()) or ():
        if decision.get("mode") != "control":
            continue
        applies_to = decision.get("applies_to") or []
        if capability is not None and capability not in applies_to:
            continue
        gates[decision["key"]] = {}
    if not gates:
        return {}
    return validate_decision_gates(plugin, gates, capability)


def analysis_decision(plugin, key):
    """Return one analysis Decision declared by an installed Plugin."""

    for decision in getattr(plugin, "decisions", ()) or ():
        if decision.get("key") == key and decision.get("mode") == "analysis":
            return decision
    return None


def rank_eligible(decision):
    """Return whether one analysis Decision can drive deterministic ranking.

    Ranking needs a comparable scalar.  ``score`` qualifies because its rubric
    is an ordered scale; ``choice`` qualifies when it names a ``target_option``
    that the rubric (the option list) contains, so the probability of that
    option is comparable across candidates.
    """

    kind = decision.get("kind")
    if kind == "score":
        return True
    if kind != "choice":
        return False
    target_option = decision.get("target_option")
    return bool(target_option) and target_option in (
        decision.get("rubric") or []
    )


def validate_decision_analyses(plugin, analyses):
    """Return normalized analysis config for one Plugin binding."""

    if analyses is None:
        return {}
    if not isinstance(analyses, dict):
        raise PluginRegistryError("decision analyses must be an object")
    if not analyses:
        return {}
    if getattr(plugin, "plugin_type", "integration") != "decision":
        raise PluginRegistryError("plugin does not provide decisions")
    normalized = {}
    for key, config in analyses.items():
        decision = analysis_decision(plugin, key)
        if decision is None:
            raise PluginRegistryError("decision analysis is not declared")
        if not rank_eligible(decision):
            raise PluginRegistryError("decision analysis cannot be ranked")
        if not isinstance(config, dict) or set(config).difference(
            ANALYSIS_CONFIG_KEYS
        ):
            raise PluginRegistryError("decision analysis config is invalid")
        normalized[key] = {}
    return normalized


def resolve_decision_analyses(plugin, analyses):
    """Return frozen analysis config including Tool identity and rubric."""

    resolved = {}
    for key in (analyses or {}):
        decision = analysis_decision(plugin, key)
        if decision is None:
            continue
        entry = {
            "kind": decision["kind"],
            "tool_key": decision["tool_keys"][0],
        }
        if decision.get("rubric"):
            entry["rubric"] = list(decision["rubric"])
        if decision.get("target_option"):
            entry["target_option"] = decision["target_option"]
        if decision.get("summary"):
            entry["summary"] = decision["summary"]
        resolved[key] = entry
    return resolved


def resolve_decision_gates(plugin, gates):
    """Return frozen gate config including its declared Tool identity.

    A Plugin upgrade may remove a gate the binding still names.  The frozen
    entry keeps the key with ``declared: False`` and no Tool, so LensNode
    reports ``not_declared`` and falls back to the inner policy instead of
    failing the Run.
    """

    resolved = {}
    for key, config in (gates or {}).items():
        decision = control_decision(plugin, key)
        if decision is None:
            # A Plugin upgrade removed a gate the binding still names.  The key
            # stays so the host can report `not_declared` (distinct from an
            # unknown key) and fall back to the inner policy.
            resolved[key] = {
                "declared": False,
                "tool_key": "",
                "kind": "",
                **config,
            }
            continue
        resolved[key] = {
            **config,
            "declared": True,
            "kind": decision["kind"],
            "tool_key": decision["tool_keys"][0],
        }
    return resolved


def _normalize_gate_config(decision, config, capability):
    if not isinstance(config, dict):
        raise PluginRegistryError("decision gate config must be an object")
    if set(config).difference(GATE_CONFIG_KEYS):
        raise PluginRegistryError("decision gate config is invalid")
    applies_to = decision.get("applies_to") or []
    if capability is not None and capability not in applies_to:
        raise PluginRegistryError(
            "decision gate does not apply to this Assistant"
        )
    kind = decision["kind"]
    defaults = decision.get("defaults") or {}
    normalized = {}
    if kind in THRESHOLD_KINDS:
        normalized["threshold"] = _unit_number(
            config.get(
                "threshold",
                defaults.get("threshold", DEFAULT_THRESHOLD),
            )
        )
        normalized["margin"] = _unit_number(
            config.get("margin", defaults.get("margin", DEFAULT_MARGIN))
        )
    elif "threshold" in config or "margin" in config:
        raise PluginRegistryError(
            "decision gate does not accept a threshold"
        )
    normalized["max_state_chars"] = _state_limit(
        config.get(
            "max_state_chars",
            defaults.get("max_state_chars", DEFAULT_MAX_STATE_CHARS),
        )
    )
    return normalized


def _unit_number(value):
    if type(value) not in (int, float) or not 0 <= value <= 1:
        raise PluginRegistryError("decision gate number is invalid")
    return value


def _state_limit(value):
    if (
        type(value) is not int
        or not MIN_STATE_CHARS <= value <= MAX_STATE_CHARS
    ):
        raise PluginRegistryError("decision gate state limit is invalid")
    return value
