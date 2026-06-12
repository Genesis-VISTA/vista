import pytest

from vista_backend.db.schemas import ChatSessionUpdate, ProjectCreate
from vista_backend.services import chat_session as chat_session_service
from vista_backend.services import project as project_service


@pytest.mark.anyio
async def test_get_or_create_chat_session_is_unique_per_user_project(session, alice):
    project = await project_service.create_project(
        session,
        ProjectCreate(name="session-project", description=None, system_prompt=None),
        alice,
    )

    first = await chat_session_service.get_or_create_chat_session(
        session,
        project_id=project.id,
        user_id=alice.id,
    )
    second = await chat_session_service.get_or_create_chat_session(
        session,
        project_id=project.id,
        user_id=alice.id,
    )

    assert first.id == second.id
    assert first.message_history == []
    assert first.messages == []


@pytest.mark.anyio
async def test_update_chat_session_persists_history_and_messages(session, alice):
    project = await project_service.create_project(
        session,
        ProjectCreate(name="persist-project", description=None, system_prompt=None),
        alice,
    )

    updated = await chat_session_service.update_chat_session(
        session,
        project_id=project.id,
        user_id=alice.id,
        updates=ChatSessionUpdate(
            message_history=[
                {
                    "kind": "request",
                    "parts": [{"part_kind": "user-prompt", "content": "hello"}],
                }
            ],
            messages=[
                {
                    "id": "m1",
                    "role": "user",
                    "content": "hello",
                },
                {
                    "id": "m2",
                    "role": "assistant",
                    "content": "hi there",
                    "intermediate": False,
                },
            ],
        ),
    )

    reloaded = await chat_session_service.get_or_create_chat_session(
        session,
        project_id=project.id,
        user_id=alice.id,
    )

    assert updated.id == reloaded.id
    assert reloaded.message_history[0]["kind"] == "request"
    assert reloaded.messages[1]["content"] == "hi there"
