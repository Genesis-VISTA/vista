"""`trim_partial_history`: what the model is told about a turn that did not finish."""

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from vista_backend.services.run_history import STOPPED_NOTE, trim_partial_history

PRIOR: list[ModelMessage] = [
    ModelRequest(parts=[UserPromptPart(content="earlier question")]),
    ModelResponse(parts=[TextPart(content="earlier answer")]),
]


def _prompt(text: str = "submit the job") -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=text)])


def _call(call_id: str = "c1") -> ModelResponse:
    return ModelResponse(
        parts=[ToolCallPart(tool_name="submit_job", args={}, tool_call_id=call_id)]
    )


def _returned(call_id: str = "c1") -> ModelRequest:
    return ModelRequest(
        parts=[
            ToolReturnPart(
                tool_name="submit_job", content="job 42", tool_call_id=call_id
            )
        ]
    )


def _closing(history: list[ModelMessage]) -> str:
    last = history[-1]
    assert isinstance(last, ModelResponse)
    return "".join(p.content for p in last.parts if isinstance(p, TextPart))


def test_a_dangling_tool_call_is_dropped():
    captured = [*PRIOR, _prompt(), _call()]

    out = trim_partial_history(
        PRIOR, captured, user_prompt="submit the job", note=STOPPED_NOTE
    )

    assert out[: len(PRIOR)] == PRIOR
    assert [type(m) for m in out[len(PRIOR) :]] == [ModelRequest, ModelResponse]
    assert not any(
        isinstance(p, ToolCallPart)
        for m in out
        if isinstance(m, ModelResponse)
        for p in m.parts
    )
    assert _closing(out) == STOPPED_NOTE


def test_a_completed_tool_round_trip_is_kept():
    captured = [*PRIOR, _prompt(), _call(), _returned()]

    out = trim_partial_history(
        PRIOR, captured, user_prompt="submit the job", note=STOPPED_NOTE
    )

    new = out[len(PRIOR) :]
    assert [type(m) for m in new] == [
        ModelRequest,
        ModelResponse,
        ModelRequest,
        ModelResponse,
    ]
    assert any(isinstance(p, ToolCallPart) for p in new[1].parts)
    assert any(isinstance(p, ToolReturnPart) for p in new[2].parts)
    assert _closing(out) == STOPPED_NOTE


def test_a_second_call_in_flight_keeps_only_the_first_round_trip():
    captured = [*PRIOR, _prompt(), _call("c1"), _returned("c1"), _call("c2")]

    out = trim_partial_history(
        PRIOR, captured, user_prompt="submit the job", note=STOPPED_NOTE
    )

    calls = [
        p.tool_call_id
        for m in out
        if isinstance(m, ModelResponse)
        for p in m.parts
        if isinstance(p, ToolCallPart)
    ]
    assert calls == ["c1"]


def test_a_text_only_turn_gets_the_note_on_its_answer():
    captured = [*PRIOR, _prompt("hello"), ModelResponse(parts=[TextPart(content="hi")])]

    out = trim_partial_history(PRIOR, captured, user_prompt="hello", note=STOPPED_NOTE)

    assert len(out) == len(PRIOR) + 2, "no second response stacked on the first"
    last = out[-1]
    assert isinstance(last, ModelResponse)
    assert [p.content for p in last.parts if isinstance(p, TextPart)] == [
        "hi",
        STOPPED_NOTE,
    ]


def test_the_prompt_survives_a_run_that_never_reached_the_model():
    out = trim_partial_history(
        PRIOR, list(PRIOR), user_prompt="submit the job", note=STOPPED_NOTE
    )

    new = out[len(PRIOR) :]
    assert isinstance(new[0], ModelRequest)
    assert new[0].parts[0].content == "submit the job"  # type: ignore[union-attr]
    assert _closing(out) == STOPPED_NOTE
