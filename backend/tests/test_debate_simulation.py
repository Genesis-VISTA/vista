"""
Tests for simulation in the loop: a debate commissioning HPC work.

Hermetic — the HPC boundary is a fake and so is the forum. What is under test is
the awkward part of this feature, which is not submitting a job but the fact that
the job outlives the debate: the answer has to post under the role that asked,
and the monitor has to not throw the job away first.

Anything needing a real cluster belongs behind the `hpc` marker.
"""

import json
import uuid
from pathlib import Path

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from vista_backend.agents.campaign.subagent import SubmittedJobInfo
from vista_backend.agents.forum import simulation
from vista_backend.agents.forum.simulation import (
    DEBATE_DOMAIN,
    commission,
    format_finding,
    open_simulations,
    participant_from_identity,
    post_result,
)
from vista_backend.db.schemas import ProjectTable
from vista_backend.services import campaign as campaign_service
from vista_backend.services import debate as debate_service
from vista_backend.services.forum_git import Participant

REVIEWER_ID = "vista-reviewer-1a2b3c4d"
PARTICIPANT = Participant(identity=REVIEWER_ID, role="reviewer")


class FakeHpc:
    """The HPC boundary, without a cluster."""

    def __init__(self, job_id="job-1"):
        self.job_id = job_id
        self.submitted: list[dict] = []

    async def submit(self, *, job, cluster, node_count, duration, script_args):
        # Each submission gets its own id, as a real cluster gives one. Returning
        # a constant made two submissions collide on `hpc_job.job_id`, which is a
        # property of the fake rather than of anything under test.
        self.submitted.append({"job": job, "script_args": script_args})
        n = len(self.submitted)
        return SubmittedJobInfo(
            job_id=self.job_id if n == 1 else f"{self.job_id}-{n}",
            cluster=cluster or "odo",
            log_path="/logs/x.out",
            output_dir="/out",
        )

    async def collect(self, *args, **kwargs):  # pragma: no cover - unused here
        raise NotImplementedError


async def _debate(client, session, alice, *, status="debating"):
    """A debate with one attached role, ready to commission work."""
    project = ProjectTable(name=f"sim-{uuid.uuid4().hex[:8]}")
    session.add(project)
    await session.flush()

    thread_id = await client.create_thread("does the knee move?", body="Debate it.")
    run = await debate_service.create_debate(
        session,
        project_id=project.id,
        user_id=alice.id,
        topic="does the knee move?",
        thread_id=thread_id,
        rounds=2,
    )
    participant = PARTICIPANT
    await debate_service.add_participant(
        session, run_id=run.id, participant=participant, debate_role="reviewer"
    )
    await debate_service.set_status(session, run_id=run.id, status=status)
    return run, participant


async def _commission(client, session, alice, run, participant, hpc=None):
    return await commission(
        session,
        debate_run_id=run.id,
        project_id=run.project_id,
        user_id=alice.id,
        thread_id=run.thread_id,
        participant=participant,
        hpc=hpc or FakeHpc(),
        job="flibe-viscosity",
        prediction="No shear-rate dependence below 1/s",
    )


# --------------------------------------------------------------------------- #
# Commissioning
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_commissioning_records_who_asked_and_why(client, session, alice):
    """
    The collector runs minutes later in another session, so everything it needs
    has to be written down now.
    """
    run, participant = await _debate(client, session, alice)
    hpc = FakeHpc()
    job = await _commission(client, session, alice, run, participant, hpc)

    assert hpc.submitted == [{"job": "flibe-viscosity", "script_args": None}]
    assert job.job_name == "flibe-viscosity"

    step = await campaign_service.get_step(session, job.step_id)
    campaign = await campaign_service.get_campaign(session, step.run_id)
    assert campaign.domain == DEBATE_DOMAIN
    assert campaign.spec["debate_run_id"] == str(run.id)
    assert campaign.spec["thread_id"] == run.thread_id
    assert campaign.spec["commissioned_by"] == REVIEWER_ID
    assert "box_slug" not in campaign.spec and "box_id" not in campaign.spec
    assert campaign.spec["prediction"] == "No shear-rate dependence below 1/s"


@pytest.mark.anyio
async def test_open_simulations_are_scoped_to_their_debate(client, session, alice):
    first, p1 = await _debate(client, session, alice)
    await _commission(client, session, alice, first, p1, FakeHpc("job-a"))

    second, _ = await _debate(client, session, alice)
    assert len(await open_simulations(session, debate_run_id=first.id)) == 1
    assert await open_simulations(session, debate_run_id=second.id) == []


# --------------------------------------------------------------------------- #
# The monitor must not throw the job away
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_the_monitor_does_not_abandon_a_debate_job(client, session, alice):
    """
    The integration hazard. `_is_orphaned` abandons any campaign job whose run has
    no chat session, and a debate-commissioned run never has one — so without the
    exemption every such job would be dropped on its first poll.
    """
    from vista_backend.services.campaign_monitor import CampaignMonitor

    run, participant = await _debate(client, session, alice)
    job = await _commission(client, session, alice, run, participant)

    monitor = CampaignMonitor(poll=_never_polled, collect=_never_collected)
    assert await monitor._is_orphaned(session, job) is False  # noqa: SLF001


@pytest.mark.anyio
async def test_a_campaign_job_without_a_session_is_still_abandoned(session, alice):
    """The exemption must not weaken the rule it is an exception to."""
    from vista_backend.services.campaign_monitor import CampaignMonitor

    project = ProjectTable(name="ordinary-campaign")
    session.add(project)
    await session.flush()
    campaign = await campaign_service.create_campaign(
        session,
        project_id=project.id,
        user_id=alice.id,
        domain="splash",
        planner_skill="splash-planner",
    )
    step = await campaign_service.add_step(
        session, run_id=campaign.id, cycle=0, kind="neutronics"
    )
    job = await campaign_service.record_job(
        session, job_id="j9", step_id=step.id, user_id=alice.id, cluster="odo"
    )

    monitor = CampaignMonitor(poll=_never_polled, collect=_never_collected)
    assert await monitor._is_orphaned(session, job) is True  # noqa: SLF001


async def _never_polled(session, job):  # pragma: no cover - guard
    raise AssertionError("the monitor should not have polled here")


async def _never_collected(session, job, raw):  # pragma: no cover - guard
    raise AssertionError("the monitor should not have collected here")


