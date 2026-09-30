"""Tests for the monitor<->MCP/planner wiring seams (parse, per-job invoke/planner, poll, collect)."""

from pathlib import Path

import pytest

from vista_backend.agents.campaign.manifest import CampaignManifest
from vista_backend.agents.campaign.mcp_invoke import project_paths_for
from vista_backend.agents.campaign.planner import CampaignPlanner, build_subagents
from vista_backend.agents.campaign.subagent import (
    CallableResultParser,
    ParsedResult,
    SubmittedJobInfo,
)
from vista_backend.agents.campaign.wiring import (
    build_collector,
    build_invoke_for_job,
    build_planner_for_job,
    build_status_poll,
    parse_job_state,
)
from vista_backend.config import settings
from vista_backend.db.schemas import ProjectTable, UserTable
from vista_backend.services import campaign as campaign_service
from vista_backend.services import chat_session as chat_session_service


MANIFEST_YAML = """
domain: testdomain
metrics:
  primary: {name: SCORE, target: 1.0}
subagents:
  - {role: alpha, skill: alpha-skill, job: alpha_job}
  - {role: beta, skill: beta-skill, job: beta_job}
"""


class _FakeHpc:
    async def submit(self, *, job, cluster, node_count, duration, script_args):
        return SubmittedJobInfo(job_id="job-1", cluster=cluster or "odo")

    async def status(self, *, job_id, cluster):
        return "STATE=COMPLETED"

    async def fetch_outputs(self, *, job_id, files, cluster):
        return ""


async def _make_run_step_job(session, alice, *, planner_skill="mock-planner"):
    project = ProjectTable(name=f"wiring-{planner_skill}")
    session.add(project)
    await session.flush()
    chat = await chat_session_service.get_or_create_chat_session(
        session, project_id=project.id, user_id=alice.id
    )
    run = await campaign_service.create_campaign(
        session,
        project_id=project.id,
        user_id=alice.id,
        session_id=chat.id,
        domain="testdomain",
        planner_skill=planner_skill,
    )
    step = await campaign_service.add_step(
        session, run_id=run.id, cycle=0, kind="alpha", candidate={"x": 1}
    )
    job = await campaign_service.record_job(
        session, job_id="job-1", step_id=step.id, user_id=alice.id, cluster="odo"
    )
    return project, run, step, job


# --- parse_job_state -------------------------------------------------------


def test_parse_job_state_from_status_output():
    text = "JOB_ID=12345\nCLUSTER=frontier\nSTATE=COMPLETED\n\n--- LOGS ---\nTBR=1.18"
    assert parse_job_state(text) == "COMPLETED"


def test_parse_job_state_defaults_to_unknown():
    assert parse_job_state("no state line here") == "UNKNOWN"
    assert parse_job_state("") == "UNKNOWN"


# --- build_invoke_for_job --------------------------------------------------


@pytest.mark.anyio
async def test_build_invoke_for_job_binds_user_and_paths(session, alice):
    _project, run, _step, job = await _make_run_step_job(session, alice)
    captured = {}

    def fake_builder(user, paths):
        captured["user"] = user
        captured["paths"] = paths

        async def invoke(name, args):
            return "OK"

        return invoke

    invoke = await build_invoke_for_job(session, job, invoke_builder=fake_builder)
    assert await invoke("get_hpc_job_status", {}) == "OK"
    assert captured["user"].email == alice.email
    assert captured["paths"] == project_paths_for(run.project_id, run.user_id)


# --- build_status_poll -----------------------------------------------------


@pytest.mark.anyio
async def test_build_status_poll_derives_invoke_per_job_and_parses(session, alice):
    _project, _run, _step, job = await _make_run_step_job(session, alice)
    calls = []

    def fake_builder(user, paths):
        async def invoke(name, args):
            calls.append((name, args))
            return "STATE=RUNNING\nlogs..."

        return invoke

    poll = build_status_poll(invoke_builder=fake_builder)
    state, raw = await poll(session, job)

    assert state == "RUNNING"
    assert "STATE=RUNNING" in raw
    assert calls == [("get_hpc_job_status", {"job_id": "job-1", "cluster": "odo"})]


# --- build_planner_for_job -------------------------------------------------


