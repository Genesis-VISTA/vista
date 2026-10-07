"""
The `water4energy-diagnostic` skill and its `water4energy` project.

Three layers, all hermetic:
  * the SKILL.md itself parses and carries the guidance the skill exists to give
  * the offline seed registers the skill and wires the project to it
  * a real agent's system prompt actually advertises it, with the right tools

The HPC job half (`cluster_defaults.json`, `run_diagnostic.py`) is covered in
`mcp_servers/vista_mcp_server/tests/test_water4energy_job.py`, where the catalog
models live. Nothing here touches Frontier, Globus, or the staged climatologies.
"""

import json
import re
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, SystemPromptPart, TextPart
from pydantic_ai.models.function import AgentInfo
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

import vista_backend
from harness import agent_under_test, make_project, make_user, scripted_model
from vista_backend.agents.skills import (
    parse_skill,
    read_skill,
    skill_to_markdown,
    to_prompt,
)
from vista_backend.config import settings
from vista_backend.db.schemas import ProjectTable, SkillTable
from vista_backend.db.seed import seed_db
from vista_backend.utils.misc import tool_allowed

pytestmark = [pytest.mark.anyio, pytest.mark.unit]

SKILL = "water4energy-diagnostic"
PROJECT = "water4energy"

DB_DIR = Path(vista_backend.__file__).parent / "db"
SKILL_DIR = DB_DIR / "skills" / SKILL
SYSTEM_PROMPT = DB_DIR / "system_prompts" / "water4energy.md"
REPO_ROOT = Path(vista_backend.__file__).resolve().parents[3]
JOB_DIR = REPO_ROOT / "hpc_jobs" / SKILL


# ---------------------------------------------------------------------------
# SKILL.md
# ---------------------------------------------------------------------------


def test_skill_md_parses_with_valid_frontmatter():
    skill = read_skill(SKILL_DIR)
    assert skill.name == SKILL
    assert re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", skill.name)
    assert skill.metadata["version"] == "0.1.0"
    assert {"Frontier", "Climate"}.issubset(set(skill.metadata["tags"]))
    assert skill.author
    # The upstream repo ships no LICENSE and states no terms, so the skill must not
    # assert one. See openspec tasks 3.1.
    assert skill.license is None


def test_skill_md_round_trips_through_the_serializer():
    skill = read_skill(SKILL_DIR)
    again = parse_skill(skill_to_markdown(skill))
    assert (again.name, again.description, again.body) == (
        skill.name,
        skill.description,
        skill.body,
    )


@pytest.mark.parametrize(
    "trigger",
    [
        "E3SM",
        "ERA5",
        "TVA",
        "precipitation",
        "bias",
        "pattern correlation",
        "climate model",
        "reanalysis",
        "water4energy",
    ],
)
def test_description_carries_the_terms_the_model_matches_on(trigger):
    assert trigger.lower() in read_skill(SKILL_DIR).description.lower()


@pytest.mark.parametrize(
    "section",
    [
        "## Workflow",
        "## `script_args` contract",
        "## Reading `results.json`",
        "## Interpreting the result",
        "## Guardrails",
        "## Troubleshooting",
    ],
)
def test_skill_md_has_its_required_sections(section):
    assert section in read_skill(SKILL_DIR).body


@pytest.mark.parametrize(
    "claim",
    [
        "0.9937",  # temperature global reference r
        "1.685",  # temperature global reference RMSE
        "0.8879",  # precipitation global reference r
        "~20 grid-cell centers",  # why regional precip correlation is low
        "different zero points",  # why temperature nRMSE is omitted
        "E3SM − ERA5",  # bias sign convention
        'duration="00:30:00"',  # cold-environment first run
    ],
)
def test_skill_md_keeps_its_interpretation_guidance(claim):
    """
    These are the judgements the skill exists to supply. If one disappears the
    agent starts reporting figures it cannot interpret.
    """
    assert claim in read_skill(SKILL_DIR).body


def test_skill_md_does_not_call_the_service_area_a_watershed():
    """The TVA polygon is the electric service territory, not a river basin."""
    body = read_skill(SKILL_DIR).body
    assert "not** the\nTennessee River watershed" in body
    for wrong in ("TVA watershed", "TVA basin", "TVA catchment"):
        assert wrong not in body


