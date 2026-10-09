"""Shared token accounting without importing the agent runtime."""

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
