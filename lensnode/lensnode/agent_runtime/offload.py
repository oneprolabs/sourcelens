"""Deep Agents file-offload configuration for LensNode runs."""

import threading

from deepagents.middleware.filesystem import FilesystemMiddleware

_OFFLOAD_PATCH_LOCK = threading.Lock()


def _preserve_inline_tool_results():
    """Keep trusted, bounded evidence pages visible after they are consumed."""

    if getattr(FilesystemMiddleware.wrap_tool_call, "_lens_inline_wrapped", False):
        return
    original_sync = FilesystemMiddleware.wrap_tool_call
    original_async = FilesystemMiddleware.awrap_tool_call

    def wrap_tool_call(self, request, handler):
        """Let a trusted paginated Tool own the size of its inline result."""

        tool_flags = getattr(request.tool, "metadata", None) or {}
        if tool_flags.get("bounded_inline_result") is True:
            return handler(request)
        return original_sync(self, request, handler)

    async def awrap_tool_call(self, request, handler):
        """Preserve the same inline contract for asynchronous execution."""

        tool_flags = getattr(request.tool, "metadata", None) or {}
        if tool_flags.get("bounded_inline_result") is True:
            return await handler(request)
        return await original_async(self, request, handler)

    wrap_tool_call._lens_inline_wrapped = True
    FilesystemMiddleware.wrap_tool_call = wrap_tool_call
    FilesystemMiddleware.awrap_tool_call = awrap_tool_call


def apply_offload_thresholds(config):
    """Tune deepagents' proactive file-offload thresholds (issue #60).

    FilesystemMiddleware evicts oversized tool results (and human messages) to
    re-readable workspace files, but create_deep_agent hard-wires it with the
    library defaults (tool 20000 / human 50000 tokens) and exposes no way to
    configure them. Lowering the tool threshold offloads large workspace reads
    to files sooner, keeping the inline context lean so heavy-retrieval runs
    stop thrashing the summarizer (issue #60 "Snape timeline" repro: offload
    off = 4 compactions and no convergence; offload on = 0 compactions).

    The thresholds are captured into the wrapper closure and the wrapper is
    installed once per process, guarded by a lock so concurrent runs (lensnode
    executes runs on worker threads) cannot double-install. There is no shared
    mutable state to race across runs. Values are injected via setdefault, so
    the class identity is unchanged (required-middleware and isinstance checks
    are unaffected) and any explicit call-site argument still wins. The tool
    threshold is always set (config default 5000; 0 disables eviction); a None
    human threshold leaves the library default in place.
    """

    if getattr(FilesystemMiddleware.__init__, "_lens_offload_wrapped", False):
        return
    with _OFFLOAD_PATCH_LOCK:
        if getattr(
            FilesystemMiddleware.__init__, "_lens_offload_wrapped", False
        ):
            return
        tool_tokens = config.offload_tool_tokens
        human_tokens = config.offload_human_tokens
        original_init = FilesystemMiddleware.__init__
        _preserve_inline_tool_results()

        def init_with_offload_defaults(self, *args, **kwargs):
            kwargs.setdefault("tool_token_limit_before_evict", tool_tokens)
            if human_tokens is not None:
                kwargs.setdefault(
                    "human_message_token_limit_before_evict", human_tokens
                )
            original_init(self, *args, **kwargs)

        init_with_offload_defaults._lens_offload_wrapped = True
        FilesystemMiddleware.__init__ = init_with_offload_defaults