@pytest.mark.anyio
async def test_build_planner_for_job_reconstructs_from_skill(
    session, alice, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    _project, run, _step, job = await _make_run_step_job(session, alice)

    # Materialize the planner skill's campaign.yaml in the session's volume skills dir.
    skills_dir = Path(project_paths_for(run.project_id, run.user_id)["skills_dir"])
    (skills_dir / run.planner_skill).mkdir(parents=True)
    (skills_dir / run.planner_skill / "campaign.yaml").write_text(
        MANIFEST_YAML, encoding="utf-8"
    )

    async def _noop_invoke(name, args):
        return ""

    planner = await build_planner_for_job(
        session,
        job,
        invoke_builder=lambda user, paths: _noop_invoke,
        parser_factory=lambda d, r: CallableResultParser(
            lambda **_: ParsedResult(ok=True)
        ),
    )

    assert planner.manifest.roles == ["alpha", "beta"]
    assert set(planner.subagents) == {"alpha", "beta"}


@pytest.mark.anyio
async def test_build_planner_for_job_resolves_the_model_from_the_jobs_user(
    session, alice, tmp_path, monkeypatch
):
    """
    With no explicit model, the parser resolves one from the job's user row.

    This is the monitor's path: it runs in the background with no request
    context and never passes a model, so the parser used to fall back to
    `Settings` alone and could not see a key entered in the settings modal.
    """
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "openai_api_key", None)
    _project, run, _step, job = await _make_run_step_job(
        session, alice, planner_skill="mock-planner-usermodel"
    )

    user_row = await session.get(UserTable, alice.id)
    user_row.inference_model = "openai-chat:row-model"
    user_row.inference_base_url = "https://user.example/v1"
    user_row.inference_api_key = "row-key"
    await session.flush()

    skills_dir = Path(project_paths_for(run.project_id, run.user_id)["skills_dir"])
    (skills_dir / run.planner_skill).mkdir(parents=True)
    (skills_dir / run.planner_skill / "campaign.yaml").write_text(
        MANIFEST_YAML, encoding="utf-8"
    )
    for role in ("alpha", "beta"):
        (skills_dir / f"{role}-skill").mkdir(parents=True)
        (skills_dir / f"{role}-skill" / "SKILL.md").write_text(
            f"---\nname: {role}-skill\ndescription: d\n---\n\nParse {role}.\n",
            encoding="utf-8",
        )

    async def _noop_invoke(name, args):
        return ""

    # No `parser_factory` and no `model`: the production path, resolving both
    # from the row above.
    planner = await build_planner_for_job(
        session, job, invoke_builder=lambda user, paths: _noop_invoke
    )

    client = planner.subagents["alpha"].parser.agent.model.client
    assert str(client.base_url).rstrip("/") == "https://user.example/v1"
    assert client.api_key == "row-key"


# --- build_collector -------------------------------------------------------


@pytest.mark.anyio
async def test_build_collector_routes_to_run_planner(session, alice):
    manifest = CampaignManifest.model_validate(
        {
            "domain": "d",
            "metrics": {"primary": {"name": "SCORE"}},
            "subagents": [
                {"role": "alpha", "skill": "alpha-skill", "job": "alpha_job"}
            ],
        }
    )
    subagents = build_subagents(
        manifest,
        hpc=_FakeHpc(),
        skills_dir="/unused",
        parser_factory=lambda d, r: CallableResultParser(
            lambda **_: ParsedResult(ok=True, metrics={"SCORE": 9})
        ),
    )
    planner = CampaignPlanner(manifest=manifest, subagents=subagents)

    project = ProjectTable(name="wiring-collector")
    session.add(project)
    await session.flush()
    run = await campaign_service.create_campaign(
        session, project_id=project.id, user_id=alice.id, domain="d", planner_skill="p"
    )
    await planner.dispatch_candidate(
        session, run_id=run.id, user_id=alice.id, candidate={"x": 1}, cycle=0
    )
    job = await campaign_service.get_job(session, "job-1")

    async def provider(_session, _job):
        return planner

    collect = build_collector(provider)
    await collect(session, job, "STATE=COMPLETED", True)

    step = await campaign_service.get_step(session, job.step_id)
    assert step.status == "completed"
    assert step.result["metrics"]["SCORE"] == 9


# --- multi-session isolation ----------------------------------------------


@pytest.mark.anyio
async def test_two_sessions_in_one_project_share_the_project_volume(session, alice):
    """Two campaigns in two conversations of the same project+user resolve to the SAME sandbox.

    main keys the sandbox volume by (project, user), not by chat session, so conversations in a
    project share it. session_id still drives orphan/resume decisions — just not the volume path.
    """
    project = ProjectTable(name="multi-session-iso")
    session.add(project)
    await session.flush()
    chat_a = await chat_session_service.create_chat_session(
        session, project_id=project.id, user_id=alice.id
    )
    chat_b = await chat_session_service.create_chat_session(
        session, project_id=project.id, user_id=alice.id
    )
    assert chat_a.id != chat_b.id

    async def _job_for(chat, job_id):
        run = await campaign_service.create_campaign(
            session,
            project_id=project.id,
            user_id=alice.id,
            session_id=chat.id,
            domain="d",
            planner_skill="p",
        )
        step = await campaign_service.add_step(
            session, run_id=run.id, cycle=0, kind="alpha"
        )
        return await campaign_service.record_job(
            session, job_id=job_id, step_id=step.id, user_id=alice.id, cluster="odo"
        )

    job_a = await _job_for(chat_a, "job-a")
    job_b = await _job_for(chat_b, "job-b")

    seen_paths: list[dict] = []

    def fake_builder(user, paths):
        seen_paths.append(paths)

        async def invoke(name, args):
            return "OK"

        return invoke

    await build_invoke_for_job(session, job_a, invoke_builder=fake_builder)
    await build_invoke_for_job(session, job_b, invoke_builder=fake_builder)

    # Keyed by (project, user): both conversations resolve to the same project volume.
    expected = project_paths_for(project.id, alice.id)
    assert seen_paths[0] == expected
    assert seen_paths[1] == expected


