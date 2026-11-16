"""
A background command end to end with a real sandbox (openspec change
background-tasks, task 4.5): started in one turn, it starts a second turn when
it exits.

The agent is real and so is its dev MCP server (STDIO, `uv run dev-mcp-server`)
with a real sandbox; the model is scripted and the vista MCP server is left
out. Opt-in: `VISTA_RUN_SANDBOX=1`, with `MSB_HOME` set for microsandbox, or
`VISTA_DEV_MCP_SANDBOX_MODE=container` (docker or podman) without it.
"""

import asyncio
import json
import os
import shutil
from contextlib import asynccontextmanager

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from harness import (
    call,
    make_local_settings,
    make_project,
    say,
    seed_project,
    step_model,
)
from vista_backend.agents.agents import ProjectAgent, _hide_backend_args
from vista_backend.config import settings
from vista_backend.db.schemas import BackgroundTaskTable, ChatSessionTable
from vista_backend.services import background_tasks, chat_run
from vista_backend.services import chat_session as chat_session_service

pytestmark = [
    pytest.mark.sandbox,
    pytest.mark.anyio,
    pytest.mark.skipif(
        os.environ.get("VISTA_RUN_SANDBOX") != "1",
        reason="set VISTA_RUN_SANDBOX=1 to run against a real sandbox",
    ),
]


@pytest.fixture
def sandbox_mode(monkeypatch):
    if "MSB_HOME" in os.environ:
        monkeypatch.setenv("VISTA_DEV_MCP_SANDBOX_MODE", "microsandbox")
    elif shutil.which("docker") or shutil.which("podman"):
        monkeypatch.setenv("VISTA_DEV_MCP_SANDBOX_MODE", "container")
    else:
        pytest.skip("needs MSB_HOME for microsandbox, or docker or podman")


async def test_a_background_command_starts_a_turn_when_it_exits(
    engine, session, monkeypatch, tmp_path, sandbox_mode
):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(chat_run, "session_factory", lambda: AsyncSession(engine))
    monkeypatch.setattr(
        background_tasks, "session_factory", lambda: AsyncSession(engine)
    )
    registry = chat_run.ChatRunRegistry()
    monkeypatch.setattr(chat_run, "chat_run_registry", registry)
    tasks = background_tasks.BackgroundTaskRegistry()
    monkeypatch.setattr(background_tasks, "background_task_registry", tasks)

    project = await seed_project(session)
    row = await chat_session_service.create_chat_session(session, project_id=project.id)
    chat_id = row.id
    await session.commit()

    agent = ProjectAgent(make_project(tools=["*"]), make_local_settings(), chat_id)
    agent.project = agent.project.model_copy(update={"id": project.id})
    agent.volume_root.mkdir(parents=True, exist_ok=True)
    dev = agent._dev_mcp_server
    model = step_model(
        [
            call(
                "run_bash", command="sleep 3; echo done", background=True, timeout=120
            ),
            say("Started it; I will look when it ends."),
            say("It finished and printed done."),
        ]
    )
    toolset = dev.filtered(lambda ctx, tool: agent._tool_allowed(tool.name)).prepared(
        _hide_backend_args
    )

    @asynccontextmanager
    async def get(key):
        yield agent

    class Pool:
        pass

    Pool.get = staticmethod(get)  # type: ignore[attr-defined]
    monkeypatch.setattr(chat_run, "project_agent_pool", Pool)

    # Entered for the whole test, as the pool keeps a conversation's agent: the
    # sandbox must outlive the turn that started the command.
    async with dev:
        with agent.agent.override(model=model, toolsets=[toolset]):
            first = await registry.start(
                chat_session_id=chat_id,
                project_id=project.id,
                agent_key=(chat_id, project.id),
                user_prompt="run it in the background",
                prior_history=[],
            )
            async with asyncio.timeout(120):
                await first.wait()
            assert first.state == "done"

            async with AsyncSession(engine) as db:
                [record] = await background_tasks.list_for_chat(db, chat_id)
            assert record.state == "running", "the turn ended; the command did not"
            # The second turn is scripted and over in moments: wait for what it saves.
            async with asyncio.timeout(60):
                while True:
                    async with AsyncSession(engine) as db:
                        saved_chat = await db.get(ChatSessionTable, chat_id)
                    if (
                        saved_chat
                        and saved_chat.auto_turns == 1
                        and saved_chat.run_state == "done"
                    ):
                        break
                    await asyncio.sleep(0.2)
            await tasks.shutdown()

    events = saved_chat.run_events or []
    started = next(e["data"] for e in events if e["event"] == "run_started")
    assert started["origin"] == "task"
    taken = [e["data"] for e in events if e["event"] == "inbox_taken"]
    assert taken[0]["kind"] == "task_event"
    assert "exited with status 0" in taken[0]["text"]
    assert taken[0]["text"].endswith("done")
    assert "It finished and printed done." in json.dumps(saved_chat.message_history)
    async with AsyncSession(engine) as db:
        saved = await db.get(BackgroundTaskTable, record.id)
        assert saved is not None and (saved.state, saved.exit_status) == ("exited", 0)
    log = background_tasks.host_log_path(agent.volume_root, record)
    assert log.read_text(encoding="utf-8") == "done\n"
