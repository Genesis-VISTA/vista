"""
Skill drafting runs hermetically against a PydanticAI FunctionModel.

The model is faked at `build_model_for`. The chat history always carries
VISTA's own system prompt, so these check the drafting instructions are still
sent to the model, and that the hint (the report, from the report modal)
reaches the user prompt.
"""

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from vista_backend.agents import skill_authoring
from vista_backend.agents.skill_authoring import SkillDraft, generate_skill_draft


DRAFT = {
    "name_suggestion": "plot-a-quadratic",
    "description_suggestion": "Plot a quadratic in the sandbox.",
    "body": "## Steps\n\n1. Plot it.",
}


def _history() -> list[ModelMessage]:
    return [
        ModelRequest(
            parts=[SystemPromptPart("You are VISTA."), UserPromptPart("Plot x^2.")]
        ),
        ModelResponse(parts=[TextPart("Done.")]),
    ]


@pytest.fixture
def seen(monkeypatch) -> list[list[ModelMessage]]:
    calls: list[list[ModelMessage]] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls.append(messages)
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, DRAFT)])

    monkeypatch.setattr(
        skill_authoring, "build_model_for", lambda user: FunctionModel(respond)
    )
    return calls


@pytest.mark.anyio
async def test_returns_structured_draft(seen):
    assert await generate_skill_draft(_history()) == SkillDraft(**DRAFT)


@pytest.mark.anyio
async def test_instructions_reach_the_model_despite_chat_history(seen):
    await generate_skill_draft(_history())

    last = seen[0][-1]
    assert isinstance(last, ModelRequest)
    assert last.instructions is not None
    assert "SKILL.md" in last.instructions


@pytest.mark.anyio
async def test_hint_reaches_user_prompt(seen):
    await generate_skill_draft(_history(), hint="## Summary\n\nThe report.")

    last = seen[0][-1]
    assert isinstance(last, ModelRequest)
    prompt = last.parts[-1]
    assert isinstance(prompt, UserPromptPart)
    assert str(prompt.content).endswith("User hint: ## Summary\n\nThe report.")
