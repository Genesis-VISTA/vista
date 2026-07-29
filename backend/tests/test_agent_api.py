"""
HTTP / SSE contract for `POST /projects/{name}/agent/run` (testing roadmap
Milestone B).

The route is exercised in-process against the real FastAPI app, real auth, and
a real `ProjectAgent` driven by a scripted model — only the MCP boundary and
the agent pool are faked. That locks the streaming event kinds, the authz
answers, and chat-session persistence against regressions.
"""

import json
import uuid

import pytest
from pydantic import TypeAdapter
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart

from harness import (
    agent_under_test,
    api_client,
    call,
    parse_sse,
    say,
    scripted_model,
    seed_project,
    seed_user,
    step_model,
)
from vista_backend.services import chat_session as chat_session_service

pytestmark = [pytest.mark.anyio, pytest.mark.integration]

RUN = "/projects/{name}/agent/run"


def _headers(user) -> dict[str, str]:
    return {"X-Vista-User-Email": user.email}


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


async def test_non_member_cannot_run_the_agent(session):
    alice = await seed_user(session)
    bob = await seed_user(session)
    project = await seed_project(session, alice, name="alices-project")

    with api_client(session) as (client, _):
        response = await client.post(
            RUN.format(name=project.name),
            json={"user_prompt": "hi"},
            headers=_headers(bob),
        )

    assert response.status_code == 403
    assert "do not have access" in response.json()["detail"]


