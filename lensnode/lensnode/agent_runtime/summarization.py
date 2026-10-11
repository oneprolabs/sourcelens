"""Context compaction policy for long-running LensNode investigations."""

from langchain.agents.middleware import SummarizationMiddleware
from langchain_core.messages import HumanMessage, RemoveMessage, trim_messages

from ..gateway_model import LensGatewayChatModel

CONTINUATION_SUMMARY_PROMPT = (
    "You are compacting the context of an IN-PROGRESS investigation to "
    "free up space. The user's question has NOT been answered yet — you "
    "are still gathering evidence from the workspace and MUST keep working "
    "after this compaction. The notes below replace the older conversation "
    "history.\n\n"
    "The user's own messages are preserved verbatim outside this summary, "
    "so do NOT restate the question here — focus on distilling the evidence "
    "and the remaining work. Use these sections, writing 'None' where "
    "empty:\n\n"
    "## EVIDENCE GATHERED SO FAR\n"
    "Concrete findings already discovered, with file paths and the key "
    "facts/identifiers/values they contain. Be specific.\n\n"
    "## STILL TO DO\n"
    "What evidence is still missing to fully answer the question.\n\n"
    "Do NOT write a final answer here. Do NOT imply the task is complete "
    "or already answered. This is a working note to yourself so you can "
    "keep investigating, then produce the final answer in a later step.\n\n"
    "<messages>\n{messages}\n</messages>"
)


class InvalidSummaryError(ValueError):
    """Keep the existing conversation when compaction has no usable summary."""


def _validated_summary(summary):
    """Reject empty summaries and upstream placeholders before removing history."""

    if not summary.strip() or summary.strip() in {
        "Previous conversation was too long to summarize.",
        "No previous conversation history.",
    }:
        raise InvalidSummaryError("invalid_summary")
    return summary


class LensSummarizationMiddleware(SummarizationMiddleware):
    """Compact older turns once the running context grows past a threshold.

    Deep investigations accumulate large tool outputs (file reads) that
    make every later LLM round re-send a growing transcript, and per-round
    latency scales with that context. Compacting the oldest turns into a
    summary keeps the recent working set verbatim while bounding context,
    cutting tail latency and the risk of context overflow. Re-queryable
    evidence (tool/file reads) can be searched again from the workspace if
    dropped; user-authored input cannot, so it is exempted from compaction
    (see _partition_messages and issue #60).
    """

    def _partition_messages(self, conversation_messages, cutoff_index):
        """Keep human input verbatim; summarize only re-queryable turns.

        The base split summarizes everything before the cutoff, which includes
        the user's original input. Content the user pasted into the chat is NOT
        re-queryable from the workspace, so summarizing it away loses it
        irrecoverably (issue #60). Move every HumanMessage out of the
        to-summarize set into the preserved set so the subject and the task
        survive compaction; summarize only the re-queryable tool/AI turns.
        """

        to_summarize = conversation_messages[:cutoff_index]
        preserved = conversation_messages[cutoff_index:]
        human_anchors = [
            message
            for message in to_summarize
            if isinstance(message, HumanMessage)
        ]
        if not human_anchors:
            return to_summarize, preserved
        non_human = [
            message
            for message in to_summarize
            if not isinstance(message, HumanMessage)
        ]
        # Anchors are clustered ahead of the preserved tail, not kept in their
        # original positions. The stream stays valid (no orphaned ToolMessages;
        # only HumanMessages move) and the user turns survive verbatim.
        return non_human, human_anchors + preserved

    def _trim_messages_for_summary(self, messages):
        """Trim evidence without requiring human anchors already preserved elsewhere."""

        if self.trim_tokens_to_summarize is None:
            return messages
        return trim_messages(
            messages,
            max_tokens=self.trim_tokens_to_summarize,
            token_counter=self.token_counter,
            strategy="last",
            allow_partial=True,
            include_system=True,
        )

    def _create_summary(self, messages_to_summarize):
        """Validate the synchronous summary before the upstream history replacement."""

        return _validated_summary(super()._create_summary(messages_to_summarize))

    async def _acreate_summary(self, messages_to_summarize):
        """Validate the asynchronous summary before the upstream history replacement."""

        return _validated_summary(await super()._acreate_summary(messages_to_summarize))

    def _invalid_summary(self):
        """Report skipped compaction without reporting evidence as successfully saved."""

        emit = getattr(self, "_emit_event", None)
        if emit is not None:
            emit("deepagents.summarization.failed", {"reason": "invalid_summary"})

    def before_model(self, state, runtime):
        """Summarize on threshold and report what was compacted."""

        before_tokens = self.token_counter(state["messages"])
        try:
            result = super().before_model(state, runtime)
        except InvalidSummaryError:
            self._invalid_summary()
            return None
        self._report_compaction(before_tokens, result)
        return result

    async def abefore_model(self, state, runtime):
        """Apply the same preservation and diagnostics to asynchronous agents."""

        before_tokens = self.token_counter(state["messages"])
        try:
            result = await super().abefore_model(state, runtime)
        except InvalidSummaryError:
            self._invalid_summary()
            return None
        self._report_compaction(before_tokens, result)
        return result

    def _report_compaction(self, before_tokens, result):
        """Report token savings only after a usable summary was generated."""

        emit = getattr(self, "_emit_event", None)
        if result is not None and emit is not None:
            kept = [
                message
                for message in result["messages"]
                if not isinstance(message, RemoveMessage)
            ]
            after_tokens = self.token_counter(kept)
            emit(
                "deepagents.summarization.compacted",
                {
                    "before_tokens": before_tokens,
                    "after_tokens": after_tokens,
                    "saved_tokens": max(before_tokens - after_tokens, 0),
                },
            )


