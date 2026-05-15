from typing import AsyncGenerator, AsyncIterator, Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, TypeAdapter
from pydantic_ai import AgentRunResultEvent
from pydantic_ai.messages import AgentStreamEvent, ModelMessage
from pydantic_ai.ui.vercel_ai import VercelAIAdapter
from pydantic_ai.ui.vercel_ai.response_types import BaseChunk, DataChunk
from sqlmodel import select
from sse_starlette.sse import EventSourceResponse
from sse_starlette.event import ServerSentEvent
from ..agents.agents import LogEvent, ProjectAgent, ProjectAgentResult, ProjectAgentResultEvent, McpElicitationEvent
from ..db.db import SessionDep
from ..db.schemas import ProjectPublic, ProjectTable
from ..utils.streams import StreamMerger, StreamClosedError

router = APIRouter()


@router.post("/projects/{project_name}/agent/run/vercel")
async def agent_run_vercel(
    project_name: str, session: SessionDep, request: Request,
):
    """
    Vercel AI SDK v5 / v6 compatible streaming endpoint.

    The request body is the raw Vercel AI SDK `RequestData` (UIMessage list +
    chat metadata) — we let `VercelAIAdapter` parse it; no custom Pydantic model.

    The agent loop itself is driven by `ProjectAgent.run_stream` (shared with the
    SSE endpoint), so all per-tool / per-turn logging lives in one place. Here we
    just translate the resulting event stream into Vercel chunks:

    - `LogEvent`s become `DataChunk(type="data-log", transient=True)` and are
      surfaced in the frontend's AgentLogs panel via `useChat`'s `onData`.
    - Native PydanticAI `AgentStreamEvent` / `AgentRunResultEvent`s are piped
      into `adapter.transform_stream`, which produces the actual Vercel chunks
      (text deltas, tool I/O, finish, etc).
    - Our bundled `ProjectAgentResultEvent` is dropped — `transform_stream`
      already finalized the wire from the native result event.

    MCP elicitation events arrive as `DataChunk(type="data-mcp-form-elicitation")`
    or `DataChunk(type="data-mcp-url-elicitation")` alongside the running stream;
    on the frontend these surface as UIMessage parts that ChatInterface watches to
    open ElicitationModal. The response is POSTed back to /mcp/elicitation.

    See: https://ai-sdk.dev/docs/ai-sdk-ui/streaming-data and
    pydantic_ai.ui.vercel_ai.{VercelAIAdapter, VercelAIEventStream, response_types.DataChunk}.
    """
    project_row = (await session.exec(
        select(ProjectTable).where(ProjectTable.name == project_name)
    )).first()
    if project_row is None:
        raise HTTPException(status_code=404, detail="Project not found")
    project = ProjectPublic.model_validate(project_row)
    project_agent = ProjectAgent(project)

    # Merges the adapter's Vercel chunk stream with injected DataChunks (logs +
    # MCP elicitation events) so they interleave with the running agent stream.
    merger = StreamMerger[BaseChunk]()

    # The adapter is used only for request parsing (`adapter.messages`,
    # `adapter.sanitize_messages`) and stream transformation
    # (`adapter.transform_stream`). The `agent=` field is required by the
    # dataclass but never invoked here — `ProjectAgent.run_stream` drives.
    adapter = await VercelAIAdapter.from_request(
        request, agent=project_agent._build_agent(), sdk_version=5,
    )
    message_history = adapter.sanitize_messages(adapter.messages)

    async def native_stream() -> AsyncIterator[AgentStreamEvent | AgentRunResultEvent]:
        async for event in project_agent.run_stream(
            user_prompt=None,
            message_history=message_history,
            enable_elicitation=True,
        ):
            if isinstance(event, LogEvent):
                try:
                    merger.send(DataChunk(
                        type="data-log",
                        data={"level": event.level, "area": event.area, "message": event.message},
                        transient=True,
                    ))
                except StreamClosedError:
                    pass
            elif isinstance(event, McpElicitationEvent):
                request.app.state.elicitations[event.elicitation_id] = project_agent
                chunk_type = "data-" + event.event_kind.replace("_", "-")
                try:
                    merger.send(DataChunk(
                        type=chunk_type,
                        data=TypeAdapter(Any).dump_python(event),
                        transient=False,
                    ))
                except StreamClosedError:
                    pass
            elif isinstance(event, ProjectAgentResultEvent):
                # The native AgentRunResultEvent was already yielded above,
                # so transform_stream has finalized; the bundled result has no
                # extra info the Vercel client needs.
                pass
            else:
                yield event

    merger.add_stream(adapter.transform_stream(native_stream()))

    event_stream = adapter.build_event_stream()
    return StreamingResponse(
        event_stream.encode_stream(aiter(merger)),
        headers=event_stream.response_headers,
        media_type=event_stream.content_type,
    )


