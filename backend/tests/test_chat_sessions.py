import pytest

from vista_backend.db.schemas import ChatSessionCreate, ChatSessionUpdate, ProjectCreate
from vista_backend.services import chat_session as chat_session_service
from vista_backend.services import project as project_service


def _kind(message):
    if isinstance(message, dict):
        return message["kind"]
    return message.kind


@pytest.mark.anyio
async def test_create_and_list_chat_sessions_support_multiple_per_project(
    session, alice
):
    project = await project_service.create_project(
        session,
        ProjectCreate(name="session-project", description=None, system_prompt=None),
        alice,
    )

    first = await chat_session_service.create_chat_session(
        session,
        project_id=project.id,
        user_id=alice.id,
        payload=ChatSessionCreate(title="First"),
    )
    second = await chat_session_service.create_chat_session(
        session,
        project_id=project.id,
        user_id=alice.id,
        payload=ChatSessionCreate(title="Second"),
    )

    listed = await chat_session_service.list_chat_sessions(
        session,
        project_id=project.id,
        user_id=alice.id,
    )

    assert first.id != second.id
    assert first.message_history == []
    assert first.messages == []
    assert [row.title for row in listed] == ["Second", "First"]


@pytest.mark.anyio
async def test_update_chat_session_persists_history_and_messages(session, alice):
    project = await project_service.create_project(
        session,
        ProjectCreate(name="persist-project", description=None, system_prompt=None),
        alice,
    )

    created = await chat_session_service.create_chat_session(
        session,
        project_id=project.id,
        user_id=alice.id,
    )

    updated = await chat_session_service.update_chat_session(
        session,
        project_id=project.id,
        user_id=alice.id,
        chat_session_id=created.id,
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
            latest_result={
                "ok": True,
                "stdout": "",
                "stderr": "",
                "artifacts": [],
                "meta": {"tool": "display_file"},
                "ui": {"kind": "html", "html": "<div>plot</div>"},
            },
        ),
    )

    reloaded = await chat_session_service.get_or_create_chat_session(
        session,
        project_id=project.id,
        user_id=alice.id,
        chat_session_id=updated.id,
    )

    assert updated.id == reloaded.id
    assert reloaded.message_history[0]["kind"] == "request"
    assert reloaded.messages[1]["content"] == "hi there"
    assert reloaded.latest_result["ui"]["html"] == "<div>plot</div>"


@pytest.mark.anyio
async def test_update_chat_session_title_only_preserves_existing_history(
    session, alice
):
    project = await project_service.create_project(
        session,
        ProjectCreate(name="rename-project", description=None, system_prompt=None),
        alice,
    )

    created = await chat_session_service.create_chat_session(
        session,
        project_id=project.id,
        user_id=alice.id,
    )

    created = await chat_session_service.update_chat_session(
        session,
        project_id=project.id,
        user_id=alice.id,
        chat_session_id=created.id,
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
                }
            ],
        ),
    )

    renamed = await chat_session_service.update_chat_session(
        session,
        project_id=project.id,
        user_id=alice.id,
        chat_session_id=created.id,
        updates=ChatSessionUpdate(title="Renamed conversation"),
    )

    assert renamed.title == "Renamed conversation"
    assert renamed.message_history[0]["kind"] == "request"
    assert renamed.messages[0]["content"] == "hello"


@pytest.mark.anyio
async def test_effective_message_history_is_stateless_without_chat_session_id(
    session, alice
):
    project = await project_service.create_project(
        session,
        ProjectCreate(name="history-project", description=None, system_prompt=None),
        alice,
    )

    fallback = [
        {
            "kind": "request",
            "parts": [{"part_kind": "user-prompt", "content": "fallback"}],
        }
    ]
    effective = await chat_session_service.get_effective_message_history(
        session,
        project_id=project.id,
        user_id=alice.id,
        fallback_history=fallback,
    )
    assert len(effective) == 1
    assert _kind(effective[0]) == "request"

    listed = await chat_session_service.list_chat_sessions(
        session,
        project_id=project.id,
        user_id=alice.id,
    )
    assert listed == []

    persisted_session = await chat_session_service.create_chat_session(
        session,
        project_id=project.id,
        user_id=alice.id,
    )
    await chat_session_service.save_message_history(
        session,
        project_id=project.id,
        user_id=alice.id,
        chat_session_id=persisted_session.id,
        message_history=[
            {
                "kind": "response",
                "parts": [{"part_kind": "text", "content": "persisted"}],
            }
        ],
    )
    persisted = await chat_session_service.get_effective_message_history(
        session,
        project_id=project.id,
        user_id=alice.id,
        chat_session_id=persisted_session.id,
        fallback_history=fallback,
    )
    assert len(persisted) == 1
    assert _kind(persisted[0]) == "response"


@pytest.mark.anyio
async def test_append_message_history_extends_existing_session_history(session, alice):
    project = await project_service.create_project(
        session,
        ProjectCreate(name="append-project", description=None, system_prompt=None),
        alice,
    )

    prior_history = [
        {
            "kind": "request",
            "parts": [{"part_kind": "user-prompt", "content": "first"}],
        }
    ]
    new_messages = [
        {
            "kind": "response",
            "parts": [{"part_kind": "text", "content": "second"}],
        }
    ]

    created = await chat_session_service.create_chat_session(
        session,
        project_id=project.id,
        user_id=alice.id,
    )

    await chat_session_service.append_message_history(
        session,
        project_id=project.id,
        user_id=alice.id,
        prior_history=prior_history,
        new_messages=new_messages,
        chat_session_id=created.id,
    )

    reloaded = await chat_session_service.get_or_create_chat_session(
        session,
        project_id=project.id,
        user_id=alice.id,
        chat_session_id=created.id,
    )
    assert [message["kind"] for message in reloaded.message_history] == [
        "request",
        "response",
    ]


@pytest.mark.anyio
async def test_delete_chat_session_removes_only_target_session(session, alice):
    project = await project_service.create_project(
        session,
        ProjectCreate(name="delete-project", description=None, system_prompt=None),
        alice,
    )

    first = await chat_session_service.create_chat_session(
        session,
        project_id=project.id,
        user_id=alice.id,
        payload=ChatSessionCreate(title="First"),
    )
    second = await chat_session_service.create_chat_session(
        session,
        project_id=project.id,
        user_id=alice.id,
        payload=ChatSessionCreate(title="Second"),
    )

    await chat_session_service.delete_chat_session(
        session,
        project_id=project.id,
        user_id=alice.id,
        chat_session_id=first.id,
    )

    listed = await chat_session_service.list_chat_sessions(
        session,
        project_id=project.id,
        user_id=alice.id,
    )

    assert [row.id for row in listed] == [second.id]
