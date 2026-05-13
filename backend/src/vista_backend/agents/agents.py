"""
Logic to build the actual PydanticAI Agent
"""
import fnmatch
from typing import AsyncIterator
from pathlib import Path

from pydantic_ai import Agent, RunContext, UsageLimits, AgentRunResult, AgentRunResultEvent
from pydantic_ai.mcp import MCPServerStreamableHTTP, ProcessToolCallback
from pydantic_ai.messages import AgentStreamEvent, ModelMessage
from pydantic_ai.models import infer_model
import mcp.client.session

from ..config import settings
from ..db.schemas import ProjectPublic
from .skills import to_prompt


BASE_SYSTEM_PROMPT = (Path(__file__).parent / "base_system_prompt.md").read_text()


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
) -> MCPServerStreamableHTTP:
    """
    Connect to the VISTA MCP Server
    """
    return MCPServerStreamableHTTP(
        url=settings.mcp_url,
        elicitation_callback=elicitation_callback,
        process_tool_call=process_tool_call,
        timeout=10,
        read_timeout=1800 + 60,
    )


class ProjectAgent:
    def __init__(self, project: ProjectPublic):
        self.project = project
    
    def _build_agent(self,
        elicitation_callback: mcp.client.session.ElicitationFnT | None = None,
        process_tool_call: ProcessToolCallback | None = None,
    ) -> Agent:
        """
        Construct a PydanticAI Agent for a project.
        """
        mcp = get_mcp_server(
            elicitation_callback=elicitation_callback,
            process_tool_call=process_tool_call,
        )
        toolset = mcp.filtered(lambda ctx, tool: _tool_allowed(tool.name, self.project.tools))

        agent = Agent(
            model=infer_model(settings.model),
            toolsets=[toolset],
        )

        skills_block = to_prompt([settings.skills_dir / skill for skill in self.project.skills])

        @agent.system_prompt
        def system_prompt(ctx: RunContext[str]) -> str:
            parts = [
                BASE_SYSTEM_PROMPT,
                self.project.system_prompt or "",
                skills_block,
            ]
            return "\n\n".join([p for p in parts if p])

        return agent

    async def run(self,
        user_prompt: str,
        message_history: list[ModelMessage]|None = None,
        elicitation_callback: mcp.client.session.ElicitationFnT|None = None,
        process_tool_call: ProcessToolCallback | None = None,
    ) -> AgentRunResult:
        """
        Run the agent for a single agent "turn"

        See run_stream for more info.
        """

        async for event in self.run_stream(
            user_prompt = user_prompt,
            message_history = message_history,
            elicitation_callback=elicitation_callback,
            process_tool_call=process_tool_call,
        ):
            if isinstance(event, AgentRunResultEvent):
                return event.result
        raise RuntimeError("Pydantic AI didn't emit AgentRunResultEvent") # Should be unreachable

    async def run_stream(self,
        user_prompt: str,
        message_history: list[ModelMessage]|None = None,
        elicitation_callback: mcp.client.session.ElicitationFnT|None = None,
        process_tool_call: ProcessToolCallback | None = None,
    ) -> AsyncIterator[AgentStreamEvent | AgentRunResultEvent]:
        """
        Run the agent and return a stream of PydanticAI events.

        See `pydantic_ai.messages.AgentStreamEvent` and `pydantic_ai.AgentRunResultEvent` and
        https://pydantic.dev/docs/ai/core-concepts/agent/#running-agents

        Pass elicitation_callback to support MCP elicitation. The function should return an awaitable
        that completes with the elicitation result.
        """
        usage_limits = UsageLimits(**(self.project.usage_limits or {}))
        agent = self._build_agent(
            elicitation_callback=elicitation_callback,
            process_tool_call=process_tool_call,
        )

        async for event in agent.run_stream_events(
            user_prompt,
            message_history=message_history,
            usage_limits=usage_limits,
        ):
            yield event