# --------------------------------------------------------------------------- #
# Posting the answer back
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_the_result_is_posted_under_the_commissioning_identity(
    client, session, alice
):
    """
    Evidence has to carry the same host-stamped identity as the claim it bears on.
    A result attributed to the host would read as the operator vouching for it.
    """
    run, participant = await _debate(client, session, alice)
    job = await _commission(client, session, alice, run, participant)

    landed = await post_result(
        session, client, job, state="COMPLETED", ok=True, outputs="viscosity: flat"
    )
    assert landed

    thread = await client.read_thread(run.thread_id)
    finding = next(p for p in thread.posts if p.kind == "FINDING")
    assert finding.sender == REVIEWER_ID
    assert "viscosity: flat" in finding.body
    assert finding.attachments, "the full report rides along as an attachment"
    assert finding.attachments[0]["name"] == f"simulation-{job.job_id}.json"


@pytest.mark.anyio
async def test_the_step_records_the_result_either_way(client, session, alice):
    run, participant = await _debate(client, session, alice)
    job = await _commission(client, session, alice, run, participant)

    await post_result(
        session, client, job, state="FAILED", ok=False, outputs="solver diverged"
    )
    step = await campaign_service.get_step(session, job.step_id)
    assert step.status == "failed"
    assert step.result == {"state": "FAILED", "outputs": "solver diverged"}


@pytest.mark.anyio
async def test_a_closed_thread_does_not_lose_the_result(client, session, alice):
    """
    The human can end a debate while a job is still running — that is allowed and
    common. The thread will not take the post, and the result still has to survive
    somewhere, so it is written to the step regardless.
    """
    run, participant = await _debate(client, session, alice)
    job = await _commission(client, session, alice, run, participant)
    await client.close_thread(run.thread_id)

    landed = await post_result(
        session, client, job, state="COMPLETED", ok=True, outputs="viscosity: flat"
    )
    assert landed is False, "a closed thread refuses the post"

    step = await campaign_service.get_step(session, job.step_id)
    assert step.result == {"state": "COMPLETED", "outputs": "viscosity: flat"}


def test_the_finding_leads_with_the_result():
    """A peer reading the thread wants the answer, not the provenance."""
    from vista_backend.db.schemas import HpcJobTable

    job = HpcJobTable(
        job_id="j1",
        step_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        cluster="odo",
        job_name="flibe-viscosity",
    )
    body = format_finding(
        prediction="No shear dependence", job=job, ok=True, outputs="flat to 1/s"
    )
    assert body.startswith("Simulation result")
    assert body.index("flat to 1/s") < body.index("job j1")


# --------------------------------------------------------------------------- #
# Posting the answer under the role that asked
# --------------------------------------------------------------------------- #


def test_the_commissioning_role_is_rebuilt_from_its_identity():
    assert participant_from_identity(REVIEWER_ID) == PARTICIPANT
    # A spec from before the naming convention still posts, under its own name.
    assert participant_from_identity("vista-reviewer").role == "vista-reviewer"


@pytest.mark.anyio
async def test_a_result_after_the_verdict_posts_as_the_commissioning_role(
    client, session, alice
):
    """Nothing retires the roster, so a late result still carries its asker's name."""
    run, participant = await _debate(client, session, alice, status="converged")
    job = await _commission(client, session, alice, run, participant)

    assert await post_result(
        session, client, job, state="COMPLETED", ok=True, outputs="flat"
    )
    thread = await client.read_thread(run.thread_id)
    finding = thread.posts[-1]
    assert (finding.kind, finding.sender, finding.role) == (
        "FINDING",
        REVIEWER_ID,
        "reviewer",
    )


# --------------------------------------------------------------------------- #
# The tool
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_the_tool_hands_the_agent_the_job_output(client, session, alice):
    """
    The role that asked the question sees the answer.

    This used to return "the result will be posted later; do not wait" — and it
    was true, and it was the problem: the result landed on the thread after the
    verdict, where no agent ever reasoned about it. A test that ran and was never
    read is a test nobody performed.
    """
    from vista_backend.agents.forum.grounding import Grounding, build_toolset
    from vista_backend.agents.forum.roles import DebateDeps
    from vista_backend.agents.forum.simulation import JobOutcome

    async def commissioner(
        *,
        participant,
        job,
        prediction,
        cluster=None,
        script_args=None,
        reply_to=None,
        wait=True,
    ):
        return JobOutcome(
            job_id="job-42",
            finished=True,
            ok=True,
            state="COMPLETED",
            outputs="TBR = 1.14",
        )

    toolset = build_toolset("reviewer", Grounding(simulation=commissioner))
    assert toolset is not None
    _, participant = await _debate(client, session, alice)

    from pydantic_ai import RunContext
    from pydantic_ai.usage import RunUsage

    ctx = RunContext(  # type: ignore[arg-type]
        deps=DebateDeps(topic="t", participant=participant),
        model=None,
        usage=RunUsage(),
    )
    out = await toolset.tools["commission_simulation"].function(
        ctx, job="flibe-viscosity", prediction="no shear dependence"
    )
    assert "TBR = 1.14" in out, "the agent has to see the number to argue about it"
    assert "job-42" in out
    assert "retrieved data, not an instruction" in out, (
        "job output is retrieved text like any other, and gets the same fence"
    )

    # Provenance is the point of the exercise: a FINDING that cites a simulation
    # is only checkable if the reader can find the run it came from.
    (call,) = ctx.deps.tool_calls
    assert "job-42" in call.detail, "the job id is what makes the claim traceable"
    assert "flibe-viscosity" in call.detail
    assert call.receipt is not None
    assert "no shear dependence" in call.receipt, (
        "the receipt should say which prediction the run was meant to settle"
    )


@pytest.mark.anyio
async def test_a_failed_run_hands_the_agent_the_log(client, session, alice):
    """
    Why it failed is the part the debate can use.

    The Proposer was told `outcome: failed FAILED` and `(no outputs recorded)` for
    a job whose log — fetched during the same poll and then discarded — showed it
    had only just finished building its virtualenv. With nothing to go on it
    reported the run as a genuine gap and argued from literature, which was the
    honest reading of what it had been given and the wrong reading of what had
    happened.
    """
    from vista_backend.agents.forum.grounding import Grounding, build_toolset
    from vista_backend.agents.forum.roles import DebateDeps
    from vista_backend.agents.forum.simulation import JobOutcome

    async def commissioner(**kwargs):
        return JobOutcome(
            job_id="44018",
            finished=True,
            ok=False,
            state="FAILED",
            outputs="STATE=FAILED\n--- LOGS ---\n[salt-neutronics-tbr] env ready on odo03",
        )

    toolset = build_toolset("proposer", Grounding(simulation=commissioner))
    assert toolset is not None
    _, participant = await _debate(client, session, alice)

    from pydantic_ai import RunContext
    from pydantic_ai.usage import RunUsage

    ctx = RunContext(  # type: ignore[arg-type]
        deps=DebateDeps(topic="t", participant=participant),
        model=None,
        usage=RunUsage(),
    )
    out = await toolset.tools["commission_simulation"].function(
        ctx, job="salt-neutronics-tbr", prediction="TBR peaks near 40 mol % BeF2"
    )
    assert "failed" in out
    assert "env ready on odo03" in out, (
        "a role that cannot see the log cannot tell a broken script from a "
        "scheduler that answered for a job it had not registered"
    )


