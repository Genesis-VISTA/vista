"""Construction-level tests for ProjectAgent campaign-mode wiring + the monitor factory.

The live paths (real MCP submission, the LLM planner turn, the running monitor loop) need a
live model/MCP/HPC to exercise; here we verify the agent builds with the campaign tools wired,
the driver deps route through the per-run hooks, and the monitor factory + settings construct.
"""

import inspect
import uuid

import pytest

from vista_backend.agents.agents import ProjectAgent
from vista_backend.agents.campaign.agent_tools import CampaignDriverDeps
from vista_backend.agents.campaign.wiring import build_default_monitor
from vista_backend.db.schemas import ProjectPublic, UserPublicWithConfig
from vista_backend.services.campaign_monitor import CampaignMonitor


def _project_agent() -> tuple[ProjectAgent, ProjectPublic, UserPublicWithConfig]:
    project = ProjectPublic(id=uuid.uuid4(), name="campaign-pa")
    user = UserPublicWithConfig(id=uuid.uuid4(), email="x@ornl.gov")
    return ProjectAgent(project, user, uuid.uuid4()), project, user


def test_run_stream_accepts_db_session_param():
    assert "db_session" in inspect.signature(ProjectAgent.run_stream).parameters


def test_project_agent_builds_with_campaign_tools_wired():
    # Construction runs _build_agent -> register_campaign_tools without error.
    pa, project, user = _project_agent()
    deps = pa._campaign_driver_deps()
    assert isinstance(deps, CampaignDriverDeps)
    assert deps.project_id == project.id
    assert deps.user_id == user.id


def test_driver_deps_route_through_per_run_hooks():
    pa, _project, _user = _project_agent()
    deps = pa._campaign_driver_deps()

    # Session getter reflects the per-run slot set in run_stream.
    assert deps.get_session() is None
    sentinel = object()
    pa._cur_db_session = sentinel
    assert deps.get_session() is sentinel

    # Progress emitter is a no-op until run_stream installs one, then routes to it.
    deps.emit_progress("dropped")
    got: list[str] = []
    pa._cur_progress_emitter = got.append
    deps.emit_progress("hello")
    assert got == ["hello"]


def test_build_default_monitor_constructs():
    assert isinstance(build_default_monitor(), CampaignMonitor)


def test_campaign_settings_default_off():
    """
    The monitor is opt-in: nothing polls HPC jobs until someone turns it on.

    Asserted on a freshly built `CampaignSettings`, not on the process-wide
    `settings`. That object reads the developer's `.env`, so this test used to
    pass or fail on a file outside the repository — and it failed the moment a
    deployment legitimately enabled the monitor, reporting a broken default when
    the default was fine.

    Worth stating rather than just defaulting: with the monitor off, a debate can
    submit a simulation that nothing ever polls, and five of them sat at
    `submitted` for three days before anyone noticed.
    """
    from vista_backend.config import CampaignSettings

    fresh = CampaignSettings()
    assert fresh.monitor_enabled is False
    assert fresh.monitor_interval == 300.0


def test_driver_deps_carry_the_resolved_model():
    """What the launching agent resolved, default or the chat's own, is recorded on its runs."""
    project = ProjectPublic(id=uuid.uuid4(), name="campaign-model")
    user = UserPublicWithConfig(
        id=uuid.uuid4(),
        email="x@ornl.gov",
        inference_provider="mag",
        inference_model="openai:default-model",
    )

    default = ProjectAgent(project, user, uuid.uuid4())
    assert default._campaign_driver_deps().model == "openai:default-model"

    own = ProjectAgent(project, user, uuid.uuid4(), "openai:chat-model")
    assert own._campaign_driver_deps().model == "openai:chat-model"
    assert own._campaign_driver_deps().provider == "mag"


@pytest.mark.anyio
async def test_a_cycle_dispatched_from_the_chat_uses_the_runs_model(monkeypatch):
    """
    Launched on B, the chat moved to C: its next cycle still dispatches on B,
    as the monitor's rebuild does. The chat's agent is rebuilt on C, so only
    the run remembers B.
    """
    from types import SimpleNamespace

    project = ProjectPublic(id=uuid.uuid4(), name="campaign-dispatch")
    user = UserPublicWithConfig(
        id=uuid.uuid4(), email="x@ornl.gov", inference_provider="mag"
    )
    moved = ProjectAgent(project, user, uuid.uuid4(), "openai:c")
    run = SimpleNamespace(id=uuid.uuid4(), model="openai:b", provider="mag")

    built: list[str] = []

    async def planner(self, session, run, *, model=None):
        built.append(model.model_name)

    monkeypatch.setattr(ProjectAgent, "_build_campaign_planner_for_run", planner)
    await moved._campaign_driver_deps().get_planner(None, run)

    assert built == ["b"]
