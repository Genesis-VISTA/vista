"""
HTTP / SSE contract for `POST /projects/{name}/agent/run` (testing roadmap
Milestone B).

The route is exercised in-process against the real FastAPI app and
a real `ProjectAgent` driven by a scripted model — only the MCP boundary and
the agent pool are faked. That locks the streaming event kinds, the error
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
    step_model,
)
from vista_backend.services import chat_session as chat_session_service

pytestmark = [pytest.mark.anyio, pytest.mark.integration]

RUN = "/projects/{name}/agent/run"


# ---------------------------------------------------------------------------
# Missing project
# ---------------------------------------------------------------------------


async def test_missing_project_is_404(session):
    with api_client(session) as (client, _):
        response = await client.post(
            RUN.format(name="no-such-project"),
            json={"user_prompt": "hi"},
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "Project not found"


async def test_a_missing_project_is_found_before_the_agent_pool_is_touched(session):
    with api_client(session) as (client, pool):
        await client.post(
            RUN.format(name="no-such-project"),
            json={"user_prompt": "hi"},
        )

    assert pool.keys == []


# ---------------------------------------------------------------------------
# Non-streaming turn
# ---------------------------------------------------------------------------


async def test_non_streaming_turn_returns_a_result(session, local):
    project = await seed_project(session, tools=["*"], knowledge_bases=["msre-reports"])

    model = step_model([call("rag_search", query="FLiBe"), say("FLiBe is a salt.")])
    with agent_under_test(project, local, model) as (agent, mcp_fake):
        with api_client(session, agent=agent) as (client, _):
            response = await client.post(
                RUN.format(name=project.name),
                json={"user_prompt": "What is FLiBe?"},
            )

    assert response.status_code == 200
    body = response.json()
    assert mcp_fake.names_called() == ["rag_search"]
    assert body["usage"]["tool_calls"] == 1
    assert any("FLiBe is a salt." in json.dumps(m) for m in body["new_messages"])


async def test_agent_pool_key_includes_project_and_user(session, local):
    project = await seed_project(session)

    with agent_under_test(project, local, step_model([say("hi")])) as (agent, _):
        with api_client(session, agent=agent) as (client, pool):
            await client.post(
                RUN.format(name=project.name),
                json={"user_prompt": "hi"},
            )

    assert pool.keys == [(None, project.id)]


# ---------------------------------------------------------------------------
# Streaming turn
# ---------------------------------------------------------------------------


async def test_streaming_turn_emits_the_documented_event_kinds(session, local):
    project = await seed_project(session, tools=["*"], knowledge_bases=["msre-reports"])

    chat_session_id = await _new_chat_session(session, project)
    model = step_model([call("rag_search", query="FLiBe"), say("FLiBe is a salt.")])
    with agent_under_test(project, local, model) as (agent, _):
        with api_client(session, agent=agent) as (client, _pool):
            response = await client.post(
                RUN.format(name=project.name),
                json={
                    "user_prompt": "What is FLiBe?",
                    "stream": True,
                    "chat_session_id": str(chat_session_id),
                },
            )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    events = parse_sse(response.text)
    kinds = [name for name, _ in events]
    assert "log" in kinds
    assert "function_tool_call" in kinds
    assert "function_tool_result" in kinds
    assert kinds[0] == "run_started"
    assert kinds[-1] == "run_finished"
    assert "agent_run_result" in kinds


async def test_streaming_event_names_match_the_payload_event_kind(session, local):
    project = await seed_project(session, tools=["*"])

    chat_session_id = await _new_chat_session(session, project)

    with agent_under_test(project, local, step_model([say("done")])) as (agent, _):
        with api_client(session, agent=agent) as (client, _pool):
            response = await client.post(
                RUN.format(name=project.name),
                json={
                    "user_prompt": "hi",
                    "stream": True,
                    "chat_session_id": str(chat_session_id),
                },
            )

    for name, data in parse_sse(response.text):
        assert json.loads(data)["event_kind"] == name


async def test_streaming_result_event_carries_the_run_result(session, local):
    project = await seed_project(session, tools=["*"])

    chat_session_id = await _new_chat_session(session, project)

    with agent_under_test(project, local, step_model([say("done")])) as (agent, _):
        with api_client(session, agent=agent) as (client, _pool):
            response = await client.post(
                RUN.format(name=project.name),
                json={
                    "user_prompt": "hi",
                    "stream": True,
                    "chat_session_id": str(chat_session_id),
                },
            )

    name, data = next(e for e in parse_sse(response.text) if e[0] == "agent_run_result")
    result = json.loads(data)["result"]
    assert result["usage"]["requests"] == 1
    assert result["new_messages"]


# ---------------------------------------------------------------------------
# Chat session history
# ---------------------------------------------------------------------------


_HISTORY = TypeAdapter(list[ModelMessage])


async def _history(session, project, chat_session_id) -> list[ModelMessage]:
    return await chat_session_service.get_effective_message_history(
        session,
        project_id=project.id,
        fallback_history=[],
        chat_session_id=chat_session_id,
    )


def _history_text(history: list[ModelMessage]) -> str:
    return _HISTORY.dump_json(history).decode()


async def _new_chat_session(session, project) -> uuid.UUID:
    row = await chat_session_service.create_chat_session(session, project_id=project.id)
    return row.id


async def test_unknown_chat_session_is_404(session):
    project = await seed_project(session, tools=["*"])

    with api_client(session) as (client, _):
        response = await client.post(
            RUN.format(name=project.name),
            json={"user_prompt": "hi", "chat_session_id": str(uuid.uuid4())},
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "Chat session not found"


async def test_a_turn_reads_its_chat_once_for_the_model_and_the_agent(
    session, monkeypatch
):
    """The chat's model and its pooled agent's key come from one lookup."""
    project = await seed_project(session, tools=["*"])
    chat_session_id = await _new_chat_session(session, project)

    lookups: list[uuid.UUID] = []
    real = chat_session_service.get_chat_session_optional

    async def counting(*args, **kwargs):
        lookups.append(kwargs["chat_session_id"])
        return await real(*args, **kwargs)

    class Reached(Exception):
        """Raised where the turn moves on to its history."""

    async def stop(*_args, **_kwargs):
        raise Reached

    monkeypatch.setattr(chat_session_service, "get_chat_session_optional", counting)
    monkeypatch.setattr(chat_session_service, "get_effective_message_history", stop)

    with api_client(session) as (client, _), pytest.raises(Reached):
        await client.post(
            RUN.format(name=project.name),
            json={"user_prompt": "hi", "chat_session_id": str(chat_session_id)},
        )

    assert lookups == [chat_session_id]


async def test_completed_turn_appends_to_the_chat_session(session, local):
    project = await seed_project(session, tools=["*"])
    chat_session_id = await _new_chat_session(session, project)

    with agent_under_test(project, local, step_model([say("first")])) as (agent, _):
        with api_client(session, agent=agent) as (client, _pool):
            response = await client.post(
                RUN.format(name=project.name),
                json={
                    "user_prompt": "first question",
                    "chat_session_id": str(chat_session_id),
                },
            )

    assert response.status_code == 200
    history = await _history(session, project, chat_session_id)
    assert history, "the completed turn did not persist any history"
    blob = _history_text(history)
    assert "first question" in blob
    assert "first" in blob


async def test_second_turn_extends_the_existing_history(session, local):
    project = await seed_project(session, tools=["*"])
    chat_session_id = await _new_chat_session(session, project)

    for prompt, answer in (("first question", "first"), ("second question", "second")):
        with agent_under_test(project, local, step_model([say(answer)])) as (agent, _):
            with api_client(session, agent=agent) as (client, _pool):
                await client.post(
                    RUN.format(name=project.name),
                    json={
                        "user_prompt": prompt,
                        "chat_session_id": str(chat_session_id),
                    },
                )

    history = await _history(session, project, chat_session_id)
    blob = _history_text(history)
    assert "first question" in blob
    assert "second question" in blob


async def test_streaming_turn_also_appends_history(session, local):
    project = await seed_project(session, tools=["*"])
    chat_session_id = await _new_chat_session(session, project)

    with agent_under_test(project, local, step_model([say("streamed")])) as (agent, _):
        with api_client(session, agent=agent) as (client, _pool):
            await client.post(
                RUN.format(name=project.name),
                json={
                    "user_prompt": "hi",
                    "stream": True,
                    "chat_session_id": str(chat_session_id),
                },
            )

    history = await _history(session, project, chat_session_id)
    assert history


async def test_stateless_turn_does_not_create_a_session(session, local):
    project = await seed_project(session, tools=["*"])

    with agent_under_test(project, local, step_model([say("done")])) as (agent, _):
        with api_client(session, agent=agent) as (client, _pool):
            response = await client.post(
                RUN.format(name=project.name),
                json={"user_prompt": "hi"},
            )

    assert response.status_code == 200
    sessions = await chat_session_service.list_chat_sessions(
        session, project_id=project.id
    )
    assert sessions == []


async def test_message_history_from_the_body_is_used_when_stateless(session, local):
    """Legacy clients without a session id still get their history replayed."""
    project = await seed_project(session, tools=["*"])

    with agent_under_test(project, local, step_model([say("first")])) as (agent, _):
        with api_client(session, agent=agent) as (client, _pool):
            first = await client.post(
                RUN.format(name=project.name),
                json={"user_prompt": "first question"},
            )
    prior = first.json()["new_messages"]

    seen: list[str] = []

    def driver(messages, info):
        seen.append(_HISTORY.dump_json(messages).decode())
        return ModelResponse(parts=[TextPart("second")])

    with agent_under_test(project, local, scripted_model(driver)) as (agent, _):
        with api_client(session, agent=agent) as (client, _pool):
            second = await client.post(
                RUN.format(name=project.name),
                json={"user_prompt": "second question", "message_history": prior},
            )

    assert second.status_code == 200
    assert "first question" in seen[0], "prior turn was not replayed to the model"
    assert "second question" in seen[0]
    # The response carries only this turn's messages, not the replayed history.
    assert "first question" not in json.dumps(second.json()["new_messages"])


async def test_streaming_without_a_conversation_is_refused(session):
    project = await seed_project(session, tools=["*"])

    with api_client(session) as (client, _):
        response = await client.post(
            RUN.format(name=project.name),
            json={"user_prompt": "hi", "stream": True},
        )

    assert response.status_code == 400
    assert "chat_session_id" in response.json()["detail"]
