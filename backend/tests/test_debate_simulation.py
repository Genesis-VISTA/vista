"""
Tests for simulation in the loop: a debate commissioning HPC work.

Hermetic — the HPC boundary is a fake and the forum is the fake binary. What is
under test is the awkward part of this feature, which is not submitting a job but
the fact that the job outlives the debate: the roster has to survive long enough
to post the answer, and the monitor has to not throw the job away first.

Anything needing a real cluster belongs behind the `hpc` marker.
"""

import uuid
from pathlib import Path

import pytest

from vista_backend.agents.campaign.subagent import SubmittedJobInfo
from vista_backend.agents.forum import simulation
from vista_backend.agents.forum.simulation import (
    DEBATE_DOMAIN,
    commission,
    format_finding,
    open_simulations,
    post_result,
    reap_after_collection,
)
from vista_backend.config import ForumSettings
from vista_backend.db.schemas import ProjectTable
from vista_backend.services import campaign as campaign_service
from vista_backend.services import debate as debate_service
from vista_backend.services.h5i_forum import ForumClient, ParticipantRole


FAKE = Path(__file__).parent / "fixtures" / "fake_h5i.py"


class FakeHpc:
    """The HPC boundary, without a cluster."""

    def __init__(self, job_id="job-1"):
        self.job_id = job_id
        self.submitted: list[dict] = []

    async def submit(self, *, job, cluster, node_count, duration, script_args):
        self.submitted.append({"job": job, "script_args": script_args})
        return SubmittedJobInfo(
            job_id=self.job_id,
            cluster=cluster or "odo",
            log_path="/logs/x.out",
            output_dir="/out",
        )

    async def collect(self, *args, **kwargs):  # pragma: no cover - unused here
        raise NotImplementedError


@pytest.fixture
def client(tmp_path) -> ForumClient:
    (tmp_path / ".git" / ".h5i").mkdir(parents=True)
    return ForumClient(
        ForumSettings(enabled=True, binary=str(FAKE), repo_root=tmp_path, timeout=30.0),
        confirm_delay=0.0,
    )


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
    participant = await client.create_participant(
        box_slug="reviewer", identity="vista-reviewer", role=ParticipantRole.REVIEWER
    )
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
    assert campaign.spec["commissioned_by"] == "vista-reviewer"
    assert campaign.spec["box_slug"] == participant.box_slug
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
    assert finding.sender == "vista-reviewer"
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
# Keeping the roster alive long enough to answer
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_a_role_with_work_in_flight_is_not_retired(client, session, alice):
    """
    A revoked participant cannot post, so retiring the roster while a job is
    running would silently make its result unpostable — the debate would end
    looking complete and the evidence would never arrive.
    """
    from vista_backend.agents.forum.debate import DebateOrchestrator
    from vista_backend.agents.forum.roles import RoleAgents

    run, participant = await _debate(client, session, alice)
    await _commission(client, session, alice, run, participant)

    orch = DebateOrchestrator(client=client, roles=RoleAgents())
    roster = await orch._participants(session, run.id)  # noqa: SLF001
    await orch._retire(session, run.id, roster)  # noqa: SLF001

    rows = await debate_service.list_participants(session, run_id=run.id)
    assert all(row.active for row in rows), "the roster stays until the job is in"


@pytest.mark.anyio
async def test_the_roster_is_reaped_once_the_last_job_lands(client, session, alice):
    run, participant = await _debate(client, session, alice, status="converged")
    job = await _commission(client, session, alice, run, participant)

    await post_result(session, client, job, state="COMPLETED", ok=True, outputs="flat")
    await campaign_service.update_job(session, job_id=job.job_id, result_collected=True)
    await reap_after_collection(session, client, job)

    rows = await debate_service.list_participants(session, run_id=run.id)
    assert not any(row.active for row in rows)


@pytest.mark.anyio
async def test_a_still_running_debate_keeps_its_own_roster(client, session, alice):
    """
    The reaper must not take a role off the forum mid-argument just because one
    of its jobs finished — the orchestrator owns the roster while it is arguing.
    """
    run, participant = await _debate(client, session, alice, status="debating")
    job = await _commission(client, session, alice, run, participant)
    await campaign_service.update_job(session, job_id=job.job_id, result_collected=True)

    await reap_after_collection(session, client, job)

    rows = await debate_service.list_participants(session, run_id=run.id)
    assert all(row.active for row in rows)


@pytest.mark.anyio
async def test_the_reaper_waits_for_every_outstanding_job(client, session, alice):
    run, participant = await _debate(client, session, alice, status="converged")
    first = await _commission(
        client, session, alice, run, participant, FakeHpc("job-a")
    )
    await _commission(client, session, alice, run, participant, FakeHpc("job-b"))

    await campaign_service.update_job(
        session, job_id=first.job_id, result_collected=True
    )
    await reap_after_collection(session, client, first)

    rows = await debate_service.list_participants(session, run_id=run.id)
    assert all(row.active for row in rows), "job-b is still out"


# --------------------------------------------------------------------------- #
# The tool
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_the_tool_tells_the_agent_not_to_wait(client, session, alice):
    """
    A job outlives a round, so the tool must not read as though it returns an
    answer — an agent that waits for one would stall its own turn.
    """
    from vista_backend.agents.forum.grounding import Grounding, build_toolset
    from vista_backend.agents.forum.roles import DebateDeps

    async def commissioner(
        *, participant, job, prediction, cluster=None, script_args=None, reply_to=None
    ):
        return "job-42"

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
    assert "job-42" in out
    assert "do not wait" in out


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
    for name in ("commission", "post_result", "reap_after_collection", "DEBATE_DOMAIN"):
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

    monkeypatch.setattr(wiring, "build_forum_client", lambda: client)

    async def planner_must_not_run(session, job):  # pragma: no cover - guard
        raise AssertionError("a debate job has no planner to collect through")

    run, participant = await _debate(client, session, alice)
    job = await _commission(client, session, alice, run, participant)

    collect = wiring.build_debate_aware_collector(planner_must_not_run)
    await collect(session, job, "STATE=COMPLETED\nviscosity: flat")

    thread = await client.read_thread(run.thread_id)
    finding = next(p for p in thread.posts if p.kind == "FINDING")
    assert finding.sender == "vista-reviewer"


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
    await collect(session, job, "STATE=COMPLETED")
    assert seen == ["j7"]
