"""
The `refine-downscaling` skill on the `water4energy` project.

Three layers, all hermetic:
  * the SKILL.md itself parses and carries the guidance the skill exists to give
  * the offline seed registers it alongside `water4energy-diagnostic`
  * a real agent's system prompt advertises both, with routing between them

The HPC job half (`cluster_defaults.json`, `job.frontier.slurm`,
`run_downscaling.py`) is covered in
`mcp_servers/vista_mcp_server/tests/test_refine_downscaling_job.py`, where the
catalog models live. Nothing here touches Frontier, a GPU, or the staged Daymet
assets.
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

SKILL = "refine-downscaling"
SIBLING = "water4energy-diagnostic"
PROJECT = "water4energy"

DB_DIR = Path(vista_backend.__file__).parent / "db"
SKILL_DIR = DB_DIR / "skills" / SKILL
SYSTEM_PROMPT = DB_DIR / "system_prompts" / "water4energy.md"
REPO_ROOT = Path(vista_backend.__file__).resolve().parents[3]
JOB_DIR = REPO_ROOT / "hpc_jobs" / SKILL


# The published full-year 1990 reference, from the demo package's model registry.
# Pinned here as literals: the demo package is not vendored, so CI cannot read it.
def flat(text: str) -> str:
    """Collapse whitespace: the sources hard-wrap prose, so phrases cross lines."""
    return " ".join(text.split())


REFERENCE = {
    "tmin": {"mae": "0.0497", "rmse": "0.1680", "baseline": "0.2016", "gain": "75.4 %"},
    "tmax": {"mae": "0.0487", "rmse": "0.1535", "baseline": "0.2012", "gain": "75.8 %"},
    "prcp": {"mae": "0.0495", "rmse": "0.3137", "baseline": "0.1347", "gain": "63.2 %"},
}


# ---------------------------------------------------------------------------
# SKILL.md
# ---------------------------------------------------------------------------


def test_skill_md_parses_with_valid_frontmatter():
    skill = read_skill(SKILL_DIR)
    assert skill.name == SKILL
    assert re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", skill.name)
    assert skill.metadata["version"] == "0.1.0"
    assert {"Frontier", "GPU", "Downscaling"}.issubset(set(skill.metadata["tags"]))
    assert skill.author
    # The demo package ships no LICENSE and states no terms, so the skill must not
    # assert one — same reasoning as the sibling skill.
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
        "downscal",  # downscale / downscaling
        "super-resolution",
        "resolution",
        "Daymet",
        "REFINE",
        "tmin",
        "prcp",
        "precipitation",
        "Frontier",
        "Water4Energy",
    ],
)
def test_description_carries_the_vocabulary_that_should_trigger_it(trigger):
    assert trigger in flat(read_skill(SKILL_DIR).description)


def test_description_rules_out_the_jobs_it_must_not_be_chosen_for():
    """
    This project carries two climate skills whose descriptions share a lot of
    vocabulary (temperature, precipitation, Frontier, climate). The negative
    statements are what keep the router off the wrong one.
    """
    description = flat(read_skill(SKILL_DIR).description)
    assert "NOT a climate simulation" in description
    assert "NOT a bias-correction" in description
    assert "NOT a general regridding" in description


def test_body_routes_between_the_two_climate_skills():
    body = flat(read_skill(SKILL_DIR).body)
    assert SIBLING in body
    # "Evaluate the model" means different things to each skill; the body has to
    # say so rather than let the agent pick.
    assert "ambiguous" in body


def test_body_carries_the_published_reference_metrics():
    """The judgment aid: without these the agent cannot tell a healthy run."""
    body = flat(read_skill(SKILL_DIR).body)
    for variable, values in REFERENCE.items():
        assert variable in body
        for value in values.values():
            assert value in body, (
                f"{variable}: {value} missing from the reference table"
            )


def test_body_warns_that_a_short_run_is_not_the_reference():
    body = flat(read_skill(SKILL_DIR).body)
    assert "A short run is not the reference" in body
    assert "365" in body


def test_body_explains_the_precipitation_error_shape():
    """
    RMSE/MAE is ~6.3 for prcp against ~3.2-3.4 for the temperatures. Read naively
    that looks like the model failing at rainfall; it is heavy-tailed daily
    precipitation, and the like-for-like number is the gain over the baseline.
    """
    body = flat(read_skill(SKILL_DIR).body)
    assert "heavy-tailed" in body
    assert "6.3" in body and "3.4" in body
    assert "63 %" in body and "75 %" in body


def test_body_warns_that_near_zero_bias_is_not_a_clean_bill_of_health():
    body = flat(read_skill(SKILL_DIR).body)
    assert "Near-zero bias is not a clean bill of health" in body
    assert "cancel" in body


def test_body_reports_the_physical_consistency_caveat():
    body = flat(read_skill(SKILL_DIR).body)
    assert "0.0142" in body or "1.4 %" in body
    assert "enforce-temperature-order" in body


@pytest.mark.parametrize(
    "guardrail",
    [
        "Never present figures as the result",
        "Always state the interval",
        "Call the baseline a baseline",
        "Never compare metrics across different splits",
        "Do not fetch the NetCDF without asking",
        "grid indexes",
        "A completed job is not a validated model",
    ],
)
def test_body_carries_its_guardrails(guardrail):
    assert guardrail in flat(read_skill(SKILL_DIR).body)


def test_body_states_that_training_is_out_of_scope():
    """The agent must not be able to talk itself into a multi-node training run."""
    body = flat(read_skill(SKILL_DIR).body)
    assert "Training is deliberately out of scope" in body


# ---------------------------------------------------------------------------
# SKILL.md <-> job agreement
# ---------------------------------------------------------------------------


def test_skill_and_job_readme_agree_on_the_job_name():
    assert (JOB_DIR / "README.md").read_text(encoding="utf-8").startswith(f"# {SKILL}")
    assert f'job="{SKILL}"' in flat(read_skill(SKILL_DIR).body)


def test_skill_documents_only_flags_the_wrapper_accepts():
    """A documented flag the wrapper would reject is a broken submission."""
    wrapper = (JOB_DIR / "run_downscaling.py").read_text(encoding="utf-8")
    body = flat(read_skill(SKILL_DIR).body)
    for flag in (
        "--mode",
        "--days",
        "--start-date",
        "--batch-size",
        "--allow-large",
        "--checksum-inputs",
        "--split",
        "--max-days",
        "--plots",
        "--tile-rows",
    ):
        assert flag in body, f"{flag} is accepted but undocumented"
        assert f'"{flag}"' in wrapper, f"{flag} is documented but not accepted"


def test_skill_and_wrapper_agree_on_the_day_guardrail():
    wrapper = (JOB_DIR / "run_downscaling.py").read_text(encoding="utf-8")
    assert "MAX_DAYS_WITHOUT_OVERRIDE = 31" in wrapper
    assert "31 days" in flat(read_skill(SKILL_DIR).body)


def test_skill_names_both_modes_the_wrapper_offers():
    body = flat(read_skill(SKILL_DIR).body)
    for mode in ("--mode infer", "--mode evaluate"):
        assert mode in body


def test_skill_does_not_promise_a_measured_walltime():
    """
    `duration` is inherited from the demo's own launcher ceiling and has never been
    timed. The skill must say so rather than let the agent quote it as a fact.
    """
    defaults = json.loads(
        (JOB_DIR / "cluster_defaults.json").read_text(encoding="utf-8")
    )
    assert defaults["frontier"]["duration"] == 1800
    body = flat(read_skill(SKILL_DIR).body)
    assert "placeholder" in body
    assert "not a measurement" in body


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


async def test_the_project_carries_both_climate_skills(seeded):
    row = (
        await seeded.exec(select(ProjectTable).where(ProjectTable.name == PROJECT))
    ).first()
    assert row is not None
    assert set(row.skills) == {SKILL, SIBLING}


async def test_adding_the_skill_created_no_new_project(seeded):
    names = {p.name for p in (await seeded.exec(select(ProjectTable))).all()}
    assert names == {"alloy-design", "molten-salt", PROJECT}


async def test_the_project_description_covers_both_halves(seeded):
    row = (
        await seeded.exec(select(ProjectTable).where(ProjectTable.name == PROJECT))
    ).first()
    assert "downscaling" in row.description.lower()
    assert "E3SM" in row.description


@pytest.mark.parametrize(
    "tool, allowed",
    [
        ("submit_hpc_job", True),
        ("get_hpc_job_status", True),
        ("get_hpc_job_outputs", True),
        ("display_file", True),
        ("run_bash", True),
        ("agenthpc_submit_parameter_set", False),
        ("rag_search", False),  # no knowledge bases -> denied automatically
    ],
)
async def test_the_tool_policy_is_unchanged_by_the_second_skill(seeded, tool, allowed):
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


async def _prompt_and_tools(project, user, question):
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
        async for _ in agent.run_stream(user_prompt=question):
            pass
    assert prompts, "the model was never sent a system prompt"
    return prompts[0], tools[0]


@pytest.fixture
def staged_project(tmp_path, monkeypatch):
    """A real water4energy agent with BOTH real SKILL.md files in its volume."""
    import shutil

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    project = make_project(
        name=PROJECT,
        system_prompt=SYSTEM_PROMPT.read_text(encoding="utf-8"),
        skills=[SKILL, SIBLING],
        knowledge_bases=[],
        tools=["*", "!agenthpc_*"],
    )
    user = make_user()
    volume_skills = tmp_path / "volumes" / f"{project.id}-{user.id}" / "skills"
    volume_skills.mkdir(parents=True)
    for name in (SKILL, SIBLING):
        shutil.copytree(DB_DIR / "skills" / name, volume_skills / name)
    return project, user


async def test_the_agent_is_offered_both_climate_skills(staged_project):
    prompt, _ = await _prompt_and_tools(
        *staged_project, "Downscale a day of Daymet to 4 km."
    )
    assert f"<name>\n{SKILL}\n</name>" in prompt
    assert f"<name>\n{SIBLING}\n</name>" in prompt


@pytest.mark.parametrize(
    "rule",
    [
        # Routing between the two skills is the new failure mode this prompt owns.
        "Evaluate the model",
        "do not chain them",
        # Downscaling-specific discipline.
        "A short downscaling run is not the published reference",
        "heavy-tailed daily rainfall",
        "grid indexes, not longitude and latitude",
        "Never fetch it by reflex",
        "no training",
        # The environment rule that is the opposite of the sibling's.
        "needs a human",
    ],
)
async def test_the_prompt_carries_the_downscaling_rules(staged_project, rule):
    prompt, _ = await _prompt_and_tools(
        *staged_project, "Downscale a day of Daymet to 4 km."
    )
    assert rule in flat(prompt)


async def test_the_prompt_still_carries_the_diagnostics_rules(staged_project):
    """Adding a second skill must not cost the first one its guidance."""
    prompt, _ = await _prompt_and_tools(
        *staged_project, "How does E3SM do over the TVA area?"
    )
    for rule in ("E3SM − ERA5", "not the Tennessee River", 'duration="00:30:00"'):
        assert rule in flat(prompt)
