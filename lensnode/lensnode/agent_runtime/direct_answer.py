"""Direct-answer execution and promise recovery for General Chat."""

import re

from langchain_core.messages import SystemMessage

from .messages import build_initial_messages as _build_initial_messages
from .prompts import (
    command_answer_language as _command_answer_language,
    pick_text as _pick_text,
)
_UNFULFILLED_ACTION_PROMISE = re.compile(
    r"(?:^|[\n。！？.!?]\s*)(?:"
    r"让我(?:先|重新|开始)(?:把)?"
    r"(?:尝试|执行|登录|认证|身份验证|查询|读取|获取|拉取|重试)|"
    r"我(?:先|现在开始|马上|接下来)(?:把)?"
    r"(?:完成|进行|执行|登录|认证|身份验证|查询|读取|获取|拉取|重试)|"
    r"接下来(?:我)?(?:先|会|将|开始)(?:把)?"
    r"(?:执行|登录|认证|身份验证|查询|读取|获取|拉取|重试)|"
    r"let me (?:first )?"
    r"(?:start|retry|fetch|query|authenticate|continue)|"
    r"i(?:'ll| will) (?:first |now |next )?"
    r"(?:start|retry|fetch|query|authenticate|continue)|"
    r"next,? i(?:'ll| will) (?:start|retry|fetch|query|authenticate)"
    r")",
    re.IGNORECASE,
)


def _contains_unfulfilled_action_promise(content):
    """Return whether a final draft promises work it did not perform."""

    paragraphs = [
        item.strip()
        for item in re.split(r"\n\s*\n", str(content or ""))
        if item.strip()
    ]
    tail = paragraphs[-1][-600:] if paragraphs else ""
    return bool(_UNFULFILLED_ACTION_PROMISE.search(tail))


def _answer_general_chat_directly(
    model,
    command,
    system_prompt,
    messages=None,
    emit_event=None,
    emit_output=None,
    stream=False,
):
    """Answer a simple informational request without creating an agent.

    When ``stream`` is set and an ``emit_output`` sink is given, the draft —
    and any promise-recovery answer — is published token-by-token. The draft
    is only validated for unfulfilled action promises after it is generated,
    so a rejected draft is retracted with a reset before the corrected answer
    streams, letting the user see the fast first draft get replaced instead of
    waiting for the whole check to finish.
    """

    direct_prompt = (
        f"{system_prompt}\n\nRuntime route: direct_answer. Do not call any "
        "tools. Answer the user directly and concisely from the conversation "
        "and loaded Skill instructions already present in this prompt. State "
        "the conclusion before conditions or explanation. Never promise to "
        "start, retry, or continue work after this answer."
    )
    messages = [
        SystemMessage(content=direct_prompt),
        *(
            list(messages)
            if messages is not None
            else _build_initial_messages(
                command.get("history"),
                command.get("question", ""),
                command.get("image_data_urls"),
            )
        ),
    ]
    streaming = bool(stream and emit_output is not None)
    invoke_kwargs = {"runtime_control_call": True}
    if streaming:
        invoke_kwargs["runtime_stream_control"] = True
    response = model.invoke(messages, **invoke_kwargs)
    content = getattr(response, "content", None)
    answer = (
        content.strip() if isinstance(content, str) else str(content or "")
    )
    if _contains_unfulfilled_action_promise(answer):
        if emit_event is not None:
            emit_event("deepagents.answer.promise_recovery", {})
        if streaming:
            emit_output("", reset=True)
        language = _command_answer_language(command)
        correction = _pick_text(
            "上一版回答以尚未执行的行动承诺收尾。不要调用工具，也不要描述"
            "接下来要做什么。请直接回答用户当前的问题，先给明确结论，再说明"
            "条件、限制或需要用户确认的事项。",
            "The previous draft ended with a promise of work that was not "
            "performed. Do not call tools or describe future actions. Answer "
            "the current question directly: give the conclusion first, then "
            "state conditions, limitations, or needed confirmation.",
            language,
        )
        recovery_messages = [
            *messages,
            {"role": "assistant", "content": answer},
            {"role": "user", "content": correction},
        ]
        response = model.invoke(recovery_messages, **invoke_kwargs)
        content = getattr(response, "content", None)
        answer = (
            content.strip()
            if isinstance(content, str)
            else str(content or "")
        )
        if _contains_unfulfilled_action_promise(answer):
            if streaming:
                emit_output("", reset=True)
            answer = _pick_text(
                "本轮没有执行任何工具，也无法确认所描述的后续操作已经开始。"
                "请明确您是只询问可行性，还是希望继续执行该操作。",
                "No tool was executed in this turn, and the described next "
                "step was not started. Please clarify whether you only want "
                "a feasibility answer or want the operation to continue.",
                language,
            )
            if streaming and answer:
                emit_output(answer)
    if emit_output is not None and answer and not streaming:
        emit_output(answer)
    return answer
