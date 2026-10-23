"""Agent streaming, wrap-up synthesis, and event emission."""

import json
import logging
import time

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from ..agent_tools import SELF_REPORTING_TOOLS
from ..gateway_model import (
    GatewayStreamError,
    RunCancelledError,
    RunTokenBudgetExhaustedError,
)
from .evidence_gate import _successful_artifact_write
from .messages import (
    extract_streamed_plan_steps as _extract_streamed_plan_steps,
    extract_final_message as _extract_final_message,
    extract_final_answer as _extract_final_answer,
    normalize_plan_steps as _normalize_plan_steps,
    runtime_message,
    tool_call_summary as _tool_call_summary,
)
from .prompts import (
    answer_language_requirement as _answer_language_requirement,
    pick_text as _pick_text,
)
from .system_prompts import _answer_evidence_guidance
from .summarization import ContextCompactionError

LOGGER = logging.getLogger("lensnode")
WRAPUP_MAX_CONTEXT_CHARS = 36000
WRAPUP_SYSTEM_MAX_CHARS = 8000


class EmptyAgentResponseError(RuntimeError):
    """The agent and its one recovery attempt both returned no text."""

    code = "EMPTY_AGENT_RESPONSE"


def _run_agent_with_turn_limit(
    agent,
    messages,
    max_turns,
    model=None,
    thread=None,
    turn_baseline_ai=None,
    event_baseline_ai=None,
    resume_from_checkpoint=False,
    emit_event=None,
    answer_language="English",
    cancel_event=None,
    wrapup_event=None,
    token_budget_wrapup_event=None,
    on_checkpoint_state=None,
    input_checkpoint_seeded=False,
    stream_recovery_attempts=0,
    stream_recovery_backoff_s=1.0,
    stream_recovery_backoff_max_s=8.0,
    on_stream_recovery=None,
    subagent_display_names=None,
    current_question="",
    system_prompt=None,
    execution_evidence=None,
    finalize_deliverables=None,
    on_review_state=None,
):
    """Stream agent events and optionally stop after NEW AI turns.

    `messages` may be prefixed with prior conversation turns. Historical
    assistant turns are excluded from both the turn count and event
    emission, so the limit and trace reflect only the current run.

    On resume, ``turn_baseline_ai`` remains the original history count so
    turns already consumed by this Run still count. ``event_baseline_ai``
    suppresses re-emitting model events already present in the checkpoint.

    A subagent ``task`` delegation runs to completion inside a single
    parent-graph step (deepagents calls it via a plain, synchronous
    ``subagent.invoke(...)``), so the turn count can only be checked
    between parent steps — a delegation in flight when the budget is
    reached can overshoot max_turns by however many turns it used
    internally before returning. This is expected, not a bug in the
    count itself; it is the reason a forced wrap-up (see
    _synthesize_wrapup_answer) matters more than counting precisely.

    Returns (answer, truncated, termination_reason). ``truncated`` is true
    when the agent was stopped before it finished naturally, and the reason
    preserves the gate that requested wrap-up.

    A gateway stream failure may resume from the latest graph checkpoint
    when a thread and a positive recovery budget are supplied. Seen events
    and turn accounting remain in memory so checkpoint replay is not exposed
    as duplicate progress.
    """

    last_state = {"messages": messages} if resume_from_checkpoint else None
    truncated = False
    truncation_reason = None
    seen_tool_calls = set()
    seen_model_calls = set()
    plan_state = {"revision": 0}
    observed_calls = {}
    observed_tools = {}
    user_context = [
        HumanMessage(content=item.get("content", ""))
        for item in messages
        if isinstance(item, dict) and item.get("role") == "user"
    ]
    baseline_ai = (
        turn_baseline_ai
        if turn_baseline_ai is not None
        else sum(1 for m in messages if m.get("role") == "assistant")
    )
    emitted_baseline_ai = (
        event_baseline_ai
        if event_baseline_ai is not None
        else baseline_ai
    )
    seeded_baseline = False

    if resume_from_checkpoint:
        graph_input = None
    elif input_checkpoint_seeded:
        graph_input = {"messages": []}
    else:
        graph_input = {"messages": messages}
    state_stream = _stream_agent_states_with_recovery(
        agent,
        graph_input,
        thread,
        stream_recovery_attempts,
        emit_event,
        on_stream_recovery,
        stream_recovery_backoff_s,
        stream_recovery_backoff_max_s,
    )
    state_stream = _stream_agent_states_with_invalid_tool_recovery(
        agent,
        state_stream,
        thread,
        emit_event,
        stream_recovery_backoff_s,
        stream_recovery_backoff_max_s,
    )
    state_stream = _stream_agent_states_with_plan_updates(
        state_stream,
        model,
        emit_event,
        plan_state,
    )
    budget_exhausted = False
    compaction_failed = False
    delivery_grace_used = False
    delivery_write_grace_used = False
    pending_artifact_write = None
    publication_model_id = None
    if not current_question:
        current_question = next(
            (
                item.get("content", "") if isinstance(item, dict) else item.content
                for item in reversed(messages)
                if (item.get("role") if isinstance(item, dict) else getattr(item, "type", None)) in {"user", "human"}
            ),
            "",
        )

    def _iterate_states():
        nonlocal budget_exhausted, compaction_failed
        try:
            yield from state_stream
        except RunTokenBudgetExhaustedError:
            budget_exhausted = True
        except ContextCompactionError:
            compaction_failed = True

    for state in _iterate_states():
        if cancel_event is not None and cancel_event.is_set():
            raise RunCancelledError(
                "Run was cancelled; stopping the agent loop."
            )
        last_state = state
        if on_review_state is not None:
            on_review_state(state)
        if on_checkpoint_state is not None:
            on_checkpoint_state()
        current = state.get("messages", [])
        _remember_wrapup_tools(current, observed_calls, observed_tools)
        if not seeded_baseline:
            # Seed the historical assistant turns by their (now-assigned)
            # message and tool-call ids so they are never emitted or counted
            # as new turns. Reusing the tool event reducer also restores its
            # plan revision/state without publishing the old events again.
            baseline_messages = []
            ai_count = 0
            for message in current:
                if getattr(message, "type", "") != "ai":
                    continue
                ai_count += 1
                if ai_count <= emitted_baseline_ai:
                    baseline_messages.append(message)
                    seen_model_calls.add(
                        getattr(message, "id", None) or id(message)
                    )
            _emit_new_tool_calls(
                baseline_messages,
                seen_tool_calls,
                lambda _name, _detail: None,
                plan_state=plan_state,
                subagent_display_names=subagent_display_names,
            )
            seeded_baseline = True
        if emit_event is not None:
            _emit_new_model_calls(current, seen_model_calls, emit_event)
            _emit_new_tool_calls(
                current,
                seen_tool_calls,
                emit_event,
                plan_state=plan_state,
                subagent_display_names=subagent_display_names,
            )
        ai_turns = sum(
            1
            for m in current
            if getattr(m, "type", "") == "ai"
        ) - baseline_ai
        if wrapup_event is not None and wrapup_event.is_set():
            truncated = True
            truncation_reason = "soft_deadline"
            if emit_event is not None:
                emit_event("deepagents.agent.soft_deadline", {})
            break
        if (
            token_budget_wrapup_event is not None
            and token_budget_wrapup_event.is_set()
        ):
            if _is_budget_terminal_answer(current, model):
                # Let after-model verification run and the graph finish naturally.
                # A correction or pending tool state still stops at the reserve.
                continue
            latest_ai = next((message for message in reversed(current) if getattr(message, "type", "") == "ai"), None)
            latest_ai_id = (getattr(latest_ai, "id", None) or id(latest_ai)) if latest_ai is not None else None
            if publication_model_id is not None:
                if latest_ai_id == publication_model_id:
                    continue
                publication_model_id = None
            if pending_artifact_write is not None:
                recent = current[-1] if current else None
                pending_id = pending_artifact_write
                pending_artifact_write = None
                if (
                    getattr(recent, "tool_call_id", None) == pending_id
                    and _successful_artifact_write(recent)
                ):
                    # Reserve one model call to publish a successfully written artifact.
                    publication_model_id = latest_ai_id
                    continue
            pending_calls = getattr(current[-1], "tool_calls", None) if current else None
            if (
                not delivery_write_grace_used
                and isinstance(execution_evidence, dict)
                and execution_evidence.get("artifact_required")
                and pending_calls
                and len(pending_calls) == 1
                and pending_calls[0].get("name") in {"write_file", "edit_file", "append_file"}
            ):
                delivery_write_grace_used = True
                pending_artifact_write = pending_calls[0]["id"]
                if emit_event is not None:
                    emit_event("deepagents.agent.delivery_grace", {"stage": "write"})
                continue
            if (
                not delivery_grace_used
                and _has_pending_tool_call(current, "save_deliverable")
            ):
                delivery_grace_used = True
                if emit_event is not None:
                    emit_event("deepagents.agent.delivery_grace", {})
                continue
            truncated = True
            truncation_reason = "token_budget_wrapup"
            if emit_event is not None:
                emit_event("deepagents.agent.token_budget", {})
            break
        if max_turns and max_turns > 0 and ai_turns >= max_turns:
            truncated = True
            truncation_reason = "turn_limit"
            break

    if budget_exhausted:
        # The gateway rejected the next call at the Run budget cap before
        # the local reserve fired. Stop issuing calls and synthesize from
        # the evidence already collected instead of failing the Run.
        truncated = True
        truncation_reason = "token_budget_wrapup"
        if emit_event is not None:
            emit_event("deepagents.agent.token_budget", {})

    if compaction_failed:
        truncated = True
        truncation_reason = "context_compaction_failed"
        if emit_event is not None:
            emit_event("deepagents.agent.context_compaction_failed", {})

    if not truncated and isinstance(execution_evidence, dict) and execution_evidence.get("convergence_exhausted"):
        truncated = True
        truncation_reason = "evidence_convergence_limit"

    wrapup_reason = truncation_reason
    if truncation_reason == "evidence_convergence_limit" and isinstance(execution_evidence, dict):
        wrapup_reason = execution_evidence.get("convergence_reason") or truncation_reason

    if finalize_deliverables is not None:
        finalize_deliverables()

    answer = _extract_final_answer(last_state or {})
    force_wrapup = truncation_reason in {
        "soft_deadline",
        "token_budget_wrapup",
        "turn_limit",
        "evidence_convergence_limit",
        "context_compaction_failed",
    }
    needs_wrapup = force_wrapup or not answer.strip()
    if truncated and model is not None and needs_wrapup:
        # The cutoff landed mid-turn (e.g. right after a tool call, before
        # any answer text), so there is nothing to extract — but the
        # conversation likely still holds real findings from every prior
        # turn. Ask once more, without tools, for a best-effort synthesis
        # instead of discarding all of that work.
        synthesis = _synthesize_wrapup_answer(
            model,
            (last_state or {}).get("messages", []),
            answer_language,
            emit_event,
            current_question=current_question,
            reason=wrapup_reason or "limit",
            system_prompt=system_prompt,
            retained_tools=observed_tools,
            user_context=user_context,
        )
        if synthesis:
            answer = synthesis
    if not truncated and not answer.strip() and model is not None:
        answer = _synthesize_wrapup_answer(
            model,
            (last_state or {}).get("messages", []),
            answer_language,
            emit_event,
            reason="empty",
            current_question=current_question,
            system_prompt=system_prompt,
            retained_tools=observed_tools,
            user_context=user_context,
        )
    if not answer.strip() and (execution_evidence or {}).get("_deliverables"):
        answer = _pick_text(
            "生成的文件已作为本条回复的附件提供。",
            "The generated files are attached to this reply.",
            answer_language,
        )
    if not answer.strip():
        raise EmptyAgentResponseError(
            "Agent returned no answer after one recovery attempt."
        )
    if truncated and answer.strip() and truncation_reason == "soft_deadline":
        answer += _pick_text(
            "\n\n---\n*即将达到硬截止时间，以上回答由当前已有证据"
            "综合生成，调查可能尚未完全完成。*",
            "\n\n---\n*Approaching the hard deadline, this answer was "
            "synthesized from the evidence already collected and the "
            "investigation may be incomplete.*",
            answer_language,
        )
    termination_reason = truncation_reason
    if termination_reason is None and model is not None:
        model_reason = getattr(model, "stop_reason", None)
        if model_reason == "token_budget_wrapup" and _is_budget_terminal_answer(
            (last_state or {}).get("messages", []), model
        ):
            model_reason = None
        if model_reason in {
            "loop_capped",
            "model_length_capped",
            "safety_terminated",
            "token_budget_wrapup",
            "token_capped",
        }:
            termination_reason = model_reason
    return answer, truncated, termination_reason


