"""
What the agent is told about its linked folders, and when (openspec change linked-folders,
group 4). Asserted on the instructions the model actually receives.
"""

import json
import uuid
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelResponse, TextPart

from harness import api_client, make_local_settings, make_project, scripted_model
from harness import seed_project
from vista_backend.agents.agents import ProjectAgent
from vista_backend.config import settings
from vista_backend.services import project_agent as project_agent_service

pytestmark = pytest.mark.anyio


class _Link:
    def __init__(self, name: str, host_path: Path):
        self.name, self.host_path = name, str(host_path)


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")


def _recorder(seen: list[str]):
    def model(messages, info):
        seen.append(messages[-1].instructions or "")
        return ModelResponse(parts=[TextPart("ok")])

    return scripted_model(model)


async def _turn(agent: ProjectAgent) -> str:
    seen: list[str] = []
    with agent.agent.override(model=_recorder(seen), toolsets=[]):
        async for _ in agent.run_stream(user_prompt="hi"):
            pass
    assert seen, "the model was never called"
    return seen[-1]


def _folder(root: Path, name: str) -> Path:
    path = root / "Research" / name
    path.mkdir(parents=True)
    return path.resolve()


async def test_the_agent_is_told_its_linked_folders(tmp_path):
    salt, msr = _folder(tmp_path, "salt-data"), _folder(tmp_path, "msr-runs")
    agent = ProjectAgent(
        make_project(),
        make_local_settings(),
        uuid.uuid4(),
        linked_folders=[
            _Link("salt-data", salt),
            _Link("msr-runs", msr),
            _Link("old-runs", tmp_path / "Volumes" / "External" / "old"),
        ],
    )
    said = await _turn(agent)

    assert "## Linked folders" in said
    assert "- /mnt/linked/salt-data (read-only)" in said
    assert "- /mnt/linked/msr-runs (read-only)" in said
    assert "read-only" in said and "/mnt/data/work" in said
    assert "- old-runs (missing)" in said
    assert "/mnt/linked/old-runs" not in said
    for host in (str(salt), str(msr), str(tmp_path)):
        assert host not in said, "a host path reached the agent"


async def test_no_links_no_section():
    agent = ProjectAgent(make_project(), make_local_settings(), uuid.uuid4())
    said = await _turn(agent)
    assert "Linked folders" not in said and "/mnt/linked" not in said


def _volumes(agent: ProjectAgent) -> list:
    [dev] = [
        s
        for s in agent._mcp_servers  # noqa: SLF001
        if "VISTA_DEV_MCP_VOLUMES" in (getattr(s, "env", None) or {})
    ]
    return json.loads(dev.env["VISTA_DEV_MCP_VOLUMES"])


async def test_a_folder_linked_after_a_turn_is_there_on_the_next(
    session, engine, tmp_path, monkeypatch
):
    """
    Through the real pool and `_build_project_agent`: the first turn's agent has no
    links; linking evicts it, and the next message's agent mounts the folder and is told.
    Only the MCP servers' start is skipped.
    """
    project = await seed_project(session, name="salt")
    await session.commit()
    folder = _folder(tmp_path, "salt-data")
    key = (uuid.uuid4(), project.id)

    async def no_mcp(self):
        return self

    async def no_mcp_exit(self, *exc):
        return None

    monkeypatch.setattr(ProjectAgent, "__aenter__", no_mcp)
    monkeypatch.setattr(ProjectAgent, "__aexit__", no_mcp_exit)
    monkeypatch.setattr(project_agent_service, "get_engine", lambda: engine)
    pool = project_agent_service.project_agent_pool
    try:
        async with pool.get(key) as first:
            assert not any("/mnt/linked" in v[1] for v in _volumes(first))
            assert "Linked folders" not in await _turn(first)

        with api_client(session) as (client, _):
            res = await client.post(
                "/projects/salt/linked-folders", json={"path": str(folder)}
            )
            assert res.status_code == 201, res.text
            await session.commit()
        await pool.flush()

        async with pool.get(key) as second:
            assert second is not first
            assert [str(folder), "/mnt/linked/salt-data", "r", False] in _volumes(
                second
            )
            assert "- /mnt/linked/salt-data (read-only)" in await _turn(second)
    finally:
        pool.delete(key)
        await pool.flush()
