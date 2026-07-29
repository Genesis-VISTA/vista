"""
System prompt assembly for `ProjectAgent` (testing roadmap Milestone B).

Assertions are made against the `SystemPromptPart` the model actually
receives, rather than by calling the prompt builder directly, so the wiring
between `_build_agent` and PydanticAI stays covered too.
"""

import textwrap
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, SystemPromptPart, TextPart
from pydantic_ai.models.function import AgentInfo

from harness import agent_under_test, make_project, make_user, scripted_model
from vista_backend.agents.agents import BASE_SYSTEM_PROMPT
from vista_backend.config import settings

pytestmark = [pytest.mark.anyio, pytest.mark.unit]


def _capturing_model(sink: list[str]):
    """A scripted model that records the system prompt then ends the turn."""

    def driver(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        for message in messages:
            for part in getattr(message, "parts", []):
                if isinstance(part, SystemPromptPart):
                    sink.append(part.content)
        return ModelResponse(parts=[TextPart("ok")])

    return scripted_model(driver, name="capture-prompt")


async def _system_prompt_for(project, user) -> str:
    sink: list[str] = []
    with agent_under_test(project, user, _capturing_model(sink)) as (agent, _):
        async for _ in agent.run_stream(user_prompt="hello"):
            pass
    assert sink, "the model was never sent a system prompt"
    return sink[0]


async def test_system_prompt_starts_from_the_base_prompt():
    prompt = await _system_prompt_for(make_project(), make_user())
    assert prompt.startswith(BASE_SYSTEM_PROMPT)


async def test_system_prompt_includes_project_system_prompt():
    project = make_project(system_prompt="Prefer FLiBe over FLiNaK.")
    prompt = await _system_prompt_for(project, make_user())
    assert "## Project Information" in prompt
    assert "Prefer FLiBe over FLiNaK." in prompt


async def test_system_prompt_omits_project_section_when_unset():
    prompt = await _system_prompt_for(make_project(system_prompt=None), make_user())
    assert "## Project Information" not in prompt


async def test_system_prompt_lists_knowledge_base_slugs():
    project = make_project(knowledge_bases=["msre-reports", "salt-corrosion"])
    prompt = await _system_prompt_for(project, make_user())
    assert "kb_slug" in prompt
    assert "  - msre-reports" in prompt
    assert "  - salt-corrosion" in prompt


async def test_system_prompt_says_rag_unavailable_without_knowledge_bases():
    prompt = await _system_prompt_for(make_project(knowledge_bases=[]), make_user())
    assert "No Knowledge Bases are configured for this project" in prompt


async def test_system_prompt_includes_skills_block(monkeypatch, tmp_path: Path):
    """
    The skills block is rendered from the agent's *volume* copy of each skill,
    so the test writes the skill where `_setup_volumes` would have put it and
    asserts the container-visible `/mnt/skills` path is what reaches the model.
    """
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    project = make_project(skills=["salt-analysis"])
    user = make_user()

    skill_dir = (
        tmp_path / "volumes" / f"{project.id}-{user.id}" / "skills" / "salt-analysis"
    )
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        textwrap.dedent("""\
        ---
        name: salt-analysis
        description: Analyze molten salt thermophysical properties.
        ---

        Run the analysis script.
        """)
    )

    prompt = await _system_prompt_for(project, user)
    assert "<available_skills>" in prompt
    assert "salt-analysis" in prompt
    assert "Analyze molten salt thermophysical properties." in prompt
    assert "/mnt/skills/salt-analysis/SKILL.md" in prompt


async def test_system_prompt_has_no_skills_block_without_skills(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    prompt = await _system_prompt_for(make_project(skills=[]), make_user())
    assert "<available_skills>" not in prompt


async def test_system_prompt_composes_all_layers_in_order(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    project = make_project(
        system_prompt="Project rules.",
        knowledge_bases=["msre-reports"],
        skills=["salt-analysis"],
    )
    user = make_user()

    skill_dir = (
        tmp_path / "volumes" / f"{project.id}-{user.id}" / "skills" / "salt-analysis"
    )
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: salt-analysis\ndescription: Analyze salts.\n---\n\nBody.\n"
    )

    prompt = await _system_prompt_for(project, user)
    positions = [
        prompt.index(BASE_SYSTEM_PROMPT),
        prompt.index("## Project Information"),
        prompt.index("msre-reports"),
        prompt.index("<available_skills>"),
    ]
    assert positions == sorted(positions)