def _is_budget_terminal_answer(messages, model):
    """Recognize a completed answer while preserving abnormal model stops."""

    if not messages:
        return False
    last = messages[-1]
    metadata = getattr(last, "response_metadata", None) or {}
    return (
        getattr(last, "type", None) == "ai"
        and bool(_message_content_text(last).strip())
        and not getattr(last, "tool_calls", None)
        and not getattr(last, "invalid_tool_calls", None)
        and metadata.get("finish_reason") not in {"length", "content_filter"}
        and getattr(model, "stop_reason", None) in {None, "token_budget_wrapup"}
    )


def _has_pending_tool_call(messages, tool_name):
    """Return whether the latest streamed agent state requests one tool."""

    if not messages:
        return False
    calls = getattr(messages[-1], "tool_calls", None) or []
    return any(
        isinstance(call, dict) and call.get("name") == tool_name
        for call in calls
    )


def _stream_agent_states_with_invalid_tool_recovery(
    agent,
    state_stream,
    thread,
    emit_event,
    backoff_s,
    backoff_max_s,
):
    """Give the model one chance to repair malformed todo JSON."""

    repaired = False
    stream = iter(state_stream)
    while True:
        try:
            state = next(stream)
        except StopIteration:
            return
        yield state
        messages = state.get("messages", [])
        if (
            repaired
            or _extract_final_message(state).strip()
            or not _has_malformed_write_todos(messages)
        ):
            continue
        repaired = True
        if emit_event is not None:
            emit_event(
                "deepagents.tool_call.recovering",
                {"tool": "write_todos", "reason": "invalid_arguments"},
            )
        correction = runtime_message(
            (
                "The previous write_todos call had invalid JSON arguments. "
                "Retry write_todos once using a complete JSON object with a "
                "todos array; do not execute business tools until the call "
                "is valid."
            ),
        )
        if thread is None:
            retry_input = {
                "messages": [
                    message
                    for message in messages
                    if not _is_malformed_tool_message(message)
                ]
                + [correction]
            }
        else:
            retry_input = {"messages": [correction]}
        stream = iter(
            _stream_agent_states_with_recovery(
                agent,
                retry_input,
                thread,
                0,
                emit_event,
                None,
                backoff_s,
                backoff_max_s,
            )
        )