class AgentRunRequest(BaseModel):
    """
    Request body for an agent turn.

    `message_history` and the returned `new_messages` use PydanticAI's
    `ModelMessage` schema -- see https://pydantic.dev/docs/ai/core-concepts/messages/
    for the message/part shape. To continue a conversation, append the previous
    call's `new_messages` to your stored history and send the result here.
    """
    stream: bool = False
    """ If True, stream the response as Server-Sent Events. """
    user_prompt: str
    """ The user prompt to the agent. """
    message_history: list[ModelMessage] = []
    """ Prior `ModelMessage`s from earlier turns (as returned from a previous call). """

@router.post("/projects/{project_name}/agent/run", response_model=ProjectAgentResult)
async def agent_run(
    project_name: str, body: AgentRunRequest, session: SessionDep, request: Request,
) -> ProjectAgentResult | Response:
    """
    Stateless chat completion that runs the full agent loop for one turn.

    Pass the `message_history`. The response contains the messages produced in this agent turn --
    append them to your stored history to continue in the next call.

    Both the streaming and non-streaming payloads are built directly on PydanticAI's data model:

    The `ProjectAgentResult` from non streaming and the final streaming event contains `new_messages`,
    which is a list of `pydantic_ai.messages.ModelMessage`, and `usage` is `pydantic_ai.RunUsage`,
    and `logs` is the list of `LogEvent`s emitted during the run.

    In streaming, each event corresponds to PydanticAI's `pydantic_ai.messages.AgentStreamEvent` see
    https://pydantic.dev/docs/ai/core-concepts/agent/#running-agents
    In addition to Pydantic's event's we also yield LogEvents from the server and mcp servers.
    The final event in streaming is `event: project_agent_run_result` whose `data` is a
    `ProjectAgentResult` -- same as the body of the non-streaming response.

    Supports MCP elicitation in streaming mode. Elicitation requests arrive as an extra SSE event
    interleaved with the agent events:
    - event: mcp_form_elicitation  data: {"elicitation_id": "<id>", "mode": "form", "message": "...", "requested_schema": {...}}
    - event: mcp_url_elicitation   data: {"elicitation_id": "<id>", "mode": "url", "message": "...", "url": "https://..."}
    The client must POST the response to /mcp/elicitation. For URL mode, "accept" means the
    user consented to navigate to the URL; the out-of-band interaction completes separately.
    """
    project = (await session.exec(
        select(ProjectTable).where(ProjectTable.name == project_name)
    )).first()
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    project = ProjectPublic.model_validate(project)
    agent = ProjectAgent(project)

    if body.stream:
        async def agent_events() -> AsyncGenerator[ServerSentEvent, None]:
            async for event in agent.run_stream(
                user_prompt=body.user_prompt,
                message_history=body.message_history,
                enable_elicitation=True,
            ):
                # `run_stream` yields both PydanticAI's native `AgentRunResultEvent` and our
                # ProjectAgentRunResult (for the benefit of the Vercel Adapter)
                # We don't need to send the duplicate info here though.
                if isinstance(event, AgentRunResultEvent):
                    continue
                if isinstance(event, McpElicitationEvent):
                    elicitation_id = event.elicitation_id
                    request.app.state.elicitations[elicitation_id] = agent
                    # calling /mcp/elicitation will resolve the elicitation request
                data = TypeAdapter(Any).dump_json(event).decode()
                yield ServerSentEvent(event=event.event_kind, data=data)

        return EventSourceResponse(agent_events())
    else:
        return await agent.run(
            user_prompt=body.user_prompt,
            message_history=body.message_history,
        )