@pytest.mark.parametrize(
    "claim",
    ["sleep 45", "Never poll back-to-back", "Stop after ~20 polls", "request_limit"],
)
def test_skill_md_paces_the_status_polling(claim):
    """
    Nothing throttles `get_hpc_job_status` server-side, and Frontier logs are cached
    for 30 s, so an unpaced loop burns the request budget for byte-identical output.
    The cadence has to live here.
    """
    assert claim in read_skill(SKILL_DIR).body


def test_skill_md_does_not_promise_the_figure_only_metrics():
    """
    Upstream computes means / nRMSE / sigma-ratio but only draws them into the
    figure, so `results.json` cannot carry them (openspec design decision 5).
    """
    body = read_skill(SKILL_DIR).body
    assert "not machine-readable" in body
    assert "Never guess or recompute them." in body


# ---------------------------------------------------------------------------
# SKILL.md <-> job agreement
# ---------------------------------------------------------------------------


def test_skill_and_job_readme_agree_on_the_job_name():
    assert (JOB_DIR / "README.md").read_text().startswith(f"# {SKILL}")
    assert f'job="{SKILL}"' in read_skill(SKILL_DIR).body


def test_skill_documents_only_flags_the_wrapper_accepts():
    """A documented flag the wrapper would reject is a broken submission."""
    wrapper = (JOB_DIR / "run_diagnostic.py").read_text()
    for flag in ("--resolution", "--dpi", "--checksum-inputs"):
        assert flag in read_skill(SKILL_DIR).body
        assert f'"{flag}"' in wrapper


def test_skill_default_duration_matches_the_job_default():
    defaults = json.loads((JOB_DIR / "cluster_defaults.json").read_text())
    assert defaults["frontier"]["duration"] == 600
    assert 'duration="00:10:00"' in read_skill(SKILL_DIR).body


# ---------------------------------------------------------------------------
# seed
# ---------------------------------------------------------------------------


@pytest.fixture
async def seeded(tmp_path, monkeypatch):
    """
    Offline seed into a throwaway data dir + in-memory SQLite.

    Seeds from a local vista-data *payload* rather than a token, which is what
    turns the science projects on (`_science_enabled`) without any network. The
    project under test lives in that gated set, so without this the seed makes
    no projects at all. Mirrors `payload_env` in test_seed_payload.py, whose
    helpers are reused rather than duplicated — building the stand-in Chroma
    store is fiddly enough to be worth sharing.
    """
    import vista_backend.db.seed as seed_module
    from test_seed_payload import make_payload, make_vector_store

    data_dir = tmp_path / "data"
    make_vector_store(data_dir / "knowledge-bases" / "molten-salt-papers" / "rag_db")
    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(settings, "vista_data_token", None)
    monkeypatch.setattr(
        settings, "vista_data_payload_dir", make_payload(tmp_path / "payload")
    )
    monkeypatch.setattr(seed_module, "REPO_ROOT", tmp_path / "repo")

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
    await seed_db(engine)
    async with AsyncSession(engine) as session:
        yield session
    await engine.dispose()


async def test_seed_registers_the_skill_without_a_vista_data_token(seeded):
    """The skill needs no GitLab assets, so it is never in `skipped_skills`."""
    names = {s.name for s in (await seeded.exec(select(SkillTable))).all()}
    assert SKILL in names


async def test_seed_snapshot_for_the_water4energy_project(seeded):
    row = (
        await seeded.exec(select(ProjectTable).where(ProjectTable.name == PROJECT))
    ).first()
    assert row is not None, "seed did not create the water4energy project"
    assert str(row.id) == "e0468a13-50ae-41e3-a8f9-e461b4b4bc3c", (
        "project id must stay stable — it is referenced by saved sessions"
    )
    # Both climate skills ride on this project: the diagnostic (E3SM vs ERA5) and
    # REFINE downscaling. Sorted, so refine-downscaling comes first.
    assert row.skills == ["refine-downscaling", SKILL]
    assert row.knowledge_bases == []
    assert row.tools == ["*", "!agenthpc_*"]
    assert row.usage_limits == {"request_limit": 100}
    assert row.system_prompt == SYSTEM_PROMPT.read_text()


