"""Construction-level tests for ProjectAgent campaign-mode wiring + the monitor factory.

The live paths (real MCP submission, the LLM planner turn, the running monitor loop) need a
live model/MCP/HPC to exercise; here we verify the agent builds with the campaign tools wired,
the driver deps route through the per-run hooks, and the monitor factory + settings construct.
"""

import inspect
import uuid

from vista_backend.agents.agents import ProjectAgent
from vista_backend.agents.campaign.agent_tools import CampaignDriverDeps
from vista_backend.agents.campaign.wiring import build_default_monitor
from vista_backend.config import settings
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
    assert settings.campaigns.monitor_enabled is False
    assert settings.campaigns.monitor_interval == 300.0
