"""Shared capability-family protocol for the LensNode agent runtime."""

CAPABILITY_FAMILY_ORDER = (
    "skill",
    "plugin",
    "mcp",
    "workspace",
    "artifact_delivery",
)
# Accepted tool-level families.  ``decision`` is the business family for a
# Decision Plugin's primitives; it is deliberately kept OUT of
# ``CAPABILITY_FAMILY_ORDER`` so routing/normalization never derives evidence
# from a decision capability.
CAPABILITY_FAMILIES = frozenset(CAPABILITY_FAMILY_ORDER) | {"decision"}
EVIDENCE_CAPABILITY_FAMILIES = frozenset(
    {"skill", "plugin", "mcp", "workspace"}
)
LEGACY_CAPABILITY_FAMILIES = frozenset({"tool"})


def is_capability_family(value):
    """Return whether a value is a supported explicit capability family."""

    return value in CAPABILITY_FAMILIES