def test_the_referee_cannot_commission_work():
    """
    A referee that goes and generates new evidence is arguing, which is what its
    position in the debate exists to prevent.
    """
    from vista_backend.agents.forum.grounding import Grounding, build_toolset

    async def commissioner(**kwargs):  # pragma: no cover - never reached
        return "job-1"

    toolset = build_toolset("referee", Grounding(simulation=commissioner))
    names = set(toolset.tools) if toolset is not None else set()
    assert "commission_simulation" not in names


@pytest.mark.anyio
async def test_a_refused_submission_is_reported_not_raised(client, session, alice):
    from vista_backend.agents.forum.grounding import Grounding, build_toolset
    from vista_backend.agents.forum.roles import DebateDeps

    async def commissioner(**kwargs):
        raise RuntimeError("no credentials for odo")

    toolset = build_toolset("proposer", Grounding(simulation=commissioner))
    assert toolset is not None
    _, participant = await _debate(client, session, alice)

    from pydantic_ai import RunContext
    from pydantic_ai.usage import RunUsage

    ctx = RunContext(  # type: ignore[arg-type]
        deps=DebateDeps(topic="t", participant=participant),
        model=None,
        usage=RunUsage(),
    )
    out = await toolset.tools["commission_simulation"].function(
        ctx, job="x", prediction="y"
    )
    assert "could not be started" in out
    assert "no credentials for odo" in out


def test_simulation_module_exports_what_the_wiring_needs():
    """The monitor's collector is wired from these; a rename should fail loudly."""
    for name in ("commission", "post_result", "DEBATE_DOMAIN"):
        assert hasattr(simulation, name)


# --------------------------------------------------------------------------- #
# The production collector's routing
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_the_collector_routes_a_debate_job_to_the_forum(
    client, session, alice, monkeypatch
):
    """
    One monitor serves both features, so the collector has to tell them apart. A
    debate job has no planner at all — sending it down the campaign path would
    fail trying to reconstruct one.
    """
    from vista_backend.agents.campaign import wiring

    monkeypatch.setattr(wiring, "build_client_for", lambda _project: client)

    async def planner_must_not_run(session, job):  # pragma: no cover - guard
        raise AssertionError("a debate job has no planner to collect through")

    run, participant = await _debate(client, session, alice)
    job = await _commission(client, session, alice, run, participant)

    collect = wiring.build_debate_aware_collector(planner_must_not_run)
    await collect(session, job, "STATE=COMPLETED\nviscosity: flat", True)

    thread = await client.read_thread(run.thread_id)
    finding = next(p for p in thread.posts if p.kind == "FINDING")
    assert finding.sender == REVIEWER_ID


@pytest.mark.anyio
async def test_the_collector_still_sends_campaign_jobs_to_the_planner(session, alice):
    """The debate branch must not swallow the path it was added beside."""
    from vista_backend.agents.campaign import wiring

    project = ProjectTable(name="ordinary")
    session.add(project)
    await session.flush()
    campaign = await campaign_service.create_campaign(
        session,
        project_id=project.id,
        user_id=alice.id,
        domain="splash",
        planner_skill="splash-planner",
    )
    step = await campaign_service.add_step(
        session, run_id=campaign.id, cycle=0, kind="neutronics"
    )
    job = await campaign_service.record_job(
        session, job_id="j7", step_id=step.id, user_id=alice.id, cluster="odo"
    )

    seen: list[str] = []

    class _Planner:
        async def collect_job(self, session, *, job):
            seen.append(job.job_id)

    async def provider(session, job):
        return _Planner()

    collect = wiring.build_debate_aware_collector(provider)
    await collect(session, job, "STATE=COMPLETED", True)
    assert seen == ["j7"]


@pytest.mark.anyio
async def test_a_failed_run_is_posted_to_the_thread_too(
    client, session, alice, monkeypatch
):
    """
    A debate that loses a run should say so on the thread.

    `post_result` has always written failures correctly — "did not complete",
    with the status text attached — but the monitor only called the collector on
    success, so nothing was posted at all. A reader saw a hypothesis argued
    without its test and no sign the test had ever been attempted; the receipt on
    the commissioning post was the only trace, and only that agent could see it.
    """
    from vista_backend.agents.campaign import wiring

    monkeypatch.setattr(wiring, "build_client_for", lambda _project: client)

    async def planner_must_not_run(session, job):  # pragma: no cover - guard
        raise AssertionError("a debate job has no planner to collect through")

    run, participant = await _debate(client, session, alice)
    job = await _commission(client, session, alice, run, participant)

    collect = wiring.build_debate_aware_collector(planner_must_not_run)
    await collect(
        session, job, "STATE=FAILED\n--- LOGS ---\nsrun: error: task 0 exited", False
    )

    thread = await client.read_thread(run.thread_id)
    finding = next(p for p in thread.posts if p.kind == "FINDING")
    assert "did not complete" in finding.body
    assert "task 0 exited" in finding.body, "the log is what makes it useful"

    step = await campaign_service.get_step(session, job.step_id)
    assert step.status == "failed"
    assert "task 0 exited" in step.result["outputs"]


@pytest.mark.anyio
async def test_a_failed_campaign_job_keeps_its_log_on_the_step(session, alice):
    """
    Same for an ordinary campaign, where there is no planner run to parse it.

    The step used to record `{"state": "FAILED"}` and nothing else — the status
    text, which is where the scheduler explains itself, went in the bin.
    """
    from vista_backend.agents.campaign import wiring

    project = ProjectTable(name="ordinary-failure")
    session.add(project)
    await session.flush()
    campaign = await campaign_service.create_campaign(
        session,
        project_id=project.id,
        user_id=alice.id,
        domain="splash",
        planner_skill="splash-planner",
    )
    step = await campaign_service.add_step(
        session, run_id=campaign.id, cycle=0, kind="neutronics"
    )
    job = await campaign_service.record_job(
        session, job_id="j8", step_id=step.id, user_id=alice.id, cluster="odo"
    )
    await campaign_service.update_job(session, job_id="j8", state="FAILED")

    async def planner_must_not_run(session, job):  # pragma: no cover - guard
        raise AssertionError("there is nothing to parse in a failed job")

    collect = wiring.build_collector(planner_must_not_run)
    await collect(session, job, "STATE=FAILED\nOUT_OF_MEMORY at step 3", False)

    refreshed = await campaign_service.get_step(session, step.id)
    assert refreshed.status == "failed"
    assert refreshed.result["state"] == "FAILED"
    assert "OUT_OF_MEMORY at step 3" in refreshed.result["outputs"]


