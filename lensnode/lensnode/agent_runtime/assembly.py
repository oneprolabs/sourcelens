"""Deep Agent middleware and subagent assembly."""

from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT
from langchain.agents.middleware import TodoListMiddleware

from .restrictions import NoTaskMiddleware as _NoTaskMiddleware
from .system_prompts import _is_general_chat


def _agent_middleware(
    command,
    summarizer,
    emit_event=None,
    *,
    capability_middleware=None,
    mcp_middleware=None,
    trace_middleware=None,
    evidence_middleware=None,
    runtime_middleware=(),
    allow_task_tool=False,
):
    """Return task-specific middleware for one Deep Agent run."""

    # deepagents 0.7 dropped its default TodoListMiddleware, but the run
    # depends on the write_todos tool: the capability boundary requires an
    # initial plan before any business tool. Restore it explicitly.
    middleware = [TodoListMiddleware()]
    if summarizer is not None:
        middleware.append(summarizer)
    middleware.extend(runtime_middleware)
    if _is_general_chat(command):
        middleware.append(
            _NoTaskMiddleware(
                emit_event,
                allow_task_tool=allow_task_tool,
            )
        )
        if capability_middleware is not None:
            middleware.append(capability_middleware)
    if evidence_middleware is not None:
        middleware.append(evidence_middleware)
    if trace_middleware is not None:
        middleware.append(trace_middleware)
    if mcp_middleware is not None:
        middleware.append(mcp_middleware)
    return middleware


def _fast_subagent(
    mcp_middleware=None,
    trace_middleware=None,
    runtime_middleware=(),
):
    """General-purpose subagent that parallelizes its own tool calls.

    By default a delegated subagent runs deepagents' stock prompt and
    tends to do serial ReAct (one file at a time) — the main reason a
    subtask is slow. Overriding the same-named general-purpose subagent
    and prepending the parallel guidance makes it batch its reads and
    searches like the main agent. Tools and model are inherited from the
    parent (tools default to the parent's set).
    """

    parallel = (
        "Work in parallel whenever steps are independent — this is the "
        "biggest lever on speed. When several files or searches are "
        "needed, issue those tool calls together in one message so they "
        "run concurrently; do not read and validate hits one at a time. "
        "Keep the number of parallel calls reasonable.\n\n"
    )
    subagent = {
        **GENERAL_PURPOSE_SUBAGENT,
        "system_prompt": parallel + GENERAL_PURPOSE_SUBAGENT["system_prompt"],
    }
    middleware = [TodoListMiddleware()]
    middleware.extend(
        item
        for item in (
            trace_middleware,
            mcp_middleware,
        )
        if item is not None
    )
    middleware.extend(runtime_middleware)
    subagent["middleware"] = middleware
    return subagent
