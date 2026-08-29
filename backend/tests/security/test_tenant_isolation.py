"""
Multi-tenancy isolation tests (evaluation plan M11, experiment E15 / P4).

Two users on two projects run concurrent tool-call campaigns. Asserts:
  * per-(user, project) sandbox volume isolation (distinct roots; one
    tenant's skills and artifacts never appear under the other's tree);
  * no cross-project DB records (membership stays disjoint);
  * metric events under concurrency carry each tenant's own correlation
    ids — no cross-tenant mixing in the shared JSONL;
  * a **canary credential** planted in user A's record reaches A's own
    HPC tool metadata (positive control — the channel works) but never
    appears in B's metadata, B's sandbox tree, or the metrics log.

This is the permanent regression home for E15; the loadgen (M6) replays
full dry-run campaigns through the same seams at the HTTP layer.
"""

import asyncio
import json
import uuid
from pathlib import Path

import pytest
from sqlmodel import select

import vista_backend.agents.agents as agents_module
import vista_backend.metrics as metrics_module
from vista_backend.agents.eval_metrics import EvalMetricsCapability
from vista_backend.config import settings
from vista_backend.db.schemas import (
    ProjectMemberTable,
    ProjectPublic,
    ProjectTable,
    SkillTable,
    UserPublicWithConfig,
)
from vista_backend.metrics import MetricsRecorder, MetricsSettings

CANARY = "CANARY-S3M-TOKEN-93b1c7e4ffa2"


def scan_tree(root: Path, needle: str) -> list[Path]:
    """Every file under `root` whose content contains `needle`."""
    hits = []
    for path in root.rglob("*"):
        if path.is_file() and needle in path.read_text(errors="ignore"):
            hits.append(path)
    return hits


@pytest.fixture
def tenant_env(tmp_path, monkeypatch, session):
    """Isolated data_dir + DB engine + a shared perf-level metrics log,
    so two ProjectAgents exercise the real volume/metrics seams."""
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(agents_module, "get_engine", lambda: session.bind)
    recorder = MetricsRecorder(
        MetricsSettings(level="perf"), default_log_path=tmp_path / "metrics.jsonl"
    )
    monkeypatch.setattr(metrics_module, "_recorder", recorder)
    return tmp_path


@pytest.fixture
def alice_with_canary() -> UserPublicWithConfig:
    return UserPublicWithConfig(
        id=uuid.uuid4(), email="alice@example.com", s3m_token=CANARY
    )


@pytest.fixture
def bob_user() -> UserPublicWithConfig:
    return UserPublicWithConfig(id=uuid.uuid4(), email="bob@example.com")


async def seed_skill(session, tenant_env: Path, name: str) -> None:
    """A skill on disk + its DB row, as the skills API would create them."""
    skill_dir = tenant_env / "storage" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: test skill\n---\nbody\n"
    )
    session.add(SkillTable(name=name, description="test skill", path=f"storage/{name}"))
    # Committed, not just flushed: the agent opens its own session, and only a
    # committed row is visible to one. (This passed on a flush while the test
    # database pinned every session to a single shared connection, which made
    # uncommitted rows visible everywhere — true of no real deployment.)
    await session.commit()


async def drive_campaign(agent: agents_module.ProjectAgent, jobs: int) -> list[dict]:
    """Concurrently exercise the real backend tool-call path the way a
    dry-run campaign does: HPC submissions (credential metadata attached)
    interleaved with sandbox calls. Returns the metadata sent per call."""
    captured: list[dict] = []

    async def call_tool(name, args, metadata):
        captured.append({"tool": name, "metadata": metadata})
        await asyncio.sleep(0)  # force interleaving between tenants
        return {"ok": True}

    callback = agent._make_mcp_process_tool_call()
    metrics_capability = EvalMetricsCapability(
        session_id=agent.id,
        project_id=str(agent.project.id),
        skills_loaded=lambda: list(agent.project.skills),
        attribute_skill=agent._attribute_skill,
    )

    async def execute_tool(tool_name: str, args: dict[str, object]):
        async def tool_handler(validated_args):
            return await callback(None, call_tool, tool_name, validated_args)

        return await metrics_capability.wrap_tool_execute(
            None,
            call=None,
            tool_def=type("ToolDef", (), {"name": tool_name})(),
            args=args,
            handler=tool_handler,
        )

    async def handler():
        for i in range(jobs):
            await execute_tool("submit_hpc_job", {"job": i})
            await execute_tool("run_bash", {"cmd": f"echo {i}"})

    await metrics_capability.wrap_run(None, handler=handler)
    return captured


