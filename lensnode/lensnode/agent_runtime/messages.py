"""Pure message and event helpers for the LensNode agent runtime."""

import json


MAX_STREAMED_PLAN_ARGUMENT_CHARS = 262_144


def extract_final_message(response):
    """Extract final assistant content from a Deep Agents response."""

    if not isinstance(response, dict):
        return str(response).strip()
    messages = response.get("messages") or []
    if not messages:
        return ""
    last = messages[-1]
    content = getattr(last, "content", None)
    if isinstance(content, str):
        return content.strip()
    return str(content or "").strip()


def detail_lines(detail):
    """Convert an event detail dict to normalized log lines."""

    if not detail:
        return None
    return [
        f"{title_key(key)}: {value}"
        for key, value in detail.items()
    ]


def title_key(value):
    """Return a compact TitleCase log key."""

    return "".join(part.capitalize() for part in str(value).split("_"))


def activity_from_event(event):
    """Return a compact frontend activity name for an agent event."""

    if event.startswith("resources."):
        return "loading_resources"
    if event.startswith("tool."):
        return "running_tool"
    if event.endswith(".invoke"):
        return "thinking"
    if event.endswith(".done"):
        return "completed"
    return "running"


def build_initial_messages(history, question, image_data_urls=None):
    """Build history and the current question with optional images."""

    messages = []
    if not image_data_urls:
        for item in history or []:
            role = item.get("role")
            content = item.get("content")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})
    content = question
    if image_data_urls:
        content = [{"type": "text", "text": question}]
        content.extend(
            {
                "type": "image_url",
                "image_url": {"url": data_url},
            }
            for data_url in image_data_urls
        )
    messages.append({
        "role": "user",
        "content": content,
    })
    return messages


def normalize_plan_steps(todos):
    """Return a bounded user-visible view of Deep Agents todos."""

    if not isinstance(todos, list):
        return []
    steps = []
    allowed_statuses = {"pending", "in_progress", "completed"}
    for index, item in enumerate(todos[:12], start=1):
        if not isinstance(item, dict):
            continue
        title = str(item.get("content") or item.get("title") or "").strip()
        if not title:
            continue
        status = str(item.get("status") or "pending")
        if status not in allowed_statuses:
            status = "pending"
        steps.append(
            {
                "id": f"step-{index}",
                "title": title[:240],
                "status": status,
            }
        )
    return steps


def _find_todos_array_start(arguments):
    """Return the first character inside the top-level todos array."""

    decoder = json.JSONDecoder()
    object_depth = 0
    array_depth = 0
    index = 0
    while index < len(arguments):
        char = arguments[index]
        if char == '"':
            try:
                value, end = decoder.raw_decode(arguments, index)
            except (TypeError, ValueError):
                return None
            if (
                object_depth == 1
                and array_depth == 0
                and value == "todos"
            ):
                cursor = end
                while cursor < len(arguments) and arguments[cursor].isspace():
                    cursor += 1
                if cursor >= len(arguments) or arguments[cursor] != ":":
                    index = end
                    continue
                cursor += 1
                while cursor < len(arguments) and arguments[cursor].isspace():
                    cursor += 1
                if cursor < len(arguments) and arguments[cursor] == "[":
                    return cursor + 1
            index = end
            continue
        if char == "{":
            object_depth += 1
        elif char == "}":
            object_depth = max(0, object_depth - 1)
        elif char == "[":
            array_depth += 1
        elif char == "]":
            array_depth = max(0, array_depth - 1)
        index += 1
    return None


def extract_streamed_plan_steps(arguments):
    """Extract fully closed todo objects from partial JSON arguments."""

    if not isinstance(arguments, str) or not arguments:
        return []
    if len(arguments) > MAX_STREAMED_PLAN_ARGUMENT_CHARS:
        return []

    try:
        payload = json.loads(arguments)
    except (TypeError, ValueError):
        payload = None
    if isinstance(payload, dict):
        return normalize_plan_steps(payload.get("todos"))

    array_start = _find_todos_array_start(arguments)
    if array_start is None:
        return []

    decoder = json.JSONDecoder()
    todos = []
    cursor = array_start
    while cursor < len(arguments) and len(todos) < 12:
        while cursor < len(arguments) and (
            arguments[cursor].isspace() or arguments[cursor] == ","
        ):
            cursor += 1
        if cursor >= len(arguments) or arguments[cursor] == "]":
            break
        if arguments[cursor] != "{":
            break
        try:
            item, end = decoder.raw_decode(arguments, cursor)
        except (TypeError, ValueError):
            break
        if not isinstance(item, dict):
            break
        todos.append(item)
        cursor = end
    return normalize_plan_steps(todos)


def tool_call_summary(call):
    """Return a short human summary of a tool call's arguments."""

    args = call.get("args") or {}
    if not isinstance(args, dict):
        return ""
    for key in ("path", "file_path", "query", "description", "ref"):
        value = args.get(key)
        if value:
            return str(value)[:120]
    return ""
