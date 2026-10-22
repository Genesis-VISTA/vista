import asyncio
import uuid

import pytest

from vista_backend.db.schemas import (
    ChatSessionCreate,
    ChatSessionUpdate,
    ProjectCreate,
)
from vista_backend.services import chat_session as chat_session_service
from vista_backend.services import project as project_service
from vista_backend.services import project_agent as project_agent_service


@pytest.mark.anyio
async def test_get_project_agent_key_is_stateless_without_chat_session_id(session):
    project = await project_service.create_project(
        session,
        ProjectCreate(name="agent-scope-project", description=None, system_prompt=None),
    )

    key = await project_agent_service.get_project_agent_key(
        session,
        project_id=project.id,
    )

    assert key == (None, project.id)
    listed = await chat_session_service.list_chat_sessions(
        session,
        project_id=project.id,
    )
    assert listed == []


@pytest.mark.anyio
async def test_invalidate_agents_matches_project_inside_session_scoped_keys(
    session, monkeypatch
):
    project = await project_service.create_project(
        session,
        ProjectCreate(
            name="agent-invalidate-project", description=None, system_prompt=None
        ),
    )
    key = await project_agent_service.get_project_agent_key(
        session,
        project_id=project.id,
    )
    other_key = (key[0], uuid.uuid4())

    deleted: list[tuple] = []
    monkeypatch.setattr(
        project_agent_service.project_agent_pool, "keys", lambda: [key, other_key]
    )
    monkeypatch.setattr(
        project_agent_service.project_agent_pool, "delete", deleted.append
    )

    project_agent_service.invalidate_agents(session, project_id=project.id)
    await session.commit()

    assert deleted == [key]


@pytest.mark.anyio
async def test_invalidate_agents_without_a_project_evicts_every_agent(
    session, monkeypatch
):
    """A settings change: every agent was built from the settings."""
    keys = [(None, uuid.uuid4()), (uuid.uuid4(), uuid.uuid4())]
    deleted: list[tuple] = []
    monkeypatch.setattr(project_agent_service.project_agent_pool, "keys", lambda: keys)
    monkeypatch.setattr(
        project_agent_service.project_agent_pool, "delete", deleted.append
    )

    project_agent_service.invalidate_agents(session)
    await session.commit()

    assert deleted == keys


@pytest.mark.anyio
async def test_get_project_agent_key_uses_selected_chat_session_id(session):
    project = await project_service.create_project(
        session,
        ProjectCreate(
            name="agent-selected-session-project", description=None, system_prompt=None
        ),
    )
    selected = await chat_session_service.create_chat_session(
        session,
        project_id=project.id,
        payload=ChatSessionCreate(title="Selected"),
    )

    key = await project_agent_service.get_project_agent_key(
        session,
        project_id=project.id,
        chat_session_id=selected.id,
    )

    assert key == (selected.id, project.id)


async def _two_chats(session, name):
    project = await project_service.create_project(
        session,
        ProjectCreate(name=name, description=None, system_prompt=None),
    )
    chats = [
        await chat_session_service.create_chat_session(session, project_id=project.id)
        for _ in range(2)
    ]
    return project, chats


async def _set_chat_model(session, project, chat, model):
    await chat_session_service.update_chat_session(
        session,
        project_id=project.id,
        chat_session_id=chat.id,
        updates=ChatSessionUpdate(inference_model=model),
    )
    await session.commit()


@pytest.mark.anyio
async def test_changing_a_chats_model_evicts_only_that_chats_agent(
    session, monkeypatch
):
    project, (chat, other) = await _two_chats(session, "chat-model-evict")
    keys = [(c.id, project.id) for c in (chat, other)]
    keys.append((None, project.id))

    deleted: list[tuple] = []
    pool = project_agent_service.project_agent_pool
    monkeypatch.setattr(pool, "keys", lambda: keys)
    monkeypatch.setattr(pool, "delete", deleted.append)

    await _set_chat_model(session, project, chat, "openai:b")

    assert deleted == [keys[0]]


@pytest.mark.anyio
async def test_a_turn_in_flight_finishes_on_its_old_agent(session, monkeypatch):
    """
    Eviction waits for holders (`TTLPool`), so the turn that is running when
    its chat's model changes keeps the agent it started with; the next turn
    gets a new one.
    """
    project, (chat, _) = await _two_chats(session, "chat-model-in-flight")
    key = (chat.id, project.id)

    built: list[object] = []
    cleaned: list[object] = []

    async def factory(_key):
        built.append(object())
        return built[-1]

    async def cleanup(agent):
        cleaned.append(agent)

    pool = project_agent_service.project_agent_pool
    monkeypatch.setattr(pool, "_factory", factory)
    monkeypatch.setattr(pool, "_cleanup", cleanup)
    try:
        async with pool.get(key) as running:
            await _set_chat_model(session, project, chat, "openai:b")
            await asyncio.sleep(0)
            assert key not in pool.keys()
            assert cleaned == [], "the running turn's agent was torn down under it"

            async with pool.get(key) as next_turn:
                assert next_turn is not running
        await pool.flush()
        assert running in cleaned
    finally:
        pool.delete(key)
        await pool.flush()
