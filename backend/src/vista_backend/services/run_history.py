"""
Model history for a chat turn that did not finish (design D3).

A turn cut short by Stop, by VISTA quitting, or by an error leaves PydanticAI's
message list mid-step: the model may have asked for a tool that never ran. Sent
back as it is, that history is rejected by the provider. `trim_partial_history`
keeps every step that completed, drops the one in flight, and closes the turn
with a short assistant note so the next prompt alternates cleanly.
"""

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)

STOPPED_NOTE = "Stopped by the researcher."
INTERRUPTED_NOTE = "Interrupted when VISTA quit."
FAILED_NOTE = "The turn failed before it finished."


def trim_partial_history(
    prior: list[ModelMessage],
    captured: list[ModelMessage],
    *,
    user_prompt: str,
    note: str,
) -> list[ModelMessage]:
    """
    Return `prior` plus the completed steps of the interrupted turn, then `note`.

    `captured` is the run's whole message list (prior history included, as
    `capture_run_messages` yields it). The researcher's prompt is always kept,
    even when the run ended before the model saw it.
    """
    new = list(captured[len(prior) :])

    if not new or not _starts_with_prompt(new[0]):
        new.insert(0, ModelRequest(parts=[UserPromptPart(content=user_prompt)]))

    # A response that asked for tools is only complete once their returns follow
    # it, and those arrive as the next request. A trailing one never got them.
    if isinstance(new[-1], ModelResponse) and any(
        isinstance(part, ToolCallPart) for part in new[-1].parts
    ):
        new.pop()

    last = new[-1]
    if isinstance(last, ModelResponse):
        # Already a text-only answer: extend it rather than stack two responses.
        new[-1] = ModelResponse(
            parts=[*last.parts, TextPart(content=note)],
            usage=last.usage,
            model_name=last.model_name,
            timestamp=last.timestamp,
            provider_name=last.provider_name,
        )
    else:
        new.append(ModelResponse(parts=[TextPart(content=note)]))
    return [*prior, *new]


def _starts_with_prompt(message: ModelMessage) -> bool:
    return isinstance(message, ModelRequest) and any(
        isinstance(part, UserPromptPart) for part in message.parts
    )
