"""Tests for the live-MCP invoke builder (metadata shaping + result coercion)."""

import json
import uuid

import pytest

from vista_backend.agents.agents import ProjectAgent
from vista_backend.agents.campaign.mcp_invoke import (
    build_invoke,
    build_metadata,
    project_paths_for,
)
from vista_backend.db.schemas import ProjectPublic, UserPublicWithConfig


def test_project_paths_for_matches_project_agent():
    """The monitor rebuilds job paths via project_paths_for; pin them to the agent's
    actual volume dirs (the dispatch side) so the two can't drift and orphan job outputs."""
    project = ProjectPublic(id=uuid.uuid4(), name="paths")
    user = UserPublicWithConfig(id=uuid.uuid4(), email="x@ornl.gov")
    session_id = uuid.uuid4()
    agent = ProjectAgent(project, user, session_id)

    paths = project_paths_for(project.id, user.id)
    assert paths == {
        "skills_dir": str(agent.skills_volume_dir),
        "output_dir": str(agent.output_dir),
        "uploads_dir": str(agent.uploads_dir),
    }


@pytest.mark.anyio
async def test_build_metadata_shape(alice):
    meta = build_metadata(alice, {"output_dir": "/o"})
    assert set(meta) == {"vista"}
    assert meta["vista"]["project_paths"] == {"output_dir": "/o"}
    # user is the json-dumped UserPublicWithConfig (carries email + HPC config fields).
    assert meta["vista"]["user"]["email"] == alice.email


@pytest.mark.anyio
async def test_build_invoke_passes_metadata_and_returns_text(alice):
    calls = []

    async def call_tool(name, args, metadata):
        calls.append((name, args, metadata))
        return "STATE=COMPLETED"

    invoke = build_invoke(call_tool, user=alice, project_paths={"output_dir": "/o"})
    out = await invoke("get_hpc_job_status", {"job_id": "7", "cluster": "odo"})

    assert out == "STATE=COMPLETED"
    name, args, metadata = calls[0]
    assert name == "get_hpc_job_status"
    assert args == {"job_id": "7", "cluster": "odo"}
    assert metadata["vista"]["user"]["email"] == alice.email
    assert metadata["vista"]["project_paths"] == {"output_dir": "/o"}


@pytest.mark.anyio
async def test_build_invoke_coerces_non_string_result_to_json(alice):
    async def call_tool(name, args, metadata):
        return {"state": "RUNNING"}

    invoke = build_invoke(call_tool, user=alice, project_paths={})
    out = await invoke("get_hpc_job_status", {})
    assert json.loads(out) == {"state": "RUNNING"}