# --------------------------------------------------------------------------- #
# What a debate is allowed to run
# --------------------------------------------------------------------------- #


def test_runnable_jobs_are_the_projects_own_skills(tmp_path):
    """
    A debate is scoped to a project, so its simulations are the project's loaded
    skills — there is no separate allowlist to drift out of sync with what the
    project is actually for.
    """
    for name in ("salt-neutronics-tbr", "salt-chemistry-md", "forge-tune"):
        (tmp_path / name).mkdir()

    assert simulation.runnable_jobs(
        ["salt-neutronics-tbr", "salt-chemistry-md"], tmp_path
    ) == ["salt-chemistry-md", "salt-neutronics-tbr"]


def test_a_job_the_project_has_not_loaded_is_not_runnable(tmp_path):
    """`forge-tune` exists in the catalog; a salt project still may not run it."""
    for name in ("salt-neutronics-tbr", "forge-tune"):
        (tmp_path / name).mkdir()
    assert simulation.runnable_jobs(["salt-neutronics-tbr"], tmp_path) == [
        "salt-neutronics-tbr"
    ]


def test_a_planner_skill_contributes_no_job(tmp_path):
    """
    `splash-planner` is a planner, not a simulation, and has no job directory —
    so it drops out by the same rule rather than needing a special case.
    """
    (tmp_path / "salt-chemistry-md").mkdir()
    assert simulation.runnable_jobs(
        ["splash-planner", "salt-chemistry-md"], tmp_path
    ) == ["salt-chemistry-md"]


def test_a_project_with_no_simulation_skills_can_run_nothing(tmp_path):
    (tmp_path / "salt-chemistry-md").mkdir()
    assert simulation.runnable_jobs(["salt-prediction"], tmp_path) == []
    assert simulation.runnable_jobs([], tmp_path) == []


def test_a_missing_catalog_is_not_a_crash(tmp_path):
    assert simulation.runnable_jobs(["salt-chemistry-md"], tmp_path / "nope") == []


# --------------------------------------------------------------------------- #
# Credentials decide which clusters exist
# --------------------------------------------------------------------------- #


class _User:
    def __init__(self, **kw):
        self.odo_s3m_token = kw.get("odo_s3m_token")
        self.frontier_s3m_token = kw.get("frontier_s3m_token")
        self.nersc_iri_token = kw.get("nersc_iri_token")


def test_each_olcf_token_unlocks_only_its_own_machine():
    """An S3M token belongs to one OLCF project, so it reaches one cluster."""
    assert simulation.clusters_for(_User(odo_s3m_token="t")) == ["odo"]
    assert simulation.clusters_for(_User(frontier_s3m_token="t")) == ["frontier"]
    both = _User(odo_s3m_token="t", frontier_s3m_token="t")
    assert simulation.clusters_for(both) == ["frontier", "odo"]


def test_the_legacy_shared_token_unlocks_nothing():
    legacy = _User()
    legacy.s3m_token = "t"  # the old single field, no longer read anywhere
    assert simulation.clusters_for(legacy) == []


def test_the_nersc_token_unlocks_perlmutter():
    assert simulation.clusters_for(_User(nersc_iri_token="t")) == ["perlmutter"]


def test_no_tokens_means_no_clusters():
    """
    Which is why the tool is not granted at all in that case: a model handed a
    tool that can only fail keeps calling it until its request budget is gone.
    """
    assert simulation.clusters_for(_User()) == []


# --------------------------------------------------------------------------- #
# The budget
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_commissioned_count_includes_finished_jobs(client, session, alice):
    """
    The cap is on what a debate may spend, and a finished job still spent it —
    counting only the open ones would let a debate submit without limit as long
    as it waited.
    """
    run, participant = await _debate(client, session, alice)
    first = await _commission(client, session, alice, run, participant, FakeHpc("a"))
    await _commission(client, session, alice, run, participant, FakeHpc("b"))

    await campaign_service.update_job(
        session, job_id=first.job_id, result_collected=True, state="COMPLETED"
    )

    assert await open_simulations(session, debate_run_id=run.id) != []
    assert await simulation.commissioned_count(session, debate_run_id=run.id) == 2


@pytest.mark.anyio
async def test_commissioned_count_is_scoped_to_one_debate(client, session, alice):
    first, p1 = await _debate(client, session, alice)
    await _commission(client, session, alice, first, p1, FakeHpc("a"))
    second, _ = await _debate(client, session, alice)

    assert await simulation.commissioned_count(session, debate_run_id=first.id) == 1
    assert await simulation.commissioned_count(session, debate_run_id=second.id) == 0


# --------------------------------------------------------------------------- #
# The live wiring
# --------------------------------------------------------------------------- #


async def _wired(session, alice, monkeypatch, tmp_path, *, skills, tokens):
    """A debate whose project and opener are set up for `build_simulation`."""
    from vista_backend.agents.forum import wiring
    from vista_backend.config import settings as app_settings
    from vista_backend.db.schemas import UserTable

    # Each job carries a `cluster_defaults.json`, as every real one does, and they
    # deliberately support *different* clusters. A catalogue of bare directories
    # cannot express the thing that broke: a job being offered on a cluster it has
    # no section for.
    catalogue = {
        "salt-neutronics-tbr": ["odo", "perlmutter"],
        "salt-chemistry-md": ["frontier"],
        "forge-tune": ["frontier", "odo", "perlmutter"],
    }
    for name, clusters in catalogue.items():
        (tmp_path / name).mkdir(exist_ok=True)
        (tmp_path / name / "cluster_defaults.json").write_text(
            json.dumps({c: {"nodes": 1} for c in clusters}), encoding="utf-8"
        )
    monkeypatch.setattr(app_settings, "hpc_jobs_dir", tmp_path)

    user = await session.get(UserTable, alice.id)
    for field, value in tokens.items():
        setattr(user, field, value)
    session.add(user)

    project = ProjectTable(name=f"wired-{uuid.uuid4().hex[:8]}", skills=skills)
    session.add(project)
    await session.flush()

    run = await debate_service.create_debate(
        session,
        project_id=project.id,
        user_id=alice.id,
        topic="t",
        thread_id="t1",
        rounds=1,
    )
    # Committed, not flushed. The commissioner opens its own session so that a
    # waiting role does not hold the debate's transaction across a cluster job,
    # and that session cannot see rows this one has not committed — which in
    # production is guaranteed, because `open_debate` commits before spawning the
    # task that runs the debate.
    run_id = run.id
    await session.commit()
    # The commit expired every object this session held, `run` included, so it is
    # re-read rather than handed back stale.
    run = await debate_service.require_debate(session, run_id)

    # The MCP boundary is the one thing a test cannot have.
    monkeypatch.setattr(wiring, "build_mcp_invoke", lambda user, paths: None)
    monkeypatch.setattr(wiring, "McpHpcTools", lambda invoke: FakeHpc())
    return wiring, run


