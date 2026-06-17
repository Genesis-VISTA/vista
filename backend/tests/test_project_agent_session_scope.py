import pytest

from vista_backend.db.schemas import ChatSessionCreate, ProjectCreate
from vista_backend.services import chat_session as chat_session_service
from vista_backend.services import project as project_service
from vista_backend.services import project_agent as project_agent_service


@pytest.mark.anyio
async def test_get_project_agent_key_is_stateless_without_chat_session_id(session, alice):
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
        ProjectCreate(name="agent-invalidate-project", description=None, system_prompt=None),
        alice,
    )
    key = await project_agent_service.get_project_agent_key(
        session,
        project_id=project.id,
        user_id=alice.id,
    )
    other_key = (key[0], project.id, project.id)

    deleted: list[tuple] = []
    monkeypatch.setattr(project_agent_service.project_agent_pool, "keys", lambda: [key, other_key])
    monkeypatch.setattr(project_agent_service.project_agent_pool, "delete", deleted.append)

    project_agent_service.invalidate_agents(session, project_id=project.id, user_id=alice.id)
    await session.commit()

    assert deleted == [key]


@pytest.mark.anyio
async def test_get_project_agent_key_uses_selected_chat_session_id(session, alice):
    project = await project_service.create_project(
        session,
        ProjectCreate(name="agent-selected-session-project", description=None, system_prompt=None),
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