async def test_seed_leaves_the_existing_projects_alone(seeded):
    names = {p.name for p in (await seeded.exec(select(ProjectTable))).all()}
    assert names == {"alloy-design", "molten-salt", PROJECT}


@pytest.mark.parametrize(
    "tool, allowed",
    [
        ("submit_hpc_job", True),
        ("get_hpc_job_status", True),
        ("get_hpc_job_outputs", True),
        ("list_hpc_jobs", True),
        ("display_file", True),
        ("run_bash", True),
        ("agenthpc_submit_parameter_set", False),
        ("rag_search", False),  # no knowledge bases -> denied automatically
    ],
)
async def test_project_tool_policy(seeded, tool, allowed):
    row = (
        await seeded.exec(select(ProjectTable).where(ProjectTable.name == PROJECT))
    ).first()
    patterns = list(row.tools) + ([] if row.knowledge_bases else ["!rag_search"])
    assert tool_allowed(tool, patterns) is allowed


# ---------------------------------------------------------------------------
# prompt wiring
# ---------------------------------------------------------------------------


def test_to_prompt_advertises_the_skill_at_its_sandbox_location(tmp_path):
    import shutil

    staged = tmp_path / SKILL
    shutil.copytree(SKILL_DIR, staged)
    block = to_prompt([staged], {tmp_path: "/mnt/skills"})
    assert f"<name>\n{SKILL}\n</name>" in block
    assert f"/mnt/skills/{SKILL}/SKILL.md" in block


async def _prompt_and_tools(project, user):
    prompts: list[str] = []
    tools: list[list[str]] = []

    def driver(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        tools.append([t.name for t in info.function_tools])
        for message in messages:
            for part in getattr(message, "parts", []):
                if isinstance(part, SystemPromptPart):
                    prompts.append(part.content)
        return ModelResponse(parts=[TextPart("ok")])

    with agent_under_test(project, user, scripted_model(driver, name="capture")) as (
        agent,
        _,
    ):
        async for _ in agent.run_stream(
            user_prompt="How does E3SM do over the TVA area?"
        ):
            pass
    assert prompts, "the model was never sent a system prompt"
    return prompts[0], tools[0]


@pytest.fixture
def staged_project(tmp_path, monkeypatch):
    """A real water4energy agent, with the real SKILL.md in its sandbox volume."""
    import shutil

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    project = make_project(
        name=PROJECT,
        system_prompt=SYSTEM_PROMPT.read_text(),
        skills=[SKILL],
        knowledge_bases=[],
        tools=["*", "!agenthpc_*"],
    )
    user = make_user()
    volume_skills = tmp_path / "volumes" / f"{project.id}-{user.id}" / "skills"
    volume_skills.mkdir(parents=True)
    shutil.copytree(SKILL_DIR, volume_skills / SKILL)
    return project, user


async def test_agent_prompt_advertises_the_skill(staged_project):
    prompt, _ = await _prompt_and_tools(*staged_project)
    assert f"<name>\n{SKILL}\n</name>" in prompt
    assert f"/mnt/skills/{SKILL}/SKILL.md" in prompt
    for trigger in ("E3SM", "ERA5", "TVA", "pattern correlation"):
        assert trigger in prompt


@pytest.mark.parametrize(
    "rule",
    [
        "Figures alone are not an answer",
        "E3SM − ERA5",
        "not the Tennessee River",
        'duration="00:30:00"',
        "no multi-agent campaign",
        "sleep 45",
    ],
)
async def test_agent_prompt_carries_the_reporting_rules(staged_project, rule):
    prompt, _ = await _prompt_and_tools(*staged_project)
    assert rule in prompt


async def test_agent_is_not_offered_the_alloy_design_toolchain(staged_project):
    _, tools = await _prompt_and_tools(*staged_project)
    assert not [t for t in tools if t.startswith("agenthpc_")]


async def test_campaign_tools_are_present_so_the_prompt_must_wave_them_off(
    staged_project,
):
    """
    `register_campaign_tools` attaches these directly to the agent, so `project.tools`
    patterns cannot filter them (openspec task 4.4). The prompt is the only defence,
    so pin both halves of that arrangement.
    """
    prompt, tools = await _prompt_and_tools(*staged_project)
    assert "start_campaign" in tools
    assert "Ignore the campaign tools" in prompt
