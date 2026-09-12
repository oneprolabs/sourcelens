"""Budget policy and shared accounting for LensNode agent runs."""

import threading


class SharedTokenBudget:
    """Thread-safe token accounting shared by a parent and its delegates."""

    def __init__(self, max_tokens=0):
        self.max_tokens = max(int(max_tokens or 0), 0)
        self._lock = threading.Lock()
        self._usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }

    def consume(self, prompt_tokens=0, completion_tokens=0, total_tokens=0):
        """Atomically add usage and return cumulative usage and hard-limit state."""
        prompt_tokens = max(int(prompt_tokens or 0), 0)
        completion_tokens = max(int(completion_tokens or 0), 0)
        total_tokens = max(
            int(total_tokens or 0), prompt_tokens + completion_tokens
        )
        with self._lock:
            values = (
                ("prompt_tokens", prompt_tokens),
                ("completion_tokens", completion_tokens),
                ("total_tokens", total_tokens),
            )
            for key, value in values:
                self._usage[key] += value
            return dict(self._usage), bool(
                self.max_tokens
                and self._usage["total_tokens"] >= self.max_tokens
            )

    @property
    def usage(self):
        with self._lock:
            return dict(self._usage)

    def restore(self, usage):
        """Restore cumulative usage from a durable checkpoint."""
        with self._lock:
            for key in self._usage:
                self._usage[key] = max(int((usage or {}).get(key) or 0), 0)


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