def _has_malformed_write_todos(messages):
    """Return whether messages contain an invalid write_todos call."""

    for message in reversed(messages or []):
        if getattr(message, "type", "") != "ai":
            continue
        for call in getattr(message, "invalid_tool_calls", None) or []:
            name = (
                call.get("name")
                if isinstance(call, dict)
                else getattr(call, "name", None)
            )
            if str(name or "") == "write_todos":
                return True
        return False
    return False


def _is_malformed_tool_message(message):
    """Return whether an AI message contains an invalid tool call."""

    if getattr(message, "type", "") != "ai":
        return False
    return bool(getattr(message, "invalid_tool_calls", None))


def _stream_agent_states_with_recovery(
    agent,
    graph_input,
    thread,
    recovery_attempts,
    emit_event,
    on_recovery,
    backoff_s,
    backoff_max_s,
):
    """Resume a failed gateway stream from the latest graph checkpoint."""

    remaining_attempts = max(0, recovery_attempts)
    max_attempts = remaining_attempts
    attempt = 0

    while True:
        try:
            yield from agent.stream(
                graph_input,
                stream_mode="values",
                config={
                    "recursion_limit": 500,
                    **(thread or {}),
                },
            )
            return
        except GatewayStreamError as exc:
            if (
                exc.code in {
                    "MODEL_INVALID_REQUEST",
                    "MODEL_QUOTA_EXCEEDED",
                    "MODEL_AUTHENTICATION_ERROR",
                    "MODEL_PERMISSION_DENIED",
                    "MODEL_NOT_FOUND",
                }
                or not thread
                or remaining_attempts <= 0
            ):
                raise
            attempt += 1
            remaining_attempts -= 1
            LOGGER.warning(
                "Agent gateway stream failed; resuming checkpoint "
                "(attempt %s/%s, code=%s)",
                attempt,
                max_attempts,
                exc.code,
            )
            if on_recovery is not None:
                on_recovery()
            if emit_event is not None:
                delay_s = min(
                    max(float(backoff_s), 0.0) * (2 ** (attempt - 1)),
                    max(float(backoff_max_s), 0.0),
                )
                emit_event(
                    "deepagents.stream.recovering",
                    {
                        "attempt": attempt,
                        "max_attempts": max_attempts,
                        "code": exc.code,
                        "backoff_s": delay_s,
                    },
                )
            else:
                delay_s = min(
                    max(float(backoff_s), 0.0) * (2 ** (attempt - 1)),
                    max(float(backoff_max_s), 0.0),
                )
            if delay_s > 0:
                LOGGER.info(
                    "Waiting %.1fs before checkpoint stream recovery "
                    "attempt %s/%s.",
                    delay_s,
                    attempt,
                    max_attempts,
                )
                time.sleep(delay_s)
            graph_input = None