@pytest.mark.anyio
async def test_the_tool_is_wired_when_the_project_and_user_allow_it(
    session, alice, monkeypatch, tmp_path
):
    wiring, run = await _wired(
        session,
        alice,
        monkeypatch,
        tmp_path,
        skills=["salt-neutronics-tbr", "splash-planner"],
        tokens={"odo_s3m_token": "tok", "frontier_s3m_token": "tok"},
    )
    commissioner, runnable = await wiring.build_simulation(session, run)

    assert commissioner is not None
    # The planner skill contributes no job, and the neutronics job is offered only
    # on odo — the OLCF credential also reaches frontier, but that job has no
    # frontier section, and offering it there is what launched nothing.
    assert runnable == {"salt-neutronics-tbr": ["odo"]}


@pytest.mark.anyio
async def test_no_tool_without_credentials(session, alice, monkeypatch, tmp_path):
    """Never grant a tool that can only fail — the lesson from the usage-limit bug."""
    wiring, run = await _wired(
        session,
        alice,
        monkeypatch,
        tmp_path,
        skills=["salt-neutronics-tbr"],
        tokens={
            "odo_s3m_token": None,
            "frontier_s3m_token": None,
            "nersc_iri_token": None,
        },
    )
    commissioner, runnable = await wiring.build_simulation(session, run)
    assert commissioner is None and runnable == {}


@pytest.mark.anyio
async def test_no_tool_when_the_project_has_no_simulation_skills(
    session, alice, monkeypatch, tmp_path
):
    wiring, run = await _wired(
        session,
        alice,
        monkeypatch,
        tmp_path,
        skills=["salt-prediction"],
        tokens={"odo_s3m_token": "tok", "frontier_s3m_token": "tok"},
    )
    commissioner, _ = await wiring.build_simulation(session, run)
    assert commissioner is None


@pytest.mark.anyio
async def test_the_commissioner_refuses_a_job_outside_the_project(
    session, alice, monkeypatch, tmp_path
):
    """`forge-tune` is in the catalog; a salt project still may not reach it."""
    wiring, run = await _wired(
        session,
        alice,
        monkeypatch,
        tmp_path,
        skills=["salt-neutronics-tbr"],
        tokens={"odo_s3m_token": "tok", "frontier_s3m_token": "tok"},
    )
    commissioner, _ = await wiring.build_simulation(session, run)

    with pytest.raises(simulation.BadCommission, match="not runnable in this project"):
        await commissioner(participant=PARTICIPANT, job="forge-tune", prediction="p")


@pytest.mark.anyio
async def test_the_commissioner_refuses_a_cluster_without_credentials(
    session, alice, monkeypatch, tmp_path
):
    wiring, run = await _wired(
        session,
        alice,
        monkeypatch,
        tmp_path,
        skills=["salt-neutronics-tbr"],
        tokens={
            "odo_s3m_token": "tok",
            "frontier_s3m_token": "tok",
            "nersc_iri_token": None,
        },
    )
    commissioner, _ = await wiring.build_simulation(session, run)

    # Refused because *this job* cannot run there for this user: the job supports
    # odo and perlmutter, and the opener has no NERSC credential.
    with pytest.raises(simulation.BadCommission, match="cannot run on 'perlmutter'"):
        await commissioner(
            participant=PARTICIPANT,
            job="salt-neutronics-tbr",
            prediction="p",
            cluster="perlmutter",
        )


@pytest.mark.anyio
async def test_the_budget_stops_a_third_simulation(
    session, alice, monkeypatch, tmp_path
):
    """Two per debate: test the sharpest prediction, not everything it wonders about."""
    from vista_backend.config import settings as app_settings

    wiring, run = await _wired(
        session,
        alice,
        monkeypatch,
        tmp_path,
        skills=["salt-neutronics-tbr"],
        tokens={"odo_s3m_token": "tok", "frontier_s3m_token": "tok"},
    )
    assert app_settings.forum.max_simulations == 2
    commissioner, _ = await wiring.build_simulation(session, run)

    # `wait=False`: this is about the cap, and waiting for a job the fake never
    # finishes would stall the test for `max_job_wait_seconds`.
    for _ in range(2):
        await commissioner(
            participant=PARTICIPANT,
            job="salt-neutronics-tbr",
            prediction="p",
            wait=False,
        )

    with pytest.raises(simulation.BudgetSpent, match="already commissioned 2 of 2"):
        await commissioner(
            participant=PARTICIPANT, job="salt-neutronics-tbr", prediction="p"
        )


# --------------------------------------------------------------------------- #
# How to invoke a job
# --------------------------------------------------------------------------- #


def _job_with_readme(tmp_path, name: str, readme: str) -> Path:
    catalog = tmp_path / "hpc_jobs"
    (catalog / name).mkdir(parents=True, exist_ok=True)
    (catalog / name / "README.md").write_text(readme, encoding="utf-8")
    return catalog


def test_a_jobs_usage_comes_from_its_own_readme(tmp_path):
    """
    The fix for two submissions that died on invented flags.

    `--salt flibe_90Li6 --geometry arc_lib --multiplier beberyllide_nearwall_30cm`
    and `--composition-sweep --bef2-mol-pct 10,20,33`, against a script whose
    options are `--bef2`, `--li6` and friends. argparse exits 2 on an unknown
    option, so each cost a real submission and one of the debate's two permitted
    runs to print a usage message to a stream nothing fetched.
    """
    catalog = _job_with_readme(
        tmp_path,
        "salt-neutronics-tbr",
        "# TBR\n\nRuns one state point.\n\n"
        "## Arguments\n"
        "- Composition (exactly one): `--bef2 P` | `--be-multiplier M`\n"
        "- `--li6 E`  Li-6 enrichment atom fraction\n\n"
        "Prose about neutronics that mentions no options at all.\n\n"
        '        script_args="--bef2 33.33 --li6 0.075")\n',
    )
    usage = simulation.usage_for_job("salt-neutronics-tbr", catalog)

    assert "--bef2" in usage
    assert "--li6" in usage
    assert 'script_args="--bef2 33.33 --li6 0.075")' in usage
    assert "Prose about neutronics" not in usage, "only the lines about arguments"


