"""
Logic to build the actual PydanticAI Agent
"""
import fnmatch, json, uuid, asyncio
from typing import AsyncIterator, Literal, Annotated as A, Any
from pathlib import Path

from pydantic import BaseModel, Field, Discriminator
from pydantic_ai import Agent, RunContext, UsageLimits, RunUsage, AgentRunResultEvent
from pydantic_ai.mcp import MCPServerStreamableHTTP, ProcessToolCallback, CallToolFunc
from pydantic_ai.messages import (
    AgentStreamEvent,
    ModelMessage,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    RetryPromptPart,
)
from pydantic_ai.models import infer_model
import mcp.client.session
import mcp.shared.context
import mcp.types

from ..config import settings
from ..db.schemas import ProjectPublic, UserPublicWithConfig
from ..utils.streams import StreamMerger, StreamClosedError
from ..utils.misc import json_dump_if
from ..vistaguard import VistaGuardSidecar
from .skills import to_prompt


BASE_SYSTEM_PROMPT = (Path(__file__).parent / "base_system_prompt.md").read_text()


class LogEntry(BaseModel):
    """
    A log line emitted during an agent run.

    Covers both the backend agent's own logs and logs forwarded from the
    VISTA MCP Server.
    """

    level: str
    """ Log level, e.g. "INFO", "WARNING", "ERROR". """
    area: str
    """ Where the log came from, e.g. "Agent", "Tool:run_bash", "MCP Server". """
    message: str

class LogEvent(LogEntry):
    event_kind: Literal["log"] = "log"

class McpFormElicitationEvent(BaseModel):
    event_kind: Literal["mcp_form_elicitation"] = "mcp_form_elicitation"
    mode: Literal["form"] = "form"
    elicitation_id: str
    message: str
    requested_schema: mcp.types.ElicitRequestedSchema

class McpUrlElicitationEvent(BaseModel):
    event_kind: Literal["mcp_url_elicitation"] = "mcp_url_elicitation"
    mode: Literal["url"] = "url"
    elicitation_id: str
    message: str
    url: str

McpElicitationEvent = McpFormElicitationEvent | McpUrlElicitationEvent


class ProjectAgentResult(BaseModel):
    """
    Result of a single agent turn (`ProjectAgent.run`, or the terminal event
    of `ProjectAgent.run_stream`).

    `new_messages` is a list of PydanticAI `ModelMessage` objects produced during this
    run -- model requests, tool calls, tool returns, and the final text response. Append
    them to your stored `message_history` to continue the conversation on the next call.

    See https://pydantic.dev/docs/ai/core-concepts/agent/#running-agents for the agent
    run model and https://pydantic.dev/docs/ai/core-concepts/messages/ for the message
    schema.
    """
    new_messages: list[ModelMessage]
    """ Messages produced during this run; append to `message_history` for the next call. """
    usage: RunUsage
    """ Token / request usage for this run -- see `pydantic_ai.RunUsage`. """
    logs: list[LogEntry] = Field(default_factory=list)
    """ All log lines emitted during this run (also streamed live as `LogEvent`s). """

class ProjectAgentResultEvent(BaseModel):
    """ Terminal event of `ProjectAgent.run_stream`, carrying the `ProjectAgentResult`. """
    event_kind: Literal["agent_run_result"] = "agent_run_result"
    result: ProjectAgentResult

ProjectAgentStreamEvent = A[AgentStreamEvent | LogEvent | McpElicitationEvent | ProjectAgentResultEvent, Discriminator("event_kind")]


def _tool_allowed(name: str, patterns: list[str]) -> bool:
    """
    Match `name` against a list of fnmatch-style patterns.

    Entries beginning with `!` are deny patterns; everything else is an
    allow pattern. A tool is allowed iff at least one allow pattern matches
    and no deny pattern matches. If there are no allow_patterns, assume allow "*".
    """
    allow_patterns = [p for p in patterns if not p.startswith("!")]
    if not allow_patterns:
        allow_patterns = ['*']
    deny_patterns = [p[1:] for p in patterns if p.startswith("!")]
    if not any(fnmatch.fnmatchcase(name, p) for p in allow_patterns):
        return False
    if any(fnmatch.fnmatchcase(name, p) for p in deny_patterns):
        return False
    return True


# TODO: Cache MCP connection?
def get_mcp_server(
    elicitation_callback: mcp.client.session.ElicitationFnT | None = None,
    process_tool_call: ProcessToolCallback | None = None,
    log_handler: mcp.client.session.LoggingFnT | None = None,
) -> MCPServerStreamableHTTP:
    """
    Connect to the VISTA MCP Server
    """
    return MCPServerStreamableHTTP(
        url=settings.mcp_url,
        elicitation_callback=elicitation_callback,
        process_tool_call=process_tool_call,
        log_handler=log_handler,
        log_level="info" if log_handler else None,
        timeout=10,
        read_timeout=1800 + 60,
    )


