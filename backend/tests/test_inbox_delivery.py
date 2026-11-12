"""
`InboxDelivery` (`services/inbox.py`): items queued while a turn runs reach the
agent at its next step, in arrival order (openspec change background-tasks,
task 2.3).

This pins PydanticAI's `RunContext.enqueue` and its drain, whose queue is marked
internal: an upgrade that changes when the drain runs fails here.
"""

import uuid

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo
from pydantic_ai.toolsets.function import FunctionToolset
from sqlalchemy.exc import OperationalError
from sqlmodel.ext.asyncio.session import AsyncSession

from harness import (
    call,
    make_local_settings,
    make_project,
    scripted_model,
    seed_project,
)
from vista_backend.agents.agents import ProjectAgent, ProjectAgentResultEvent
from vista_backend.services import chat_session as chat_session_service
from vista_backend.services import inbox

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


@pytest.fixture(autouse=True)
def no_listeners(monkeypatch):
    """No registry here: a put must not try to start a turn."""
    monkeypatch.setattr(inbox, "_listeners", [])


@pytest.fixture
async def chat_id(session) -> uuid.UUID:
    project = await seed_project(session)
    row = await chat_session_service.create_chat_session(session, project_id=project.id)
    chat_session_id = row.id
    await session.commit()
    return chat_session_id


def _user_texts(message: ModelMessage) -> list[str]:
    if not isinstance(message, ModelRequest):
        return []
    return [
        p.content
        for p in message.parts
        if isinstance(p, UserPromptPart) and isinstance(p.content, str)
    ]


class Recorder:
    """A model that answers with `steps` and keeps every request it was sent."""

    __name__ = "recorder"  # `FunctionModel` names itself after its function

    def __init__(self, steps: list[ModelResponse]) -> None:
        self.steps = steps
        self.requests: list[list[ModelMessage]] = []

    def __call__(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        self.requests.append(list(messages))
        n = len(self.requests) - 1
        return (
            self.steps[n]
            if n < len(self.steps)
            else ModelResponse(parts=[TextPart("end")])
        )


async def _run(agent: ProjectAgent, take) -> list:
    return [
        e
        async for e in agent.run_stream(
            user_prompt="start", capabilities=[inbox.InboxDelivery(take)]
        )
    ]


def _agent(toolset: FunctionToolset, model) -> tuple[ProjectAgent, object]:
    agent = ProjectAgent(make_project(tools=["*"]), make_local_settings(), uuid.uuid4())
    return agent, agent.agent.override(model=model, toolsets=[toolset])


async def test_items_put_during_a_tool_call_are_in_the_next_request_in_order(
    engine, chat_id
):
    toolset = FunctionToolset()

    @toolset.tool_plain
    async def long_fit() -> str:
        """A long tool call during which a result and then a message arrive."""
        async with AsyncSession(engine) as db:
            await inbox.put(
                db, chat_id, kind="task_event", text="bg-1 exited with status 0"
            )
            await inbox.put(db, chat_id, kind="message", text="also plot it")
        return "fit done"

    model = Recorder([call("long_fit"), ModelResponse(parts=[TextPart("ok")])])

    async def take():
        async with AsyncSession(engine) as db:
            return await inbox.take_queued(db, chat_id)

    agent, override = _agent(toolset, scripted_model(model))
    with override:
        events = await _run(agent, take)

    assert len(model.requests) == 2
    second = model.requests[1]
    # The tool's result, then the items, in arrival order.
    return_at = next(
        i
        for i, m in enumerate(second)
        if isinstance(m, ModelRequest)
        and any(isinstance(p, ToolReturnPart) for p in m.parts)
    )
    delivered = [t for m in second[return_at:] for t in _user_texts(m)]
    framed = f"{inbox.MID_TURN_NOTE}\nalso plot it"
    assert delivered == ["bg-1 exited with status 0", framed], (
        "a message taken mid-turn says the earlier request still stands; a result needs no note"
    )

    result = next(e for e in events if isinstance(e, ProjectAgentResultEvent)).result
    saved = [t for m in result.new_messages for t in _user_texts(m)]
    assert saved == ["start", "bg-1 exited with status 0", framed]
    async with AsyncSession(engine) as db:
        assert await inbox.list_queued(db, chat_id) == []


async def test_an_item_put_during_the_final_answer_gets_another_step(engine, chat_id):
    model = Recorder([])

    def answer(messages, info):
        if not model.requests:
            model.requests.append(list(messages))
            # Arrives while the model writes what would have been its last answer.
            pending.append("bg-2 exited with status 7")
            return ModelResponse(parts=[TextPart("all done")])
        model.requests.append(list(messages))
        return ModelResponse(parts=[TextPart("noted the failure")])

    pending: list[str] = []

    async def take():
        async with AsyncSession(engine) as db:
            for text in pending:
                await inbox.put(db, chat_id, kind="task_event", text=text)
            pending.clear()
            return await inbox.take_queued(db, chat_id)

    agent, override = _agent(FunctionToolset(), scripted_model(answer))
    with override:
        events = await _run(agent, take)

    assert len(model.requests) == 2, "the run continued instead of dropping the item"
    assert _user_texts(model.requests[1][-1]) == ["bg-2 exited with status 7"]
    result = next(e for e in events if isinstance(e, ProjectAgentResultEvent)).result
    assert isinstance(result.new_messages[-1], ModelResponse)


async def test_a_locked_database_delays_delivery_without_failing_the_turn(chat_id):
    async def locked():
        raise OperationalError("UPDATE inbox_item", {}, Exception("database is locked"))

    agent, override = _agent(
        FunctionToolset(),
        scripted_model(Recorder([ModelResponse(parts=[TextPart("ok")])])),
    )
    with override:
        events = await _run(agent, locked)

    assert any(isinstance(e, ProjectAgentResultEvent) for e in events)
