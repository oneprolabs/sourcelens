from lensnode.agent_runtime.direct_answer import _answer_general_chat_directly
from lensnode.agent_runtime.messages import (
    build_initial_messages,
    extract_final_answer,
    extract_final_message,
    extract_streamed_plan_steps,
)


def test_extract_final_answer_accepts_assistant_message():
    from langchain_core.messages import AIMessage

    state = {"messages": [AIMessage(content="the answer")]}

    assert extract_final_answer(state) == "the answer"


def test_extract_final_answer_never_returns_tool_output():
    from langchain_core.messages import AIMessage, ToolMessage

    state = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "search_workspace", "args": {}, "id": "c1"}
                ],
            ),
            ToolMessage(content='{"mode": "files", "files": []}', tool_call_id="c1"),
        ]
    }

    # Raw tool output must not surface as the answer; the caller synthesizes.
    assert extract_final_answer(state) == ""
    assert extract_final_message(state) == '{"mode": "files", "files": []}'


def test_extract_final_answer_accepts_dict_assistant_message():
    state = {"messages": [{"role": "assistant", "content": "dict answer"}]}

    assert extract_final_answer(state) == "dict answer"


def test_extract_final_answer_handles_empty_state():
    assert extract_final_answer({"messages": []}) == ""
    assert extract_final_answer({}) == ""



def test_build_initial_messages_includes_current_images():
    messages = build_initial_messages(
        [],
        "What is wrong?",
        ["data:image/png;base64,encoded"],
    )

    assert messages[-1] == {
        "role": "user",
        "content": [
            {"type": "text", "text": "What is wrong?"},
            {
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64,encoded"},
            },
        ],
    }


def test_build_initial_messages_does_not_replay_history_for_images():
    messages = build_initial_messages(
        [{"role": "assistant", "content": "There is no image."}],
        "Describe this image.",
        ["data:image/png;base64,encoded"],
    )

    assert messages == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Describe this image."},
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,encoded"},
                },
            ],
        }
    ]


def test_extract_streamed_plan_steps_ignores_incomplete_next_step():
    steps = extract_streamed_plan_steps(
        '{"todos":['
        '{"content":"Inspect","status":"in_progress"},'
        '{"content":"Deliver"'
    )

    assert steps == [
        {
            "id": "step-1",
            "title": "Inspect",
            "status": "in_progress",
        }
    ]


def test_extract_streamed_plan_steps_handles_json_string_delimiters():
    steps = extract_streamed_plan_steps(
        '{"todos":['
        '{"content":"Inspect } and \\"quoted\\" text",'
        '"status":"pending"},'
    )

    assert steps[0]["title"] == 'Inspect } and "quoted" text'


def test_extract_streamed_plan_steps_returns_empty_for_invalid_payload():
    assert extract_streamed_plan_steps("not-json") == []
    assert extract_streamed_plan_steps('{"other":[]}') == []


def test_extract_streamed_plan_steps_limits_visible_steps():
    todos = ",".join(
        f'{{"content":"Step {index}"}}' for index in range(15)
    )

    steps = extract_streamed_plan_steps(f'{{"todos":[{todos}]}}')

    assert len(steps) == 12
    assert steps[-1]["title"] == "Step 11"


def test_direct_answer_includes_current_images():
    class Model:
        def invoke(self, messages, runtime_control_call=False):
            assert messages[-1]["content"][1]["type"] == "image_url"
            return type("Response", (), {"content": "Image described."})()

    answer = _answer_general_chat_directly(
        Model(),
        {
            "question": "Describe this image.",
            "image_data_urls": ["data:image/png;base64,encoded"],
        },
        "System prompt",
    )

    assert answer == "Image described."