class ProjectAgent:
    def __init__(self, project: ProjectPublic, user: UserPublicWithConfig):
        self.project = project
        self.user = user
        self._elicitations: dict[str, asyncio.Future] = {}
        # VISTAGuard sidecar: per-`ProjectAgent` composite that owns
        # the security gates, capability registry, trust scorer,
        # incident manager, and provenance emitter. The sidecar's
        # `process_tool_call` is composed into the MCP hook chain in
        # `_build_agent`. In Phase 0 the sidecar is inert (no gates built);
        # enabling individual `VISTA_BACKEND_VISTAGUARD__G{N}_ENABLED` flags
        # in later phases flips `self._sidecar.is_active()` to True
        # and activates the composition automatically.
        self._sidecar = VistaGuardSidecar(settings.vistaguard, project)

    def _build_agent(self,
        elicitation_callback: mcp.client.session.ElicitationFnT | None = None,
        log_handler: mcp.client.session.LoggingFnT | None = None,
    ) -> Agent:
        """
        Construct a PydanticAI Agent for a project.
        """
        async def process_tool_call(ctx: RunContext[Any], call_tool: CallToolFunc, name: str, tool_args: dict[str, Any]):
            # TODO Temporary scaffolding for getting per-user HPC credentials to the MCP server, we set up the s3m
            # creds via MCP metadata. Later we'll set up more generic MCP server configuration that supports 3rd party
            # MCP servers. It will launch isolated MCP server instances per project, and the user can configure any
            # environment vars/headers necessary.
            metadata = {}
            HPC_TOOLS = {
                "submit_hpc_job", "get_hpc_job_status",
                "get_hpc_job_outputs", "list_hpc_jobs", "cancel_hpc_job",
            }
            if name in HPC_TOOLS:
                metadata["vista_user_config"] = self.user.model_dump(mode='json')

            if self._sidecar.is_active():
                async def call_tool_wrapper(inner_name, inner_args, inner_metadata = None):
                    inner_metadata = {**(inner_metadata or {}), **metadata} # Merge in metadatas
                    return await call_tool(inner_name, inner_args, inner_metadata)
                return await self._sidecar.process_tool_call(ctx, call_tool_wrapper, name, tool_args)
            else:
                return await call_tool(name, tool_args, metadata)

        mcp_server = get_mcp_server(
            elicitation_callback=elicitation_callback,
            process_tool_call=process_tool_call,
            log_handler=log_handler,
        )
        tool_patterns = list(self.project.tools or [])
        if not self.project.knowledge_bases and "!rag_search" not in tool_patterns:
            tool_patterns.append("!rag_search")
        toolset = mcp_server.filtered(lambda ctx, tool: _tool_allowed(tool.name, self.project.tools))

        agent = Agent(
            model=infer_model(settings.model),
            toolsets=[toolset],
        )

        @agent.system_prompt
        def system_prompt(ctx: RunContext[str]) -> str:
            parts = [BASE_SYSTEM_PROMPT]
            if self.project.system_prompt:
                parts.append("## Project Information")
                parts.append(self.project.system_prompt)

            # TODO Should cache these, but do need them to update when the project is edited
            if self.project.knowledge_bases:
                kb_lines = "\n".join(f"  - {slug}" for slug in self.project.knowledge_bases)
                kb_block = (
                    "Knowledge Bases available to this project (pass one of these "
                    "slugs as the `kb_slug` argument to `rag_search`):\n" + kb_lines
                )
            else:
                kb_block = (
                    "No Knowledge Bases are configured for this project; the "
                    "`rag_search` tool is not available."
                )
            parts.append(kb_block)

            skills_block = to_prompt([settings.skills_dir / skill for skill in self.project.skills], {
                settings.skills_dir: "/mnt/skills",
            })
            parts.append(skills_block)

            return "\n\n".join([p for p in parts if p])

        return agent

    async def run(self,
        user_prompt: str,
        message_history: list[ModelMessage]|None = None,
    ) -> ProjectAgentResult:
        """
        Run the agent for a single agent "turn".

        See run_stream for more info.
        """

        async for event in self.run_stream(
            user_prompt = user_prompt,
            message_history = message_history,
            enable_elicitation=False,
        ):
            if isinstance(event, ProjectAgentResultEvent):
                return event.result
        raise RuntimeError("Agent didn't emit a result") # Should be unreachable

    def run_stream(self,
        user_prompt: str,
        message_history: list[ModelMessage]|None = None,
        enable_elicitation: bool = False,
    ) -> AsyncIterator[ProjectAgentStreamEvent]:
        """
        Run the agent and return a stream of events.

        Yields all events from Pydantic, see `pydantic_ai.messages.AgentStreamEvent` and
        https://pydantic.dev/docs/ai/core-concepts/agent/#running-agents for more info.

        Also adds LogEvents of our own, that contains log lines from the agent and MCP server.

        Pass enable_elicitation to support MCP elicitation. When enabled, it will yield an McpElicitation
        event when elicitation is requested. You should call agent.resolve_elicitation with the result.
        """
        usage_limits = UsageLimits(**(self.project.usage_limits or {}))

        # Merge the agent's own event stream with our own MCP Server log notifications
        merger = StreamMerger[ProjectAgentStreamEvent]()
        logs: list[LogEntry] = []

        def log(level: str, area: str, message: str):
            logs.append(LogEntry(level = level, area = area, message = message))
            return LogEvent(level = level, area = area, message = message)

        async def log_handler(params: mcp.types.LoggingMessageNotificationParams):
            try:
                merger.send(log(str(params.level).upper(), "MCP Server", json_dump_if(params.data)))
            except StreamClosedError:
                pass # Merger already closed, the run finished before this notification.

        if enable_elicitation:
            async def elicitation_callback(
                context: mcp.shared.context.RequestContext[mcp.client.session.ClientSession, Any, Any],
                params: mcp.types.ElicitRequestParams,
            ) -> mcp.types.ElicitResult:
                if isinstance(params, mcp.types.ElicitRequestFormParams):
                    event = McpFormElicitationEvent(
                        elicitation_id=str(uuid.uuid4()),
                        message=params.message,
                        requested_schema=params.requestedSchema,
                    )
                elif isinstance(params, mcp.types.ElicitRequestURLParams):
                    event = McpUrlElicitationEvent(
                        # TODO probably shouldn't assume the sent elicitationId is globally unique
                        elicitation_id=params.elicitationId,
                        url=params.url,
                        message=params.message,
                    )
                else:
                    return mcp.types.ElicitResult(action="cancel")

                # URL-mode ids come from the upstream server; refuse a duplicate sent by the MCP server
                if event.elicitation_id in self._elicitations:
                    return mcp.types.ElicitResult(action="cancel")

                future: asyncio.Future[mcp.types.ElicitResult] = asyncio.get_running_loop().create_future()
                self._elicitations[event.elicitation_id] = future
                try:
                    merger.send(event)
                except StreamClosedError:
                    # Merger already closed, the run finished before this notification.
                    return mcp.types.ElicitResult(action="cancel")

                try:
                    return await asyncio.wait_for(future, timeout=5 * 60)
                except asyncio.TimeoutError:
                    return mcp.types.ElicitResult(action="cancel")
                finally:
                    self._elicitations.pop(event.elicitation_id, None)
        else:
            elicitation_callback = None

        agent = self._build_agent(
            elicitation_callback=elicitation_callback,
            log_handler=log_handler,
        )

        async def agent_stream() -> AsyncIterator[ProjectAgentStreamEvent]:
            yield log("INFO", "Agent", "\n".join([
                f"New request:",
                f"    project: {self.project.name}",
                f"    userMessage: {json.dumps(user_prompt[:200])}",
                f"    historyTurns: {len(message_history or [])}",
            ]))

            async for event in agent.run_stream_events(
                user_prompt,
                message_history=message_history,
                usage_limits=usage_limits,
            ):
                if isinstance(event, AgentRunResultEvent):
                    yield log("INFO", "Agent", f"Turn completed")
                    result = ProjectAgentResult(
                        new_messages=event.result.new_messages(),
                        usage=event.result.usage(),
                        logs=list(logs),
                    )
                    yield ProjectAgentResultEvent(result = result)
                    break # Ignore any further log events
                else:
                    # Add some logging
                    if isinstance(event, FunctionToolCallEvent):
                        yield log("INFO", f"Tool:{event.part.tool_name}", message=f"Called {event.part.tool_name} args: {json_dump_if(event.part.args)}")
                    elif isinstance(event, FunctionToolResultEvent):
                        if isinstance(event.result, RetryPromptPart):
                            yield log("WARNING", f"Tool:{event.result.tool_name}", f"Tool {event.result.tool_name} failed")
                        else:
                            yield log("INFO", f"Tool:{event.result.tool_name}", f"Tool {event.result.tool_name} completed")

                    yield event

        merger.add_stream(agent_stream())
        return aiter(merger)

    async def resolve_elicitation(self,
        elicitation_id: str,
        action: Literal["accept", "decline", "cancel"],
        content: dict[str, Any] | None = None
    ):
        """ Resolve the elicitation request with a value """
        future: asyncio.Future | None = self._elicitations.pop(elicitation_id, None)
        if future is None or future.done():
            raise KeyError(f"Elicitation {elicitation_id} not found or already resolved")
        future.set_result(mcp.types.ElicitResult(
            action=action,
            content=content if action == "accept" else None,
        ))

    async def cancel_elicitations(self):
        """ Cancel all outstanding elicitation requests """
        for future in self._elicitations.values():
            if not future.done():
                future.set_result(mcp.types.ElicitResult(action="cancel"))
        self._elicitations.clear()