def _stream_agent_states_with_plan_updates(
    state_stream,
    model,
    emit_event,
    plan_state,
):
    """Temporarily turn complete streamed todos into plan snapshots."""

    if (
        model is None
        or emit_event is None
        or not hasattr(model, "on_tool_call_delta")
    ):
        yield from state_stream
        return

    previous_callback = model.on_tool_call_delta

    def on_tool_call_delta(delta):
        if isinstance(delta, dict) and delta.get("name") == "write_todos":
            steps = _extract_streamed_plan_steps(delta.get("arguments"))
            if steps:
                _update_plan_state(
                    plan_state,
                    steps,
                    emit_event,
                    finalize=False,
                )
        if callable(previous_callback):
            previous_callback(delta)

    model.on_tool_call_delta = on_tool_call_delta
    try:
        yield from state_stream
    finally:
        if model.on_tool_call_delta is on_tool_call_delta:
            model.on_tool_call_delta = previous_callback


def _strip_dangling_tool_call(messages):
    """Drop a trailing AI message with unresolved tool_calls.

    A truncated loop can stop right after the model requested a tool call
    but before the tool result was recorded. Most providers reject a
    follow-up call whose history ends on a tool_calls message with no
    matching tool response, so that dangling turn is dropped before
    asking for a wrap-up answer — every earlier, resolved turn is kept.
    """

    if not messages:
        return messages
    last = messages[-1]
    if getattr(last, "type", "") == "ai" and getattr(
        last, "tool_calls", None
    ):
        return messages[:-1]
    return messages


