"""Agent streaming, wrap-up synthesis, and event emission."""

import json
import logging
import time

from langchain_core.messages import HumanMessage, SystemMessage

from ..agent_tools import SELF_REPORTING_TOOLS
from ..gateway_model import GatewayStreamError, RunCancelledError
from .messages import (
    extract_streamed_plan_steps as _extract_streamed_plan_steps,
    extract_final_message as _extract_final_message,
    normalize_plan_steps as _normalize_plan_steps,
    tool_call_summary as _tool_call_summary,
)
from .prompts import (
    answer_language_requirement as _answer_language_requirement,
    pick_text as _pick_text,
)

LOGGER = logging.getLogger("lensnode")
WRAPUP_MAX_CONTEXT_CHARS = 36000
WRAPUP_SYSTEM_MAX_CHARS = 8000
WRAPUP_ITEM_MAX_CHARS = 1200


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
    for state in state_stream:
        if cancel_event is not None and cancel_event.is_set():
            raise RunCancelledError(
                "Run was cancelled; stopping the agent loop."
            )
        last_state = state
        if on_checkpoint_state is not None:
            on_checkpoint_state()
        current = state.get("messages", [])
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
            truncated = True
            truncation_reason = "token_budget_wrapup"
            if emit_event is not None:
                emit_event("deepagents.agent.token_budget", {})
            break
        if max_turns and max_turns > 0 and ai_turns >= max_turns:
            truncated = True
            truncation_reason = "turn_limit"
            break

    answer = _extract_final_message(last_state or {})
    force_wrapup = truncation_reason in {
        "soft_deadline",
        "token_budget_wrapup",
        "turn_limit",
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
            current_question=next(
                (
                    str(item.get("content") or "")
                    for item in reversed(messages)
                    if item.get("role") == "user"
                ),
                "",
            ),
            reason=truncation_reason or "limit",
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
        )
    if not answer.strip():
        raise EmptyAgentResponseError(
            "Agent returned no answer after one recovery attempt."
        )
    if truncated and answer.strip():
        if truncation_reason == "soft_deadline":
            answer += _pick_text(
                "\n\n---\n*即将达到硬截止时间，以上回答由当前已有证据"
                "综合生成，调查可能尚未完全完成。*",
                "\n\n---\n*Approaching the hard deadline, this answer was "
                "synthesized from the evidence already collected and the "
                "investigation may be incomplete.*",
                answer_language,
            )
        else:
            answer += _pick_text(
                "\n\n---\n*已达到当前执行安全边界，本次调查未完全完成。"
                "可从已保存的检查点继续执行。*",
                "\n\n---\n*Reached the current execution safety boundary before "
                "the investigation fully completed. Retry to continue "
                "from the saved checkpoint.*",
                answer_language,
            )
    termination_reason = truncation_reason
    if termination_reason is None and model is not None:
        model_reason = getattr(model, "stop_reason", None)
        if model_reason in {
            "loop_capped",
            "model_length_capped",
            "safety_terminated",
            "token_budget_wrapup",
            "token_capped",
        }:
            termination_reason = model_reason
    return answer, truncated, termination_reason


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
        correction = HumanMessage(
            content=(
                "The previous write_todos call had invalid JSON arguments. "
                "Retry write_todos once using a complete JSON object with a "
                "todos array; do not execute business tools until the call "
                "is valid."
            ),
            additional_kwargs={"hide_from_ui": True},
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
            if not thread or remaining_attempts <= 0:
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
    """Return bounded plain text for one message in a wrap-up digest."""

    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(content or "")


def _compact_wrapup_messages(messages):
    """Collapse a long agent history into a bounded evidence digest."""

    if sum(len(_message_content_text(message)) for message in messages) <= (
        WRAPUP_MAX_CONTEXT_CHARS
    ):
        return list(messages)
    system_messages = []
    evidence_parts = []
    for message in messages:
        message_type = getattr(message, "type", "message")
        content = _message_content_text(message).strip()
        if message_type == "system":
            if content:
                system_messages.append(
                    SystemMessage(
                        content=content[:WRAPUP_SYSTEM_MAX_CHARS]
                    )
                )
            continue
        tool_calls = getattr(message, "tool_calls", None) or []
        tool_names = ", ".join(
            str(call.get("name") or "tool")
            for call in tool_calls
            if isinstance(call, dict)
        )
        label = message_type
        if tool_names:
            label = f"{label} ({tool_names})"
        if content:
            evidence_parts.append(
                f"[{label}] {content[:WRAPUP_ITEM_MAX_CHARS]}"
            )
    evidence = "\n".join(evidence_parts)
    if len(evidence) > WRAPUP_MAX_CONTEXT_CHARS:
        evidence = (
            "[Earlier evidence omitted to keep the final context bounded.]\n"
            + evidence[-WRAPUP_MAX_CONTEXT_CHARS:]
        )
    digest = HumanMessage(
        content=(
            "Collected run evidence for final synthesis:\n"
            f"{evidence or '[no evidence text]'}"
        )
    )
    return [*system_messages, digest]


def _synthesize_wrapup_answer(
    model,
    current,
    answer_language,
    emit_event,
    current_question="",
    reason="limit",
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
        instruction = _pick_text(
            "你上一轮没有输出可见答案。不要调用任何工具，请仅基于当前对话"
            "和已取得的结果，直接向用户给出完整答案。",
            "Your previous turn produced no visible answer. Do not call "
            "tools. Based only on the current conversation and collected "
            "results, write the complete answer to the user now.",
            answer_language,
        )
    elif reason == "token_budget_wrapup":
        instruction = _pick_text(
            "你已经达到本次调查的 Token 预算，不能再调用任何工具。请仅基于"
            "当前对话和已取得的结果，直接给出最完整的最终答案；未确认或未覆盖"
            "的部分必须明确说明。不要向用户提及内部 Token 预算或运行控制状态。",
            "You have reached the token budget for this investigation and "
            "cannot call more tools. Based only on the current conversation "
            "and collected results, write the most complete final answer. "
            "Clearly identify anything unconfirmed or not covered. Do not "
            "mention internal token budgets or runtime control state.",
            answer_language,
        )
    elif reason == "execution_failed":
        instruction = _pick_text(
            "能力执行已达到恢复上限，不能再调用任何工具。请仅基于当前"
            "对话和已取得的信息给出简洁回答，并明确说明执行失败及未完成"
            "的部分。",
            "Capability execution reached its recovery limit. Do not call "
            "tools. Give a concise answer from the current conversation and "
            "collected information, and state what failed or remains undone.",
            answer_language,
        )
    else:
        instruction = _pick_text(
            "你已经达到本次分析的步数上限，不能再调用任何工具。请基于目前"
            "为止已经掌握的全部信息，直接给出你能给出的最完整回答。如果"
            "调查还有尚未确认或未覆盖到的部分，请明确说明。",
            "You have reached the step limit for this analysis and cannot "
            "call any more tools. Based on everything you have gathered so "
            "far, write the most complete answer you can now. Clearly note "
            "any part of the investigation you were not able to confirm.",
            answer_language,
        )
    current_question_hint = ""
    if current_question:
        current_question_hint = _pick_text(
            f"当前需要回答的问题是：{current_question}",
            f"The question to answer now is: {current_question}",
            answer_language,
        )
        current_question_hint += "\n\n"
    instruction = (
        f"{instruction}\n\n"
        f"{current_question_hint}"
        f"{_answer_language_requirement(answer_language)}"
    )
    wrapup_history = _strip_dangling_tool_call(current)
    if reason == "empty":
        wrapup_history = _filter_recovery_history(wrapup_history)
    wrapup_messages = _compact_wrapup_messages(wrapup_history) + [
        HumanMessage(content=instruction)
    ]
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