def build_summarization_middleware(
    config,
    model_ref,
    emit_event,
    cancel_event=None,
    run_uuid="",
    http_client=None,
    trace_context=None,
    emit_observation=None,
    trajectory=None,
    *,
    model_class=LensGatewayChatModel,
    middleware_class=LensSummarizationMiddleware,
):
    """Build context-compaction middleware, or None when disabled.

    The summary is produced by a non-streaming gateway model so its tokens
    never leak into the user-facing answer stream. A trigger of 0 disables
    compaction (useful for A/B latency comparisons).

    create_deep_agent also wires its own summarization middleware (default
    trigger ~170k tokens for a profile-less model). Keeping this trigger
    well below that ceiling guarantees ours fires first and holds context
    below the built-in's threshold, so the built-in stays dormant and only
    one summarizer ever acts. Do not raise summary_trigger_tokens near 170k.
    """

    trigger_tokens = config.summary_trigger_tokens
    if trigger_tokens <= 0:
        return None
    context_window = getattr(config, "context_window_tokens", 0)
    trigger_ratio = getattr(config, "summary_trigger_ratio", 0.0)
    window_trigger = (
        int(context_window * trigger_ratio) if context_window > 0 else 0
    )
    if window_trigger > 0:
        trigger_tokens = min(trigger_tokens, window_trigger)
    summary_model = model_class(
        model_ref=str(model_ref),
        ai_gateway_url=config.ai_gateway_url,
        token=config.token,
        request_timeout_s=config.request_timeout_s,
        tls_skip_verify=getattr(config, "tls_skip_verify", False),
        tls_ca_file=getattr(config, "tls_ca_file", None),
        http_client=http_client,
        cancel_event=cancel_event,
        run_uuid=run_uuid,
        trace_context=trace_context or {},
        emit_observation=emit_observation,
        observation_name="summarization",
        trajectory=trajectory,
    )
    middleware = middleware_class(
        model=summary_model,
        trigger=("tokens", trigger_tokens),
        keep=("tokens", config.summary_keep_tokens),
        trim_tokens_to_summarize=32000,
        summary_prompt=CONTINUATION_SUMMARY_PROMPT,
    )
    middleware._emit_event = emit_event
    return middleware