@pytest.mark.anyio
async def test_volume_isolation_and_canary_on_disk(
    tenant_env, session, alice_with_canary, bob_user
):
    await seed_skill(session, tenant_env, "salt-analysis")
    project_a = ProjectPublic(id=uuid.uuid4(), name="a", skills=["salt-analysis"])
    project_b = ProjectPublic(id=uuid.uuid4(), name="b")
    agent_a = agents_module.ProjectAgent(project_a, alice_with_canary, uuid.uuid4())
    agent_b = agents_module.ProjectAgent(project_b, bob_user, uuid.uuid4())

    # Distinct per-session roots under data_dir/volumes.
    assert agent_a.volume_root != agent_b.volume_root
    assert agent_a.volume_root.parent == agent_b.volume_root.parent

    await agent_a._setup_volumes()
    await agent_b._setup_volumes()

    # A's project skills are staged into A's volume only.
    assert (agent_a.skills_volume_dir / "salt-analysis" / "SKILL.md").exists()
    assert not any(agent_b.skills_volume_dir.iterdir())

    # A campaign artifact containing A's credential never crosses trees.
    agent_a.output_dir.mkdir(parents=True, exist_ok=True)
    (agent_a.output_dir / "job.log").write_text(f"submitted with token {CANARY}")
    assert scan_tree(agent_a.volume_root, CANARY)  # positive control
    assert scan_tree(agent_b.volume_root, CANARY) == []


@pytest.mark.anyio
async def test_concurrent_campaigns_isolate_credentials_and_metrics(
    tenant_env, session, alice_with_canary, bob_user
):
    project_a = ProjectPublic(id=uuid.uuid4(), name="a")
    project_b = ProjectPublic(id=uuid.uuid4(), name="b")
    agent_a = agents_module.ProjectAgent(project_a, alice_with_canary, uuid.uuid4())
    agent_b = agents_module.ProjectAgent(project_b, bob_user, uuid.uuid4())

    captured_a, captured_b = await asyncio.gather(
        drive_campaign(agent_a, jobs=5), drive_campaign(agent_b, jobs=5)
    )

    # Positive control: A's own HPC submissions do carry A's token...
    blob_a = json.dumps(captured_a, default=str)
    assert CANARY in blob_a
    # ...but only on HPC tools, never on sandbox calls.
    for call in captured_a:
        if call["tool"] != "submit_hpc_job":
            assert CANARY not in json.dumps(call, default=str)

    # The canary never reaches tenant B on any channel.
    assert CANARY not in json.dumps(captured_b, default=str)

    # The shared metrics log never captures the credential, and every
    # event carries its own tenant's correlation ids — concurrency does
    # not mix streams.
    metrics_text = (tenant_env / "metrics.jsonl").read_text()
    assert CANARY not in metrics_text
    events = [json.loads(line) for line in metrics_text.splitlines()]
    tool_events = [e for e in events if e["event_type"] == "tool_call.client"]
    assert len(tool_events) == 20
    by_session = {agent_a.id: str(project_a.id), agent_b.id: str(project_b.id)}
    assert {e["session_id"] for e in tool_events} == set(by_session)
    for event in tool_events:
        assert event["project_id"] == by_session[event["session_id"]]


@pytest.mark.anyio
async def test_no_cross_project_db_records(session, alice, bob):
    project_a = ProjectTable(name="a")
    project_b = ProjectTable(name="b")
    session.add_all([project_a, project_b])
    await session.flush()
    session.add_all(
        [
            ProjectMemberTable(project_id=project_a.id, user_id=alice.id),
            ProjectMemberTable(project_id=project_b.id, user_id=bob.id),
        ]
    )
    await session.flush()

    members_a = (
        await session.exec(
            select(ProjectMemberTable).where(
                ProjectMemberTable.project_id == project_a.id
            )
        )
    ).all()
    assert [m.user_id for m in members_a] == [alice.id]
    bob_rows = (
        await session.exec(
            select(ProjectMemberTable).where(ProjectMemberTable.user_id == bob.id)
        )
    ).all()
    assert {m.project_id for m in bob_rows} == {project_b.id}