def test_a_job_with_no_readme_offers_no_usage(tmp_path):
    """Silence, not a guess. A job we cannot document is not a job to invent flags for."""
    catalog = tmp_path / "hpc_jobs"
    (catalog / "undocumented").mkdir(parents=True)
    assert simulation.usage_for_job("undocumented", catalog) == ""
    assert simulation.usage_for(["undocumented"], catalog) == {}


def test_a_long_usage_is_capped(tmp_path):
    from vista_backend.agents.forum.simulation import USAGE_CHARS

    catalog = _job_with_readme(
        tmp_path,
        "verbose",
        "\n".join(f"- `--flag-{i} V` describes it" for i in range(200)),
    )
    usage = simulation.usage_for_job("verbose", catalog)
    assert len(usage) <= USAGE_CHARS + 60
    assert "see the job's README" in usage


def test_usage_covers_only_the_jobs_asked_for(tmp_path):
    catalog = _job_with_readme(tmp_path, "wanted", "- `--wanted-flag V`")
    _job_with_readme(tmp_path, "unwanted", "- `--unwanted-flag V`")
    usage = simulation.usage_for(["wanted"], catalog)
    assert set(usage) == {"wanted"}


# Federation used to be reconciled here, deployment-wide, at boot. It is now a
# property of each project — see tests/test_project_forum.py, which pins the
# remote being applied and checked with a sync against `ensure_forum`.


@pytest.mark.anyio
async def test_commissioned_runs_report_state_not_just_that_a_job_was_sent(
    client, session, alice
):
    """
    The gap a reader actually hit: a post's provenance proves a job was
    *submitted*, and there it ended. Whether it ran, failed, or is still queued
    lives on the campaign side, so a job id in a chip was the end of the trail.

    `last_polled_at` is reported for the same reason. A job nothing has looked at
    since submission is not a job running slowly, and the state alone cannot tell
    the two apart — which is exactly the case that prompted this.
    """
    from vista_backend.agents.forum.simulation import commissioned_runs

    run, participant = await _debate(client, session, alice)
    await _commission(client, session, alice, run, participant, FakeHpc("57719697"))

    (record,) = await commissioned_runs(session, debate_run_id=run.id)

    assert record.job_id == "57719697"
    assert record.job_name == "flibe-viscosity"
    assert record.commissioned_by == REVIEWER_ID
    assert record.prediction == "No shear-rate dependence below 1/s"
    assert record.result_collected is False
    assert record.last_polled_at is None, "nothing has polled it yet, and that shows"


@pytest.mark.anyio
async def test_commissioned_runs_keep_reporting_a_finished_job(client, session, alice):
    """
    Unlike `open_simulations`, which lists only jobs still out, this is
    for reading — and "it finished and posted nothing" is precisely the state
    worth seeing. Dropping completed runs would hide it.
    """
    from vista_backend.agents.forum.simulation import (
        commissioned_runs,
        open_simulations,
    )

    run, participant = await _debate(client, session, alice)
    job = await _commission(client, session, alice, run, participant, FakeHpc("job-z"))

    await campaign_service.update_job(
        session, job_id=job.job_id, state="COMPLETED", result_collected=True
    )

    assert await open_simulations(session, debate_run_id=run.id) == []
    (record,) = await commissioned_runs(session, debate_run_id=run.id)
    assert record.result_collected is True


# --------------------------------------------------------------------------- #
# Waiting for the answer
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_waiting_returns_the_collected_output(engine, client, session, alice):
    """
    The point of waiting: the role that asked the question gets the answer while
    it is still its turn to speak.
    """
    from vista_backend.agents.forum.simulation import wait_for_result

    run, participant = await _debate(client, session, alice)
    job = await _commission(client, session, alice, run, participant, FakeHpc("job-w"))
    await campaign_service.update_step(
        session,
        step_id=job.step_id,
        status="completed",
        result={"state": "COMPLETED", "outputs": "TBR = 1.14"},
    )
    await session.commit()

    outcome = await wait_for_result(
        lambda: AsyncSession(engine), job_id="job-w", timeout=5, poll_seconds=0.01
    )

    assert outcome.finished and outcome.ok
    assert outcome.outputs == "TBR = 1.14"
    assert outcome.timed_out is False


@pytest.mark.anyio
async def test_giving_up_is_not_the_same_as_failing(engine, client, session, alice):
    """
    A job that has not finished is not a job that failed, and a role told
    otherwise would argue from a false negative — treating "no answer yet" as
    evidence against its own prediction.
    """
    from vista_backend.agents.forum.simulation import wait_for_result

    run, participant = await _debate(client, session, alice)
    await _commission(client, session, alice, run, participant, FakeHpc("job-slow"))
    await session.commit()

    outcome = await wait_for_result(
        lambda: AsyncSession(engine), job_id="job-slow", timeout=0.05, poll_seconds=0.01
    )

    assert outcome.timed_out is True
    assert outcome.finished is False
    assert outcome.ok is None, "unknown, not False"


@pytest.mark.anyio
async def test_a_failed_job_is_reported_as_failed(engine, client, session, alice):
    from vista_backend.agents.forum.simulation import wait_for_result

    run, participant = await _debate(client, session, alice)
    job = await _commission(client, session, alice, run, participant, FakeHpc("job-x"))
    await campaign_service.update_step(
        session,
        step_id=job.step_id,
        status="failed",
        result={"state": "FAILED", "outputs": "segfault"},
    )
    await session.commit()

    outcome = await wait_for_result(
        lambda: AsyncSession(engine), job_id="job-x", timeout=5, poll_seconds=0.01
    )
    assert outcome.finished and outcome.ok is False
    assert outcome.state == "FAILED"