def _message_content_text(message):
    """Return plain text for one message in a wrap-up digest."""

    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(content or "")


def _wrapup_excerpt(text, limit):
    """Keep both ends of an excerpt and explicitly mark omitted material."""

    if len(text) <= limit:
        return text
    marker = "\n[Material omitted; excerpt is not complete evidence.]\n"
    remaining = max(0, limit - len(marker))
    head = remaining * 3 // 4
    tail = remaining - head
    return (text[:head] + marker + (text[-tail:] if tail else ""))[:limit]


def _wrapup_coverage(content, depth=0):
    """Extract reported page/read metadata without inferring dataset completeness."""

    if depth > 8:
        return []
    if isinstance(content, str):
        if not content.lstrip().startswith(("{", "[")):
            return []
        try:
            content = json.loads(content)
        except (ValueError, RecursionError):
            return []
    if isinstance(content, list):
        return [item for child in content[:20] for item in _wrapup_coverage(child, depth + 1)]
    if not isinstance(content, dict):
        return []
    fields = (
        "path", "total", "offset", "limit", "has_more", "returned_lines",
        "start_line", "end_line", "result_status", "query_context", "error",
    )
    metadata = {key: content[key] for key in fields if key in content}
    rows = content.get("results")
    if isinstance(rows, list):
        metadata["returned_rows"] = len(rows)
    result = [metadata] if metadata else []
    for key in ("result", "content", "text", "data"):
        if key in content:
            result.extend(_wrapup_coverage(content[key], depth + 1))
    return result


def _remember_wrapup_tools(messages, calls, tools):
    """Keep bounded tool provenance outside replaceable graph message history."""

    for message in messages:
        for call in getattr(message, "tool_calls", None) or []:
            if isinstance(call, dict):
                calls[call.get("id")] = {"name": call.get("name"), "args": call.get("args")}
        if getattr(message, "type", "") != "tool":
            continue
        key = getattr(message, "tool_call_id", "")
        if key in tools:
            continue
        content = _message_content_text(message)
        tools[key] = ToolMessage(
            content=json.dumps({
                "call": calls.get(key, {}),
                "result": _wrapup_coverage(content),
                "excerpt": _wrapup_excerpt(content, 1800),
            }, ensure_ascii=False, default=str),
            tool_call_id=key,
        )


