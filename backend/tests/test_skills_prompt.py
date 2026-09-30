"""
Skill / project wiring for the agent prompt and tool list (Milestone C).

Builds on the Milestone B harness: a scripted model sees the system prompt and
the filtered tool list, so assertions here cover the wiring from project
config → what the model is offered — without starting MCP servers.
"""

import logging
import textwrap
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, SystemPromptPart, TextPart
from pydantic_ai.models.function import AgentInfo

from harness import agent_under_test, make_project, make_user, scripted_model
from vista_backend.agents.agents import ProjectAgent
from vista_backend.agents.skills import _in_sandbox, read_skill, to_prompt
from vista_backend.config import settings

pytestmark = [pytest.mark.anyio, pytest.mark.unit]


def _capturing_model(sink: list[str]):
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


async def _tools_offered(project, user) -> list[str]:
    seen: list[list[str]] = []

    def driver(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append([t.name for t in info.function_tools])
        return ModelResponse(parts=[TextPart("ok")])

    with agent_under_test(project, user, scripted_model(driver)) as (agent, _):
        async for _ in agent.run_stream(user_prompt="hi"):
            pass
    return seen[0]


def _write_skill(root: Path, name: str, description: str) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        textwrap.dedent(f"""\
        ---
        name: {name}
        description: {description}
        ---

        Instructions for {name}.
        """),
        encoding="utf-8",
    )
    return skill_dir


# ---------------------------------------------------------------------------
# SKILL.md load + prompt block
# ---------------------------------------------------------------------------


def test_read_skill_loads_seeded_alloy_tc_planner_skill():
    """A real on-disk skill under db/skills/ parses without a network."""
    skill_dir = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "vista_backend"
        / "db"
        / "skills"
        / "alloy-tc-planner"
    )
    skill = read_skill(skill_dir)
    assert skill.name == "alloy-tc-planner"
    assert (
        "high-entropy" in skill.description.lower()
        or "alloy" in skill.description.lower()
    )


def test_to_prompt_renders_available_skills_xml(tmp_path: Path):
    skill_dir = _write_skill(tmp_path, "salt-analysis", "Analyze molten salts.")
    block = to_prompt([skill_dir], {tmp_path: "/mnt/skills"})
    assert "<available_skills>" in block
    assert "<name>" in block and "salt-analysis" in block
    assert "Analyze molten salts." in block
    assert "/mnt/skills/salt-analysis/SKILL.md" in block


def test_to_prompt_keeps_the_sandbox_path_as_given(tmp_path: Path):
    """
    The sandbox side is never resolved on the host: on Windows that would turn `/mnt/skills`
    into `C:\\mnt\\skills`, and on macOS `/tmp` would become `/private/tmp`.
    """
    skill_dir = _write_skill(tmp_path, "salt-analysis", "Analyze molten salts.")
    block = to_prompt([skill_dir], {tmp_path: "/tmp/skills"})
    assert "<location>\n/tmp/skills/salt-analysis/SKILL.md\n</location>" in block


def test_sandbox_paths_are_posix_for_windows_host_paths():
    relative = PureWindowsPath("salt-analysis\\docs\\SKILL.md")
    assert _in_sandbox(PurePosixPath("/mnt/skills"), relative) == PurePosixPath(
        "/mnt/skills/salt-analysis/docs/SKILL.md"
    )


async def test_project_skills_appear_in_the_system_prompt(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    project = make_project(skills=["salt-chemistry-md"])
    user = make_user()
    _write_skill(
        tmp_path / "volumes" / f"{project.id}-{user.id}" / "skills",
        "salt-chemistry-md",
        "Run OpenMM+MACE density jobs.",
    )

    prompt = await _system_prompt_for(project, user)
    assert "<available_skills>" in prompt
    assert "salt-chemistry-md" in prompt
    assert "Run OpenMM+MACE density jobs." in prompt


# ---------------------------------------------------------------------------
# Missing skill slug — warn and skip (current production behavior)
# ---------------------------------------------------------------------------


async def test_missing_skill_slug_is_omitted_from_the_prompt(
    monkeypatch, tmp_path: Path, caplog
):
    """
    A project that lists a skill with no on-disk copy still runs; the skill is
    skipped with a warning rather than aborting the turn.
    """
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    project = make_project(skills=["does-not-exist"])
    user = make_user()

    with caplog.at_level(logging.WARNING):
        prompt = await _system_prompt_for(project, user)

    assert "does-not-exist" not in prompt or "<available_skills>" not in prompt
    # to_prompt warns when SKILL.md is missing under the volume path.
    assert any(
        "does-not-exist" in r.message or "skill" in r.message.lower()
        for r in caplog.records
    ) or ("<available_skills>" not in prompt)


async def test_setup_volumes_skips_unknown_skill_with_a_warning(
    monkeypatch, tmp_path: Path, caplog
):
    """
    `_setup_volumes` looks skills up in SkillTable; an unknown slug is logged
    and skipped — no exception.
    """
    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import StaticPool
    from sqlmodel import SQLModel

    monkeypatch.setattr(settings, "data_dir", tmp_path)

    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_fks(dbapi_connection, _):
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    monkeypatch.setattr("vista_backend.agents.agents.get_engine", lambda: engine)

    async def no_subprocess(*args, **kwargs):
        raise AssertionError(f"_setup_volumes spawned {args}")

    # Staging must not shell out (no `chmod` on Windows).
    monkeypatch.setattr(
        "vista_backend.agents.agents.asyncio.create_subprocess_exec", no_subprocess
    )

    project = make_project(skills=["ghost-skill"])
    agent = ProjectAgent(project, make_user())

    with caplog.at_level(logging.WARNING):
        await agent._setup_volumes()

    assert not (tmp_path / "volumes" / agent.id / "skills" / "ghost-skill").exists()
    assert any("ghost-skill" in r.message for r in caplog.records)
    await engine.dispose()


# ---------------------------------------------------------------------------
# Project tools / knowledge_bases reshape what the model sees
# ---------------------------------------------------------------------------


async def test_project_tools_filter_the_model_tool_list():
    project = make_project(tools=["display_file"], knowledge_bases=["kb"])
    offered = await _tools_offered(project, make_user())
    assert "display_file" in offered
    assert "rag_search" not in offered
    assert "submit_hpc_job" not in offered
    assert "run_bash" not in offered


async def test_knowledge_bases_toggle_rag_search_and_prompt_text():
    with_kb = make_project(tools=["*"], knowledge_bases=["molten-salt-papers"])
    without_kb = make_project(tools=["*"], knowledge_bases=[])

    assert "rag_search" in await _tools_offered(with_kb, make_user())
    assert "rag_search" not in await _tools_offered(without_kb, make_user())

    prompt_with = await _system_prompt_for(with_kb, make_user())
    prompt_without = await _system_prompt_for(without_kb, make_user())
    assert "molten-salt-papers" in prompt_with
    assert "No Knowledge Bases are configured" in prompt_without
