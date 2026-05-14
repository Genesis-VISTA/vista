"""
Logic to build the actual PydanticAI Agent
"""
import fnmatch
import json
from typing import AsyncIterator, Literal, Annotated as A
from pathlib import Path

from pydantic import BaseModel, Field, Discriminator
from pydantic_ai import Agent, RunContext, UsageLimits, RunUsage, AgentRunResultEvent
from pydantic_ai.mcp import MCPServerStreamableHTTP, ProcessToolCallback
from pydantic_ai.messages import (
    AgentStreamEvent,
    ModelMessage,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    RetryPromptPart,
)
from pydantic_ai.models import infer_model
import mcp.client.session
import mcp.types

from ..config import settings
from ..db.schemas import ProjectPublic
from ..utils.streams import StreamMerger
from ..utils.misc import json_dump_if
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

ProjectAgentStreamEvent = A[AgentStreamEvent | LogEvent | ProjectAgentResultEvent, Discriminator("event_kind")]


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
    def __init__(self, project: ProjectPublic):
        self.project = project

    def _build_agent(self,
        elicitation_callback: mcp.client.session.ElicitationFnT | None = None,
        process_tool_call: ProcessToolCallback | None = None,
        log_handler: mcp.client.session.LoggingFnT | None = None,
    ) -> Agent:
        """
        Construct a PydanticAI Agent for a project.
        """
        mcp_server = get_mcp_server(
            elicitation_callback=elicitation_callback,
            process_tool_call=process_tool_call,
            log_handler=log_handler,
        )
        toolset = mcp_server.filtered(lambda ctx, tool: _tool_allowed(tool.name, self.project.tools))

        agent = Agent(
            model=infer_model(settings.model),
            toolsets=[toolset],
        )

        skills_block = to_prompt([settings.skills_dir / skill for skill in self.project.skills], {
            settings.skills_dir: "/mnt/skills",
        })

        @agent.system_prompt
        def system_prompt(ctx: RunContext[str]) -> str:
            parts = [BASE_SYSTEM_PROMPT]
            if self.project.system_prompt:
                parts.append("## Project Information")
                parts.append(self.project.system_prompt)
            parts.append(skills_block)
            return "\n\n".join([p for p in parts if p])

        return agent

    async def run(self,
        user_prompt: str,
        message_history: list[ModelMessage]|None = None,
        elicitation_callback: mcp.client.session.ElicitationFnT|None = None,
        process_tool_call: ProcessToolCallback | None = None,
    ) -> ProjectAgentResult:
        """
        Run the agent for a single agent "turn".

        See run_stream for more info.
        """

        async for event in self.run_stream(
            user_prompt = user_prompt,
            message_history = message_history,
            elicitation_callback=elicitation_callback,
            process_tool_call=process_tool_call,
        ):
            if isinstance(event, ProjectAgentResultEvent):
                return event.result
        raise RuntimeError("Agent didn't emit a result") # Should be unreachable

    def run_stream(self,
        user_prompt: str,
        message_history: list[ModelMessage]|None = None,
        elicitation_callback: mcp.client.session.ElicitationFnT|None = None,
        process_tool_call: ProcessToolCallback | None = None,
    ) -> AsyncIterator[ProjectAgentStreamEvent]:
        """
        Run the agent and return a stream of events.

        Yields all events from Pydantic, see `pydantic_ai.messages.AgentStreamEvent` and
        https://pydantic.dev/docs/ai/core-concepts/agent/#running-agents for more info.

        Also adds LogEvents of our own, that contains log lines from the agent and MCP server.

        Pass elicitation_callback to support MCP elicitation. The function should return an awaitable
        that completes with the elicitation result.
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
            except RuntimeError:
                pass # Merger already closed, the run finished before this notification.

        agent = self._build_agent(
            elicitation_callback=elicitation_callback,
            process_tool_call=process_tool_call,
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