async def test_missing_project_is_404(session):
    alice = await seed_user(session)

    with api_client(session) as (client, _):
        response = await client.post(
            RUN.format(name="no-such-project"),
            json={"user_prompt": "hi"},
            headers=_headers(alice),
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "Project not found"


async def test_unknown_user_is_401(session):
    alice = await seed_user(session)
    project = await seed_project(session, alice)

    with api_client(session) as (client, _):
        response = await client.post(
            RUN.format(name=project.name),
            json={"user_prompt": "hi"},
            headers={"X-Vista-User-Email": "nobody@example.com"},
        )

    assert response.status_code == 401


async def test_admin_may_run_an_agent_they_are_not_a_member_of(session):
    alice = await seed_user(session)
    admin = await seed_user(session, is_admin=True)
    project = await seed_project(session, alice)

    model = step_model([say("hello")])
    with agent_under_test(project, admin, model) as (agent, _):
        with api_client(session, agent=agent) as (client, _pool):
            response = await client.post(
                RUN.format(name=project.name),
                json={"user_prompt": "hi"},
                headers=_headers(admin),
            )

    assert response.status_code == 200


async def test_authz_is_checked_before_the_agent_pool_is_touched(session):
    alice = await seed_user(session)
    bob = await seed_user(session)
    project = await seed_project(session, alice)

    with api_client(session) as (client, pool):
        await client.post(
            RUN.format(name=project.name),
            json={"user_prompt": "hi"},
            headers=_headers(bob),
        )

    assert pool.keys == []


# ---------------------------------------------------------------------------
# Non-streaming turn
# ---------------------------------------------------------------------------


async def test_non_streaming_turn_returns_a_result(session):
    alice = await seed_user(session)
    project = await seed_project(
        session, alice, tools=["*"], knowledge_bases=["msre-reports"]
    )

    model = step_model([call("rag_search", query="FLiBe"), say("FLiBe is a salt.")])
    with agent_under_test(project, alice, model) as (agent, mcp_fake):
        with api_client(session, agent=agent) as (client, _):
            response = await client.post(
                RUN.format(name=project.name),
                json={"user_prompt": "What is FLiBe?"},
                headers=_headers(alice),
            )

    assert response.status_code == 200
    body = response.json()
    assert mcp_fake.names_called() == ["rag_search"]
    assert body["usage"]["tool_calls"] == 1
    assert any("FLiBe is a salt." in json.dumps(m) for m in body["new_messages"])


async def test_agent_pool_key_includes_project_and_user(session):
    alice = await seed_user(session)
    project = await seed_project(session, alice)

    with agent_under_test(project, alice, step_model([say("hi")])) as (agent, _):
        with api_client(session, agent=agent) as (client, pool):
            await client.post(
                RUN.format(name=project.name),
                json={"user_prompt": "hi"},
                headers=_headers(alice),
            )

    assert pool.keys == [(None, project.id, alice.id)]


# ---------------------------------------------------------------------------
# Streaming turn
# ---------------------------------------------------------------------------


async def test_streaming_turn_emits_the_documented_event_kinds(session):
    alice = await seed_user(session)
    project = await seed_project(
        session, alice, tools=["*"], knowledge_bases=["msre-reports"]
    )

    model = step_model([call("rag_search", query="FLiBe"), say("FLiBe is a salt.")])
    with agent_under_test(project, alice, model) as (agent, _):
        with api_client(session, agent=agent) as (client, _pool):
            response = await client.post(
                RUN.format(name=project.name),
                json={"user_prompt": "What is FLiBe?", "stream": True},
                headers=_headers(alice),
            )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    events = parse_sse(response.text)
    kinds = [name for name, _ in events]
    assert "log" in kinds
    assert "function_tool_call" in kinds
    assert "function_tool_result" in kinds
    assert kinds[-1] == "agent_run_result"


async def test_streaming_event_names_match_the_payload_event_kind(session):
    alice = await seed_user(session)
    project = await seed_project(session, alice, tools=["*"])

    with agent_under_test(project, alice, step_model([say("done")])) as (agent, _):
        with api_client(session, agent=agent) as (client, _pool):
            response = await client.post(
                RUN.format(name=project.name),
                json={"user_prompt": "hi", "stream": True},
                headers=_headers(alice),
            )

    for name, data in parse_sse(response.text):
        assert json.loads(data)["event_kind"] == name


async def test_streaming_result_event_carries_the_run_result(session):
    alice = await seed_user(session)
    project = await seed_project(session, alice, tools=["*"])

    with agent_under_test(project, alice, step_model([say("done")])) as (agent, _):
        with api_client(session, agent=agent) as (client, _pool):
            response = await client.post(
                RUN.format(name=project.name),
                json={"user_prompt": "hi", "stream": True},
                headers=_headers(alice),
            )

    name, data = parse_sse(response.text)[-1]
    assert name == "agent_run_result"
    result = json.loads(data)["result"]
    assert result["usage"]["requests"] == 1
    assert result["new_messages"]


# ---------------------------------------------------------------------------
# Chat session history
# ---------------------------------------------------------------------------


_HISTORY = TypeAdapter(list[ModelMessage])


async def _history(session, project, user, chat_session_id) -> list[ModelMessage]:
    return await chat_session_service.get_effective_message_history(
        session,
        project_id=project.id,
        user_id=user.id,
        fallback_history=[],
        chat_session_id=chat_session_id,
    )


def _history_text(history: list[ModelMessage]) -> str:
    return _HISTORY.dump_json(history).decode()


async def _new_chat_session(session, project, user) -> uuid.UUID:
    row = await chat_session_service.create_chat_session(
        session, project_id=project.id, user_id=user.id
    )
    return row.id


async def test_unknown_chat_session_is_404(session):
    alice = await seed_user(session)
    project = await seed_project(session, alice, tools=["*"])

    with api_client(session) as (client, _):
        response = await client.post(
            RUN.format(name=project.name),
            json={"user_prompt": "hi", "chat_session_id": str(uuid.uuid4())},
            headers=_headers(alice),
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "Chat session not found"


async def test_completed_turn_appends_to_the_chat_session(session):
    alice = await seed_user(session)
    project = await seed_project(session, alice, tools=["*"])
    chat_session_id = await _new_chat_session(session, project, alice)

    with agent_under_test(project, alice, step_model([say("first")])) as (agent, _):
        with api_client(session, agent=agent) as (client, _pool):
            response = await client.post(
                RUN.format(name=project.name),
                json={
                    "user_prompt": "first question",
                    "chat_session_id": str(chat_session_id),
                },
                headers=_headers(alice),
            )

    assert response.status_code == 200
    history = await _history(session, project, alice, chat_session_id)
    assert history, "the completed turn did not persist any history"
    blob = _history_text(history)
    assert "first question" in blob
    assert "first" in blob


async def test_second_turn_extends_the_existing_history(session):
    alice = await seed_user(session)
    project = await seed_project(session, alice, tools=["*"])
    chat_session_id = await _new_chat_session(session, project, alice)

    for prompt, answer in (("first question", "first"), ("second question", "second")):
        with agent_under_test(project, alice, step_model([say(answer)])) as (agent, _):
            with api_client(session, agent=agent) as (client, _pool):
                await client.post(
                    RUN.format(name=project.name),
                    json={
                        "user_prompt": prompt,
                        "chat_session_id": str(chat_session_id),
                    },
                    headers=_headers(alice),
                )

    history = await _history(session, project, alice, chat_session_id)
    blob = _history_text(history)
    assert "first question" in blob
    assert "second question" in blob


async def test_streaming_turn_also_appends_history(session):
    alice = await seed_user(session)
    project = await seed_project(session, alice, tools=["*"])
    chat_session_id = await _new_chat_session(session, project, alice)

    with agent_under_test(project, alice, step_model([say("streamed")])) as (agent, _):
        with api_client(session, agent=agent) as (client, _pool):
            await client.post(
                RUN.format(name=project.name),
                json={
                    "user_prompt": "hi",
                    "stream": True,
                    "chat_session_id": str(chat_session_id),
                },
                headers=_headers(alice),
            )

    history = await _history(session, project, alice, chat_session_id)
    assert history


async def test_stateless_turn_does_not_create_a_session(session):
    alice = await seed_user(session)
    project = await seed_project(session, alice, tools=["*"])

    with agent_under_test(project, alice, step_model([say("done")])) as (agent, _):
        with api_client(session, agent=agent) as (client, _pool):
            response = await client.post(
                RUN.format(name=project.name),
                json={"user_prompt": "hi"},
                headers=_headers(alice),
            )

    assert response.status_code == 200
    sessions = await chat_session_service.list_chat_sessions(
        session, project_id=project.id, user_id=alice.id
    )
    assert sessions == []


async def test_message_history_from_the_body_is_used_when_stateless(session):
    """Legacy clients without a session id still get their history replayed."""
    alice = await seed_user(session)
    project = await seed_project(session, alice, tools=["*"])

    with agent_under_test(project, alice, step_model([say("first")])) as (agent, _):
        with api_client(session, agent=agent) as (client, _pool):
            first = await client.post(
                RUN.format(name=project.name),
                json={"user_prompt": "first question"},
                headers=_headers(alice),
            )
    prior = first.json()["new_messages"]

    seen: list[str] = []

    def driver(messages, info):
        seen.append(_HISTORY.dump_json(messages).decode())
        return ModelResponse(parts=[TextPart("second")])

    with agent_under_test(project, alice, scripted_model(driver)) as (agent, _):
        with api_client(session, agent=agent) as (client, _pool):
            second = await client.post(
                RUN.format(name=project.name),
                json={"user_prompt": "second question", "message_history": prior},
                headers=_headers(alice),
            )

    assert second.status_code == 200
    assert "first question" in seen[0], "prior turn was not replayed to the model"
    assert "second question" in seen[0]
    # The response carries only this turn's messages, not the replayed history.
    assert "first question" not in json.dumps(second.json()["new_messages"])