# --- collect_files: the monitor must reach the job's real outputs ----------

COLLECT_MANIFEST_YAML = """
domain: testdomain
metrics:
  primary: {name: SCORE, target: 1.0}
subagents:
  - {role: alpha, skill: alpha-skill, job: alpha_job, collect_files: [results.json]}
  - {role: beta,  skill: beta-skill,  job: beta_job,  collect_files: [out.json, log.txt]}
  - {role: gamma, skill: gamma-skill, job: gamma_job}
"""


class _RecordingHpc(_FakeHpc):
    """Records every fetch_outputs call so we can assert what the collector asked for."""

    def __init__(self, payload="RAW OUTPUT"):
        self.fetched: list[tuple[str, list[str]]] = []
        self.payload = payload

    async def fetch_outputs(self, *, job_id, files, cluster):
        self.fetched.append((job_id, list(files)))
        return self.payload


def _collect_planner(hpc, *, seen_outputs: list[str]) -> CampaignPlanner:
    manifest = CampaignManifest.model_validate(
        __import__("yaml").safe_load(COLLECT_MANIFEST_YAML)
    )

    def parser_factory(skill_dir, role):
        async def parse(*, candidate, raw_status, raw_outputs):
            seen_outputs.append(raw_outputs)
            return ParsedResult(ok=True, metrics={"SCORE": 1.0})

        return CallableResultParser(parse)

    subagents = build_subagents(
        manifest, hpc=hpc, skills_dir="/unused", parser_factory=parser_factory
    )
    return CampaignPlanner(manifest=manifest, subagents=subagents)


def test_collect_files_for_role_reads_the_manifest():
    planner = _collect_planner(_RecordingHpc(), seen_outputs=[])
    assert planner.collect_files_for_role("alpha") == ["results.json"]
    assert planner.collect_files_for_role("beta") == ["out.json", "log.txt"]
    assert planner.collect_files_for_role("gamma") == []
    assert planner.collect_files_for_role("nonexistent") == []


@pytest.mark.anyio
async def test_collector_fetches_declared_files_and_parser_receives_them(
    session, alice
):
    _project, _run, _step, job = await _make_run_step_job(session, alice)
    hpc = _RecordingHpc(payload='{"Tc": 1180}')
    seen: list[str] = []
    planner = _collect_planner(hpc, seen_outputs=seen)

    collect = build_collector(planner_provider=lambda _s, _j: _async(planner))
    await collect(session, job, "STATE=COMPLETED", True)

    # The declared file list reached the HPC boundary...
    assert hpc.fetched == [("job-1", ["results.json"])]
    # ...and its content reached the parser, rather than the empty string.
    assert seen == ['{"Tc": 1180}']


@pytest.mark.anyio
async def test_collector_without_declared_files_fetches_nothing(session, alice):
    """A role that declares no collect_files behaves exactly as before the change."""
    project = ProjectTable(name="wiring-gamma")
    session.add(project)
    await session.flush()
    chat = await chat_session_service.get_or_create_chat_session(
        session, project_id=project.id, user_id=alice.id
    )
    run = await campaign_service.create_campaign(
        session,
        project_id=project.id,
        user_id=alice.id,
        session_id=chat.id,
        domain="testdomain",
        planner_skill="mock-planner",
    )
    step = await campaign_service.add_step(
        session, run_id=run.id, cycle=0, kind="gamma", candidate={"x": 1}
    )
    job = await campaign_service.record_job(
        session, job_id="job-gamma", step_id=step.id, user_id=alice.id, cluster="odo"
    )

    hpc = _RecordingHpc()
    seen: list[str] = []
    planner = _collect_planner(hpc, seen_outputs=seen)

    collect = build_collector(planner_provider=lambda _s, _j: _async(planner))
    await collect(session, job, "STATE=COMPLETED", True)

    assert hpc.fetched == []  # no fetch_outputs call at all
    assert seen == [""]  # parser sees empty outputs, as today


@pytest.mark.anyio
async def test_explicit_files_argument_overrides_the_manifest(session, alice):
    _project, _run, _step, job = await _make_run_step_job(session, alice)
    hpc = _RecordingHpc()
    planner = _collect_planner(hpc, seen_outputs=[])

    await planner.collect_job(session, job=job, files=["override.json"])
    assert hpc.fetched == [("job-1", ["override.json"])]


def _async(value):
    """Wrap a value in an awaitable, for injecting a prebuilt planner as a provider."""

    async def _coro():
        return value

    return _coro()
