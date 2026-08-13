"""
Scripted LLMs for hermetic agent tests.

`ProjectAgent.run_stream` drives the model through `run_stream_events`, so a
bare `FunctionModel(driver)` is not enough — PydanticAI requires a
`stream_function` for streamed requests. `scripted_model` adapts the
`ModelResponse`-returning driver style used by
[`test_campaign_driver.py`](../test_campaign_driver.py) into both forms, so
tests can script a turn without caring which run method is used.
"""

from collections.abc import AsyncIterator, Callable, Sequence

from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

Driver = Callable[[list[ModelMessage], AgentInfo], ModelResponse]


def scripted_model(driver: Driver, *, name: str = "scripted") -> FunctionModel:
    """
    A `FunctionModel` that works for both `run` and `run_stream_events`.

    `driver` is called once per model request and returns the full
    `ModelResponse` for that step. PydanticAI's streaming protocol cannot
    interleave text and tool-call deltas in one response, so a step must be
    either all tool calls or all text.
    """

    async def stream(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        response = driver(messages, info)
        tool_calls = [p for p in response.parts if isinstance(p, ToolCallPart)]
        texts = [p for p in response.parts if isinstance(p, TextPart)]
        if tool_calls and texts:
            raise ValueError(
                "A scripted step must be either tool calls or text, not both"
            )
        if tool_calls:
            for index, part in enumerate(tool_calls):
                yield {
                    index: DeltaToolCall(
                        name=part.tool_name,
                        json_args=part.args_as_json_str(),
                        tool_call_id=part.tool_call_id,
                    )
                }
            return
        for part in texts:
            yield part.content

    return FunctionModel(driver, stream_function=stream, model_name=name)


def step_model(steps: Sequence[ModelResponse], *, name: str = "steps") -> FunctionModel:
    """
    A scripted model that replays `steps` in order, one per model request.

    Requests past the end of the script fall back to a plain text response so
    an over-eager agent loop fails on the assertion under test rather than on
    an IndexError.
    """
    calls = {"n": 0}

    def driver(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        index = calls["n"]
        calls["n"] += 1
        if index < len(steps):
            return steps[index]
        return ModelResponse(parts=[TextPart("script exhausted")])

    return scripted_model(driver, name=name)


def call(tool_name: str, **args) -> ModelResponse:
    """A one-tool-call step."""
    return ModelResponse(parts=[ToolCallPart(tool_name, args)])


def say(content: str) -> ModelResponse:
    """A final text step."""
    return ModelResponse(parts=[TextPart(content)])


def always_calls(tool_name: str, **args) -> FunctionModel:
    """A model that never stops requesting `tool_name` — used to hit usage limits."""

    def driver(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart(tool_name, args)])

    return scripted_model(driver, name="always_calls")