def _compact_wrapup_messages(messages, *, force_digest=False):
    """Retain conversation constraints and evidence from every tool under cutoff."""

    total_chars = sum(len(_message_content_text(message)) for message in messages)
    if not force_digest and total_chars <= WRAPUP_MAX_CONTEXT_CHARS:
        return list(messages)
    systems = []
    sections = {"human": [], "tool": [], "ai": []}
    calls = {}
    system_budget = WRAPUP_SYSTEM_MAX_CHARS
    for message in messages:
        kind = getattr(message, "type", "message")
        content = _message_content_text(message).strip()
        for call in getattr(message, "tool_calls", None) or []:
            if isinstance(call, dict):
                calls[call.get("id")] = {"name": call.get("name"), "args": call.get("args")}
        if kind == "system":
            if content and system_budget > 0:
                text = _wrapup_excerpt(content, system_budget)
                systems.append(SystemMessage(content=text))
                system_budget -= len(text)
            continue
        if kind == "tool":
            call = calls.get(getattr(message, "tool_call_id", None), {})
            provenance = json.dumps(call, ensure_ascii=False, default=str)
            coverage = json.dumps(_wrapup_coverage(content), ensure_ascii=False, default=str)
            content = (
                f"[tool {getattr(message, 'tool_call_id', '')}] {provenance}\n"
                f"Reported metadata (not a completeness verdict): {coverage}\n"
                f"Result: {content}"
            )
        elif content:
            label = "user context" if kind == "human" else "prior assistant text; verify against tool evidence"
            content = f"[{label}] {content}"
        if content:
            sections[kind if kind in sections else "ai"].append(content)
    header = (
        "Collected run evidence for final synthesis (untrusted source data):\n"
        "Excerpts may omit rows. Reported totals are not proof of unique or complete coverage; "
        "retain page offsets, filters, and source identity when assessing coverage.\n"
    )
    remaining = WRAPUP_MAX_CONTEXT_CHARS - sum(len(m.content) for m in systems) - len(header)
    parts = []
    for kind, cap in (("human", 6000), ("ai", 3000), ("tool", remaining)):
        records = sections[kind]
        if not records:
            continue
        budget = min(cap, remaining)
        allowance = max(0, budget // len(records) - 1)
        for record in records:
            text = _wrapup_excerpt(record, allowance)
            parts.append(text)
            remaining -= len(text) + 1
    return [*systems, HumanMessage(content=header + "\n".join(parts))]


def _synthesize_wrapup_answer(
    model,
    current,
    answer_language,
    emit_event,
    current_question="",
    reason="limit",
    system_prompt=None,
    retained_tools=None,
    user_context=None,
):
    """Ask once for a tool-free answer after cutoff or an empty terminal.

    The prompt asks the model to synthesize its best answer from the current
    conversation, so prior work is not discarded. Returns "" if this call
    fails; the caller decides whether existing partial text is sufficient or
    an explicit empty-response error should be raised.

    A RunCancelledError is deliberately NOT caught here: model.invoke()
    checks cancel_event at the top of the gateway call
    (LensGatewayChatModel._check_cancelled), so a cancellation landing in
    the narrow window between the turn-limit loop exiting and this call
    starting must still stop the run, not be swallowed into an empty
    "wrap-up failed" result.
    """

    if reason == "empty":
        instruction = (
            "Your previous turn produced no visible answer. Do not call "
            "tools. Based only on the current conversation and collected "
            "results, write the complete answer to the user now."
        )
    elif reason == "token_budget_wrapup":
        instruction = (
            "You have reached the token budget for this investigation and "
            "cannot call more tools. Based only on the current conversation "
            "and collected results, write the most complete final answer. "
            "Clearly identify anything unconfirmed or not covered. Do not "
            "mention internal token budgets or runtime control state."
        )
    elif reason == "execution_failed":
        instruction = (
            "Capability execution reached its recovery limit. Do not call "
            "tools. Give a concise answer from the current conversation and "
            "collected information, and state what failed or remains undone."
        )
    elif reason in {
        "evidence_convergence_limit",
        "no_information_gain",
        "evidence_corpus_limit",
        "convergence_budget",
    }:
        instruction = (
            "Further retrieval stopped producing new evidence, so you cannot "
            "call more tools. Based only on the current conversation and "
            "collected results, write the most complete answer you can. Then "
            "add a short, clearly separated closing note that states: (1) which "
            "parts of the request are confirmed by evidence, (2) which parts "
            "could not be confirmed and why (no matching results, insufficient "
            "access, or a source corpus too large to finish), and (3) what "
            "narrower query or additional access would settle the open parts. "
            "Do not mention internal runtime controls or budgets."
        )
    else:
        instruction = (
            "You have reached the step limit for this analysis and cannot "
            "call any more tools. Based on everything you have gathered so "
            "far, write the most complete answer you can now. Clearly note "
            "any part of the investigation you were not able to confirm."
        )
    instruction = (
        f"{instruction}\n\n"
        "The host automatically validates and stages final files in /deliverables/ after execution. "
        "Do not claim that a file elsewhere in private scratch is a downloadable attachment. "
        "Do not invent download links or claim that rejected or failed uploads succeeded.\n\n"
        f"{_answer_evidence_guidance()}\n"
        "These are internal runtime controls, not a conversational user request. "
        "The final user message repeats the original request. Determine the "
        "answer language from that request and its explicit language instructions, "
        "not from internal controls or collected evidence.\n\n"
        f"{_answer_language_requirement(answer_language)}"
    )
    wrapup_history = _strip_dangling_tool_call(current)
    if reason == "empty":
        wrapup_history = _filter_recovery_history(wrapup_history)
    if not current_question:
        current_question = next(
            (message.content for message in current if getattr(message, "type", None) == "human"),
            "",
        )
    current_tool_ids = {
        getattr(message, "tool_call_id", "")
        for message in wrapup_history
        if getattr(message, "type", "") == "tool"
    }
    missing_tools = [message for key, message in (retained_tools or {}).items() if key not in current_tool_ids]
    current_text = {_message_content_text(message) for message in wrapup_history}
    missing_context = [message for message in user_context or [] if _message_content_text(message) not in current_text]
    wrapup_messages = _compact_wrapup_messages(
        [*missing_context, *missing_tools, *wrapup_history], force_digest=bool(missing_tools),
    )
    if system_prompt:
        wrapup_messages = [
            SystemMessage(content=system_prompt),
            *[message for message in wrapup_messages if getattr(message, "type", None) != "system"],
        ]
    wrapup_messages.append(SystemMessage(content=instruction))
    if current_question:
        wrapup_messages.append(HumanMessage(content=current_question))
    if emit_event is not None:
        event = (
            "deepagents.answer.recovery"
            if reason == "empty"
            else "deepagents.agent.wrapup"
        )
        emit_event(event, {})
    try:
        response = model.invoke(
            wrapup_messages,
            runtime_final_synthesis=True,
            reasoning_effort="none",
        )
    except RunCancelledError:
        raise
    except Exception:
        LOGGER.exception("Wrap-up synthesis call failed after truncation")
        return ""
    content = getattr(response, "content", None)
    if isinstance(content, str):
        return content.strip()
    return str(content or "").strip()


def _filter_recovery_history(messages):
    """Exclude synthetic control turns from empty-answer synthesis."""

    filtered = []
    for message in messages or []:
        message_type = getattr(message, "type", "")
        content = _message_content_text(message).strip().lower()
        if message_type == "human" and (
            "previous turn produced no visible answer" in content
            or "上一轮没有输出可见答案" in content
        ):
            continue
        if message_type == "ai" and (
            "direct answer mode" in content
            or "不能调用工具" in content
            or "禁止调用工具" in content
        ):
            continue
        if (
            message_type == "ai"
            and getattr(message, "invalid_tool_calls", None)
            and not getattr(message, "tool_calls", None)
            and not content
        ):
            continue
        filtered.append(message)
    return filtered


def _model_summary(message, limit=160):
    """Return a short preview of a model turn for the trace.

    Prefers the assistant's own text; when the turn only issued tool
    calls (no text), shows the tools it decided to call instead.
    """

    content = getattr(message, "content", "")
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                parts.append(str(block.get("text") or ""))
            else:
                parts.append(str(block))
        content = " ".join(parts)
    text = " ".join(str(content or "").split())
    if text:
        return text[:limit] + ("…" if len(text) > limit else "")
    calls = [
        call.get("name")
        for call in (getattr(message, "tool_calls", None) or [])
        if call.get("name")
    ]
    if calls:
        ordered, counts = [], {}
        for name in calls:
            if name not in counts:
                ordered.append(name)
            counts[name] = counts.get(name, 0) + 1
        parts = [
            f"{name}×{counts[name]}" if counts[name] > 1 else name
            for name in ordered
        ]
        return "→ " + ", ".join(parts)
    return ""


def _emit_new_model_calls(messages, seen, emit_event):
    """Emit an event for each new AI response (one LLM round).

    Each AI message is one round-trip to the model. The gateway returns
    token usage in the message's response_metadata, so surfacing these
    makes every LLM call visible in the trace and attributes token usage
    to the run. Dedup keys on the stable message id rather than position,
    so summarization (which rewrites the message list) cannot make a new
    final turn collide with an already-seen positional index.
    """

    for message in messages:
        if getattr(message, "type", "") != "ai":
            continue
        key = getattr(message, "id", None) or id(message)
        if key in seen:
            continue
        seen.add(key)
        meta = getattr(message, "response_metadata", None) or {}
        usage = meta.get("usage") or {}
        emit_event(
            "llm.response",
            {
                "round": len(seen),
                "model": usage.get("model"),
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "total_tokens": usage.get("total_tokens"),
                "cached_tokens": (
                    usage.get("cached_tokens")
                    or usage.get("prompt_cache_hit_tokens")
                    or 0
                ),
                "reasoning_tokens": usage.get("reasoning_tokens") or 0,
                "cost": usage.get("cost"),
                "latency_ms": meta.get("latency_ms"),
                "finish_reason": meta.get("finish_reason"),
                "stop_reason": _response_stop_reason(meta),
                "run_token_usage": meta.get("run_token_usage"),
                "summary": _model_summary(message),
            },
        )


def _response_stop_reason(metadata):
    """Return the normalized runtime stop reason for one model response."""

    for key, reason in (
        ("token_capped", "token_capped"),
        ("loop_capped", "loop_capped"),
        ("safety_terminated", "safety_terminated"),
        ("model_length_capped", "model_length_capped"),
    ):
        if metadata.get(key):
            return reason
    return None


def _merge_plan_statuses(existing_steps, incoming_steps):
    """Keep finalized plan titles while applying incoming step statuses."""

    incoming_by_id = {item["id"]: item for item in incoming_steps}
    return [
        {
            **item,
            "status": incoming_by_id.get(item["id"], item)["status"],
        }
        for item in existing_steps
    ]


def _update_plan_state(
    state,
    incoming_steps,
    emit_event,
    *,
    finalize,
):
    """Publish one changed plan snapshot and optionally finalize its shape."""

    existing_steps = state.get("steps") or []
    if state.get("initial_plan_finalized"):
        steps = _merge_plan_statuses(existing_steps, incoming_steps)
    else:
        steps = incoming_steps
    if finalize:
        state["initial_plan_finalized"] = True
    if steps == existing_steps:
        return False

    state["revision"] = int(state.get("revision") or 0) + 1
    state["steps"] = steps
    emit_event(
        "workflow.plan.updated",
        {
            "event_type": "plan.updated",
            "visibility": "user",
            "payload": {
                "revision": state["revision"],
                "steps": steps,
            },
        },
    )
    return True


def _emit_new_tool_calls(
    messages,
    seen,
    emit_event,
    *,
    plan_state=None,
    subagent_display_names=None,
):
    """Emit a progress event for each not-yet-seen agent tool call.

    The built-in workspace tools emit their own start/done events, but
    the Deep Agent loop also calls model-driven tools (write_file, ls,
    task delegation, MCP tools) that are otherwise invisible. Surfacing
    every tool call here lets the frontend show real progress instead of
    a frozen status during long turns.
    """

    for message in messages:
        if getattr(message, "type", "") != "ai":
            continue
        for call in getattr(message, "tool_calls", None) or []:
            call_id = call.get("id") or ""
            if not call_id or call_id in seen:
                continue
            seen.add(call_id)
            name = call.get("name") or "tool"
            if name == "write_todos":
                state = plan_state if plan_state is not None else {
                    "revision": 0
                }
                todos = (call.get("args") or {}).get("todos") or []
                incoming_steps = _normalize_plan_steps(todos)
                _update_plan_state(
                    state,
                    incoming_steps,
                    emit_event,
                    finalize=True,
                )
                if (
                    todos
                    and all(
                        item.get("status") == "completed" for item in todos
                    )
                    and not state.get("answering_phase_emitted")
                ):
                    state["answering_phase_emitted"] = True
                    state["execution_phase_emitted"] = True
                    emit_event(
                        "workflow.phase.changed",
                        {
                            "event_type": "phase.changed",
                            "visibility": "user",
                            "payload": {"phase": "answering"},
                        },
                    )
                continue
            if (
                plan_state is not None
                and not plan_state.get("execution_phase_emitted")
            ):
                plan_state["execution_phase_emitted"] = True
                emit_event(
                    "workflow.phase.changed",
                    {
                        "event_type": "phase.changed",
                        "visibility": "user",
                        "payload": {"phase": "executing"},
                    },
                )
            if name in SELF_REPORTING_TOOLS:
                # avoids a duplicate trace line; the tool emits its own
                # .start/.done with a richer argument summary
                continue
            detail = {"tool": name, "summary": _tool_call_summary(call)}
            if name == "task":
                subagent_name = str(
                    (call.get("args") or {}).get("subagent_type") or ""
                )
                display_name = (subagent_display_names or {}).get(
                    subagent_name,
                    subagent_name,
                )
                if display_name:
                    detail["assistant_name"] = display_name[:160]
            emit_event(f"tool.{name}.invoke", detail)
