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
def built_with() -> list[str | None]:
    """The model override each draft asked `build_model_for` for."""
    return []


@pytest.fixture
def seen(monkeypatch, built_with) -> list[list[ModelMessage]]:
    calls: list[list[ModelMessage]] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls.append(messages)
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, DRAFT)])

    def build(local, model=None):
        built_with.append(model)
        return FunctionModel(respond)

    monkeypatch.setattr(skill_authoring, "build_model_for", build)
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


async def _chat(session, project, model):
    from vista_backend.db.schemas import ChatSessionUpdate
    from vista_backend.services import chat_session as chat_session_service

    chat = await chat_session_service.create_chat_session(
        session, project_id=project.id
    )
    await chat_session_service.update_chat_session(
        session,
        project_id=project.id,
        chat_session_id=chat.id,
        updates=ChatSessionUpdate(inference_model=model),
    )
    return chat


async def _generate(session, **body):
    from harness import api_client

    with api_client(session) as (client, _):
        return await client.post(
            "/skills/generate", json={"message_history": [], **body}
        )


@pytest.mark.anyio
async def test_draft_from_an_overridden_chat_uses_its_model(session, seen, built_with):
    from harness import seed_project

    project = await seed_project(session, name="a-project")
    chat = await _chat(session, project, "openai:chat-choice")

    response = await _generate(session, chat_session_id=str(chat.id))

    assert response.status_code == 200, response.text
    assert built_with == ["openai:chat-choice"]


@pytest.mark.anyio
async def test_draft_without_a_chat_uses_the_default(session, seen, built_with):
    assert (await _generate(session)).status_code == 200
    assert built_with == [None]


@pytest.mark.anyio
async def test_draft_with_a_missing_chat_is_a_404(session, seen, built_with):
    import uuid

    response = await _generate(session, chat_session_id=str(uuid.uuid4()))
    assert response.status_code == 404, response.text

    assert seen == [] and built_with == []
