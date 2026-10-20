import asyncio

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
async def test_get_project_agent_key_is_stateless_without_chat_session_id(
    session, alice
):
    project = await project_service.create_project(
        session,
        ProjectCreate(name="agent-scope-project", description=None, system_prompt=None),
        alice,
    )

    key = await project_agent_service.get_project_agent_key(
        session,
        project_id=project.id,
        user_id=alice.id,
    )

    assert key[0] is None
    assert key[1] == project.id
    assert key[2] == alice.id
    listed = await chat_session_service.list_chat_sessions(
        session,
        project_id=project.id,
        user_id=alice.id,
    )
    assert listed == []


@pytest.mark.anyio
async def test_invalidate_agents_matches_project_and_user_inside_session_scoped_keys(
    session, alice, monkeypatch
):
    project = await project_service.create_project(
        session,
        ProjectCreate(
            name="agent-invalidate-project", description=None, system_prompt=None
        ),
        alice,
    )
    key = await project_agent_service.get_project_agent_key(
        session,
        project_id=project.id,
        user_id=alice.id,
    )
    other_key = (key[0], project.id, project.id)

    deleted: list[tuple] = []
    monkeypatch.setattr(
        project_agent_service.project_agent_pool, "keys", lambda: [key, other_key]
    )
    monkeypatch.setattr(
        project_agent_service.project_agent_pool, "delete", deleted.append
    )

    project_agent_service.invalidate_agents(
        session, project_id=project.id, user_id=alice.id
    )
    await session.commit()

    assert deleted == [key]


@pytest.mark.anyio
async def test_get_project_agent_key_uses_selected_chat_session_id(session, alice):
    project = await project_service.create_project(
        session,
        ProjectCreate(
            name="agent-selected-session-project", description=None, system_prompt=None
        ),
        alice,
    )
    selected = await chat_session_service.create_chat_session(
        session,
        project_id=project.id,
        user_id=alice.id,
        payload=ChatSessionCreate(title="Selected"),
    )

    key = await project_agent_service.get_project_agent_key(
        session,
        project_id=project.id,
        user_id=alice.id,
        chat_session_id=selected.id,
    )

    assert key == (selected.id, project.id, alice.id)


async def _two_chats(session, alice, name):
    project = await project_service.create_project(
        session,
        ProjectCreate(name=name, description=None, system_prompt=None),
        alice,
    )
    chats = [
        await chat_session_service.create_chat_session(
            session, project_id=project.id, user_id=alice.id
        )
        for _ in range(2)
    ]
    return project, chats


async def _set_chat_model(session, alice, project, chat, model):
    await chat_session_service.update_chat_session(
        session,
        project_id=project.id,
        user_id=alice.id,
        chat_session_id=chat.id,
        updates=ChatSessionUpdate(inference_model=model),
    )
    await session.commit()


@pytest.mark.anyio
async def test_changing_a_chats_model_evicts_only_that_chats_agent(
    session, alice, monkeypatch
):
    project, (chat, other) = await _two_chats(session, alice, "chat-model-evict")
    keys = [(c.id, project.id, alice.id) for c in (chat, other)]
    keys.append((None, project.id, alice.id))

    deleted: list[tuple] = []
    pool = project_agent_service.project_agent_pool
    monkeypatch.setattr(pool, "keys", lambda: keys)
    monkeypatch.setattr(pool, "delete", deleted.append)

    await _set_chat_model(session, alice, project, chat, "openai:b")

    assert deleted == [keys[0]]


@pytest.mark.anyio
async def test_a_turn_in_flight_finishes_on_its_old_agent(session, alice, monkeypatch):
    """
    Eviction waits for holders (`TTLPool`), so the turn that is running when
    its chat's model changes keeps the agent it started with; the next turn
    gets a new one.
    """
    project, (chat, _) = await _two_chats(session, alice, "chat-model-in-flight")
    key = (chat.id, project.id, alice.id)

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
            await _set_chat_model(session, alice, project, chat, "openai:b")
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
