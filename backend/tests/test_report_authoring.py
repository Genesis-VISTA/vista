"""
Report drafting runs hermetically against a PydanticAI FunctionModel.

The model is faked at `build_model_for`, so these tests check what the drafter
sends (history, hint) and that the structured draft comes back intact.
"""

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from vista_backend.agents import report_authoring
from vista_backend.agents.report_authoring import (
    ConversationReport,
    generate_report_draft,
)


DRAFT = {
    "title": "LiF-NaF eutectic density",
    "slug_suggestion": "lif-naf-eutectic-density",
    "summary": "Computed the LiF-NaF eutectic density at 1000 K.",
    "body": "## Summary\n\nDensity found.\n\n## Record\n\n- rho = 1.95 g/cm3",
}


def _history() -> list[ModelMessage]:
    return [
        ModelRequest(parts=[UserPromptPart("What is the LiF-NaF eutectic density?")]),
        ModelResponse(parts=[TextPart("About 1.95 g/cm3 at 1000 K.")]),
    ]


def _user_prompts(messages: list[ModelMessage]) -> list[str]:
    return [
        part.content
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart) and isinstance(part.content, str)
    ]


@pytest.fixture
def seen(monkeypatch) -> list[list[ModelMessage]]:
    """Fake the model; record the messages each request sends."""
    calls: list[list[ModelMessage]] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls.append(messages)
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, DRAFT)])

    monkeypatch.setattr(
        report_authoring, "build_model_for", lambda user: FunctionModel(respond)
    )
    return calls


@pytest.mark.anyio
async def test_returns_structured_draft(seen):
    draft = await generate_report_draft(_history())

    assert draft == ConversationReport(**DRAFT)


@pytest.mark.anyio
async def test_sends_full_history(seen):
    await generate_report_draft(_history())

    prompts = _user_prompts(seen[0])
    assert prompts[0] == "What is the LiF-NaF eutectic density?"
    assert prompts[-1] == "Write a report of the conversation above."


@pytest.mark.anyio
async def test_hint_reaches_user_prompt(seen):
    await generate_report_draft(_history(), hint="  focus on the density  ")

    assert _user_prompts(seen[0])[-1].endswith("User hint: focus on the density")


@pytest.mark.anyio
async def test_blank_hint_is_ignored(seen):
    await generate_report_draft(_history(), hint="   ")

    assert "User hint" not in _user_prompts(seen[0])[-1]
