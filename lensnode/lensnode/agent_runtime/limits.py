"""Budget policy for individual LensNode agent runs."""


def resolve_token_budget(config, command):
    """Return the token budget for one run.

    Prefer the budget the control plane sends with the run (derived from the
    Assistant profile); fall back to the node config only when the run carries
    none. A max_tokens of 0 means unlimited, so no ceiling is applied.
    """

    run_budget = (command or {}).get("token_budget")
    fallback_max = max(
        int(getattr(config, "token_budget_max_tokens", 200000) or 0),
        0,
    )
    fallback_reserve = max(
        int(
            getattr(config, "token_budget_final_reserve_tokens", 40000) or 0
        ),
        0,
    )
    if isinstance(run_budget, dict):
        max_tokens = max(int(run_budget.get("max_tokens") or 0), 0)
        reserve = max(
            int(run_budget.get("final_reserve_tokens") or 0),
            0,
        )
        return {
            "profile": str(run_budget.get("profile") or "standard"),
            "max_tokens": max_tokens,
            "final_reserve_tokens": min(reserve, max_tokens)
            if max_tokens
            else 0,
        }
    return {
        "profile": "system",
        "max_tokens": fallback_max,
        "final_reserve_tokens": min(fallback_reserve, fallback_max),
    }


def resolve_tool_call_budget(config, command):
    """Return the maximum actual tool calls permitted for one run.

    The control plane may provide an explicit per-run budget. Otherwise the
    LensNode setting applies. A value of zero disables the limit.
    """

    run_budget = (command or {}).get("tool_budget")
    fallback_max = max(
        int(getattr(config, "tool_budget_max_calls", 64) or 0),
        0,
    )
    if isinstance(run_budget, dict):
        return max(int(run_budget.get("max_calls") or 0), 0)
    return fallback_max