@pytest.mark.anyio
async def test_waiting_leaves_the_database_writable(engine, client, session, alice):
    """
    The deadlock this design exists to avoid.

    A waiting role is waiting for the *monitor* to record a result. If the wait
    held a transaction open, on SQLite the monitor could not write that update —
    the debate would wait for something it was itself blocking. So each poll opens
    and closes its own session, and this proves another writer can get in while a
    wait is in flight.
    """
    import asyncio

    from vista_backend.agents.forum.simulation import wait_for_result

    run, participant = await _debate(client, session, alice)
    job = await _commission(client, session, alice, run, participant, FakeHpc("job-c"))
    step_id = job.step_id
    await session.commit()

    waiting = asyncio.create_task(
        wait_for_result(
            lambda: AsyncSession(engine),
            job_id="job-c",
            timeout=10,
            poll_seconds=0.01,
        )
    )
    await asyncio.sleep(0.05)  # let the wait get going

    # Stand in for the monitor: a different session, writing mid-wait.
    async with AsyncSession(engine) as monitor:
        await campaign_service.update_step(
            step_id=step_id,
            session=monitor,
            status="completed",
            result={"state": "COMPLETED", "outputs": "TBR = 1.09"},
        )
        await monitor.commit()

    outcome = await waiting
    assert outcome.outputs == "TBR = 1.09", (
        "the wait never saw the monitor's write, which means it was blocking it"
    )


@pytest.mark.anyio
async def test_no_monitor_means_no_wait_and_no_promise(
    session, alice, monkeypatch, tmp_path
):
    """
    Nothing polls jobs unless the campaign monitor is running, and it is off by
    default — which is how five jobs sat at `submitted` for three days.

    Two things follow, and both were wrong before. Waiting for a collector that
    does not exist can only time out, stalling a debate for `max_job_wait_seconds` per
    job. And telling the agent "the result will be posted when it finishes" writes
    a promise the deployment cannot keep into the permanent record of the debate.
    """
    from vista_backend.config import settings as app_settings

    wiring, run = await _wired(
        session,
        alice,
        monkeypatch,
        tmp_path,
        skills=["salt-neutronics-tbr"],
        tokens={"odo_s3m_token": "tok", "frontier_s3m_token": "tok"},
    )
    monkeypatch.setattr(app_settings.campaigns, "monitor_enabled", False)
    commissioner, _ = await wiring.build_simulation(session, run)

    outcome = await commissioner(
        participant=PARTICIPANT, job="salt-neutronics-tbr", prediction="p"
    )

    assert outcome.uncollectable is True
    assert outcome.timed_out is False, "it did not time out; it was never watched"
    assert outcome.finished is False


@pytest.mark.anyio
async def test_the_tool_says_plainly_that_nothing_will_collect_the_job():
    """
    The agent has to be told, or it argues as though a test is running when
    nothing is watching it — and commissions another with the same outcome.
    """
    from vista_backend.agents.forum.grounding import Grounding, build_toolset
    from vista_backend.agents.forum.roles import DebateDeps
    from vista_backend.agents.forum.simulation import JobOutcome

    async def commissioner(*, participant, job, prediction, **kw):
        return JobOutcome(job_id="job-99", uncollectable=True)

    toolset = build_toolset("reviewer", Grounding(simulation=commissioner))
    assert toolset is not None

    from pydantic_ai import RunContext
    from pydantic_ai.usage import RunUsage

    deps = DebateDeps(topic="t", participant=PARTICIPANT)
    ctx = RunContext(deps=deps, model=None, usage=RunUsage())  # type: ignore[arg-type]
    out = await toolset.tools["commission_simulation"].function(
        ctx, job="salt-neutronics-tbr", prediction="p"
    )

    assert "no job monitor running" in out
    assert "will not be posted" in out
    assert "Do not commission more work" in out
    (call,) = deps.tool_calls
    assert "nothing is polling it" in call.detail, (
        "the record has to show it too, not just the agent's transcript"
    )


@pytest.mark.anyio
async def test_the_default_cluster_is_one_the_job_can_actually_run_on(
    session, alice, monkeypatch, tmp_path
):
    """
    The bug that launched nothing.

    The opener has credentials for frontier, odo and perlmutter. `clusters_for`
    returns them sorted, and the default used to be `clusters[0]` — frontier,
    picked alphabetically, with no reference to the job. `salt-neutronics-tbr` has
    no frontier section, so `submit_hpc_job` refused it, twice, and the agent had
    been given no way to know which cluster to ask for instead.
    """
    wiring, run = await _wired(
        session,
        alice,
        monkeypatch,
        tmp_path,
        skills=["salt-neutronics-tbr"],
        tokens={
            "odo_s3m_token": "tok",
            "frontier_s3m_token": "tok",
            "nersc_iri_token": "tok",
        },
    )
    commissioner, runnable = await wiring.build_simulation(session, run)

    assert runnable == {"salt-neutronics-tbr": ["odo", "perlmutter"]}
    assert "frontier" not in runnable["salt-neutronics-tbr"], (
        "frontier is reachable for this user but this job has no section for it"
    )

    outcome = await commissioner(
        participant=PARTICIPANT,
        job="salt-neutronics-tbr",
        prediction="TBR > 1.1",
        wait=False,
    )
    job = await campaign_service.get_job(session, outcome.job_id)
    assert job is not None, "the job should have been submitted"
    assert job.cluster == "odo", "defaulted to a cluster the job supports"


@pytest.mark.anyio
async def test_a_frontier_only_job_is_offered_on_frontier(
    session, alice, monkeypatch, tmp_path
):
    """
    The same rule the other way round, which is why it cannot be a fixed default:
    `salt-chemistry-md` runs *only* on frontier, so alphabetical order was right
    for it by luck and wrong for the neutronics job.
    """
    wiring, run = await _wired(
        session,
        alice,
        monkeypatch,
        tmp_path,
        skills=["salt-chemistry-md"],
        tokens={
            "odo_s3m_token": "tok",
            "frontier_s3m_token": "tok",
            "nersc_iri_token": "tok",
        },
    )
    _commissioner, runnable = await wiring.build_simulation(session, run)
    assert runnable == {"salt-chemistry-md": ["frontier"]}


@pytest.mark.anyio
async def test_a_job_with_no_reachable_cluster_is_not_offered_at_all(
    session, alice, monkeypatch, tmp_path
):
    """
    Never grant what can only fail. A NERSC-only opener cannot run a frontier-only
    job, so it is dropped rather than offered and refused on every attempt.
    """
    wiring, run = await _wired(
        session,
        alice,
        monkeypatch,
        tmp_path,
        skills=["salt-chemistry-md"],
        tokens={
            "odo_s3m_token": None,
            "frontier_s3m_token": None,
            "nersc_iri_token": "tok",
        },
    )
    commissioner, runnable = await wiring.build_simulation(session, run)

    assert runnable == {}
    assert commissioner is None, "a tool that can only be refused is not granted"


