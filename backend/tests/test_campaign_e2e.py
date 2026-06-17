"""
End-to-end test of the generic campaign framework over a mock domain.

Composes the real services + planner + monitor + wiring (only the HPC boundary and
email are faked) to prove the full loop:

    create -> plan -> dispatch a candidate (neutronics + chemistry in parallel)
    -> [restart] a fresh monitor resumes the durable open jobs
    -> poll (PENDING then COMPLETED) -> collect -> email -> steps completed
    -> user confirms exit -> campaign no longer resumable
"""
import pytest

from vista_backend.agents.campaign.manifest import CampaignManifest
from vista_backend.agents.campaign.planner import CampaignPlanner, build_subagents
from vista_backend.agents.campaign.subagent import (
    CallableResultParser,
    ParsedResult,
    SubmittedJobInfo,
)
from vista_backend.agents.campaign.wiring import build_collector
from vista_backend.db.schemas import ProjectTable
from vista_backend.services import campaign as campaign_service
from vista_backend.services.campaign_monitor import CampaignMonitor, resume_open_campaigns


MANIFEST = CampaignManifest.model_validate(
    {
        "domain": "mockdomain",
        "metrics": {"primary": {"name": "score", "target": 1.0, "direction": "maximize"}},
        "subagents": [
            {"role": "neutronics", "skill": "mock-neutronics", "job": "neutronics"},
            {"role": "chemistry", "skill": "mock-chemistry", "job": "chemistry"},
        ],
    }
)


class FakeHpc:
    """Increments job ids so a candidate's two role-jobs get distinct PKs."""
    def __init__(self):
        self.n = 0

    async def submit(self, *, job, cluster, node_count, duration, script_args):
        self.n += 1
        return SubmittedJobInfo(job_id=f"job-{self.n}", cluster=cluster or "frontier")

    async def status(self, *, job_id, cluster):
        return "STATE=COMPLETED"

    async def fetch_outputs(self, *, job_id, files, cluster):
        return ""


def _parser_factory(skill_dir, role):
    """Deterministic parser: derive a per-role metric from the candidate."""
    def parse(*, candidate, raw_status, raw_outputs):
        return ParsedResult(
            ok=True, summary=role, metrics={"score": (candidate or {}).get("x", 0.0), "role": role}
        )

    return CallableResultParser(parse)


def _build_planner() -> CampaignPlanner:
    # Fresh objects each call — simulates rebuilding the planner after a restart.
    subagents = build_subagents(
        MANIFEST, hpc=FakeHpc(), skills_dir="/unused", parser_factory=_parser_factory
    )
    return CampaignPlanner(manifest=MANIFEST, subagents=subagents)


class _Emailer:
    def __init__(self):
        self.sent = []

    async def __call__(self, *, to, subject, body):
        self.sent.append({"to": to, "subject": subject})
        return True


@pytest.mark.anyio
async def test_campaign_end_to_end(session, alice):
    # --- intake + plan -----------------------------------------------------
    project = ProjectTable(name="e2e-project")
    session.add(project)
    await session.flush()
    run = await campaign_service.create_campaign(
        session, project_id=project.id, user_id=alice.id,
        domain="mockdomain", planner_skill="mock-planner", title="Mock sweep",
    )
    await campaign_service.save_plan(session, run_id=run.id, plan=[{"step": 1, "text": "cycle 0"}])
    await campaign_service.set_status(session, run_id=run.id, status="running")

    # --- dispatch one candidate (both roles, in parallel) ------------------
    planner = _build_planner()
    job_ids = await planner.dispatch_candidate(
        session, run_id=run.id, user_id=alice.id, candidate={"x": 0.7}, cycle=0, cluster="frontier",
    )
    assert sorted(job_ids) == ["job-1", "job-2"]
    assert len(await campaign_service.list_open_jobs(session)) == 2
    assert run.id in {r.id for r in await resume_open_campaigns(session)}

    # --- restart: a brand-new monitor + freshly-rebuilt planner pick up the durable jobs ---
    emailer = _Emailer()
    state_box = {"state": "PENDING"}

    async def poll(job):
        return state_box["state"], f"STATE={state_box['state']}"

    async def planner_provider(_session, _job):
        return _build_planner()

    monitor = CampaignMonitor(
        poll=poll, collect=build_collector(planner_provider), send_email=emailer
    )

    # First tick: still queued — nothing completes, nothing emailed.
    await monitor.reconcile_once(session)
    assert len(await campaign_service.list_open_jobs(session)) == 2
    assert emailer.sent == []
    assert all(s.status == "dispatched" for s in await campaign_service.list_steps(session, run_id=run.id))

    # Jobs finish; next tick collects, completes the steps, and emails the user.
    state_box["state"] = "COMPLETED"
    await monitor.reconcile_once(session)

    steps = await campaign_service.list_steps(session, run_id=run.id)
    assert {s.kind for s in steps} == {"neutronics", "chemistry"}
    assert all(s.status == "completed" for s in steps)
    assert all(s.result["metrics"]["score"] == 0.7 for s in steps)

    assert len(await campaign_service.list_open_jobs(session)) == 0
    assert len(emailer.sent) == 2
    user_email = (await _user_email(session, alice))
    assert all(m["to"] == user_email for m in emailer.sent)

    # --- user confirms exit -> no longer resumable -------------------------
    await campaign_service.set_status(session, run_id=run.id, status="exited")
    assert run.id not in {r.id for r in await resume_open_campaigns(session)}


async def _user_email(session, alice):
    from vista_backend.db.schemas import UserTable

    return (await session.get(UserTable, alice.id)).email