# --------------------------------------------------------------------------- #
# A refusal only a human can clear
# --------------------------------------------------------------------------- #


def _ctx(participant, **deps_kwargs):
    from pydantic_ai import RunContext
    from pydantic_ai.usage import RunUsage

    from vista_backend.agents.forum.roles import DebateDeps

    return RunContext(  # type: ignore[arg-type]
        deps=DebateDeps(topic="t", participant=participant, **deps_kwargs),
        model=None,
        usage=RunUsage(),
    )


def _sim_tool(commissioner):
    from vista_backend.agents.forum.grounding import Grounding, build_toolset

    toolset = build_toolset("proposer", Grounding(simulation=commissioner))
    assert toolset is not None
    return toolset.tools["commission_simulation"].function


@pytest.mark.anyio
async def test_a_credential_refusal_is_not_asked_twice(client, session, alice):
    """
    Retrying a token scoped to the wrong project only spends the request budget.

    This is what the live forum did: an S3M token minted for `chm243` was refused
    by odo, which wants `gen150-vista`, and the role asked again with identical
    arguments and was refused identically. Two of twelve requests for one fact
    that was already on the table.
    """
    calls = []

    async def commissioner(**kwargs):
        calls.append(kwargs["cluster"])
        raise RuntimeError(
            "Your S3M token belongs to project 'chm243', but odo access "
            "requires 'gen150-vista'."
        )

    tool = _sim_tool(commissioner)
    _, participant = await _debate(client, session, alice)
    ctx = _ctx(participant, runnable={"salt-neutronics-tbr": ["odo", "perlmutter"]})

    first = await tool(ctx, job="salt-neutronics-tbr", prediction="p", cluster="odo")
    assert "gen150-vista" in first
    assert "only a human can" in first

    second = await tool(ctx, job="salt-neutronics-tbr", prediction="p", cluster="odo")
    assert calls == ["odo"], "the second attempt never reached the cluster"
    assert "already tried" in second
    assert "gen150-vista" in second, "and it still says why"


@pytest.mark.anyio
async def test_another_cluster_is_still_worth_trying(client, session, alice):
    """
    The memo is per (job, cluster), not per turn.

    A job that odo refuses may well run on perlmutter, and blocking the whole turn
    on the first refusal would throw away the debate's other credential.
    """
    calls = []

    async def commissioner(**kwargs):
        calls.append(kwargs["cluster"])
        raise RuntimeError("no credentials")

    tool = _sim_tool(commissioner)
    _, participant = await _debate(client, session, alice)
    ctx = _ctx(participant, runnable={"salt-neutronics-tbr": ["odo", "perlmutter"]})

    await tool(ctx, job="salt-neutronics-tbr", prediction="p", cluster="odo")
    await tool(ctx, job="salt-neutronics-tbr", prediction="p", cluster="perlmutter")
    assert calls == ["odo", "perlmutter"]


@pytest.mark.anyio
async def test_the_default_cluster_is_the_same_attempt_as_naming_it(
    client, session, alice
):
    """
    Otherwise omitting an argument is a way to retry a dead credential.

    The commissioner sends an unnamed cluster to the job's first, so `cluster=None`
    and `cluster="odo"` are one attempt against one machine; keyed separately they
    would be two, and the block would be trivially evaded.
    """
    calls = []

    async def commissioner(**kwargs):
        calls.append(kwargs["cluster"])
        raise RuntimeError("no credentials")

    tool = _sim_tool(commissioner)
    _, participant = await _debate(client, session, alice)
    ctx = _ctx(participant, runnable={"salt-neutronics-tbr": ["odo", "perlmutter"]})

    await tool(ctx, job="salt-neutronics-tbr", prediction="p")
    out = await tool(ctx, job="salt-neutronics-tbr", prediction="p", cluster="odo")
    assert calls == [None], "naming the default is not a new attempt"
    assert "already tried" in out


@pytest.mark.anyio
async def test_a_mistyped_job_can_be_corrected(client, session, alice):
    """
    The one refusal worth another go, so it must *not* be remembered.

    A wrong name is the role's own mistake and a corrected call works. Memoising
    it would turn a typo into a dead end for the rest of the turn — and the reply
    says what this debate can actually run, so the correction is one the role is
    able to make.
    """
    calls = []

    async def commissioner(**kwargs):
        calls.append(kwargs["job"])
        raise simulation.BadCommission("'md' is not runnable in this project.")

    tool = _sim_tool(commissioner)
    _, participant = await _debate(client, session, alice)
    ctx = _ctx(participant, runnable={"salt-neutronics-tbr": ["odo", "perlmutter"]})

    out = await tool(ctx, job="md", prediction="p")
    assert "salt-neutronics-tbr (on odo or perlmutter)" in out
    assert "only a human can" not in out

    await tool(ctx, job="md", prediction="p")
    assert calls == ["md", "md"], "a correctable refusal is not a dead end"


@pytest.mark.anyio
async def test_a_spent_budget_says_to_stop_rather_than_to_retry(client, session, alice):
    """No later call can work either, and the reply has to say so."""

    async def commissioner(**kwargs):
        raise simulation.BudgetSpent("already commissioned 2 of 2 permitted.")

    tool = _sim_tool(commissioner)
    _, participant = await _debate(client, session, alice)
    ctx = _ctx(participant, runnable={"salt-neutronics-tbr": ["odo"]})

    out = await tool(ctx, job="salt-neutronics-tbr", prediction="p")
    assert "do not call this tool again" in out
    assert "2 of 2" in out


@pytest.mark.anyio
async def test_the_receipt_names_the_cluster_the_job_went_to(client, session, alice):
    """
    "the default cluster" is not provenance.

    The receipt is how a reader checks that the run behind a FINDING is the run
    that was claimed, and a role that leaves the cluster to the default was the
    common case.
    """

    async def commissioner(**kwargs):
        return simulation.JobOutcome(
            job_id="j-1", state="COMPLETED", finished=True, ok=True, outputs="TBR 1.05"
        )

    tool = _sim_tool(commissioner)
    _, participant = await _debate(client, session, alice)
    ctx = _ctx(participant, runnable={"salt-neutronics-tbr": ["odo", "perlmutter"]})

    await tool(ctx, job="salt-neutronics-tbr", prediction="p")
    (call,) = ctx.deps.tool_calls
    assert "odo" in call.detail
    assert "cluster:    odo" in (call.receipt or "")
