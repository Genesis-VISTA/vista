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
from mcp.client.session import ElicitationFnT

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
    elicitation_callback: ElicitationFnT | None = None,
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


def build_project_agent(
    project: ProjectPublic,
    elicitation_callback: ElicitationFnT | None = None,
    process_tool_call: ProcessToolCallback | None = None,
) -> Agent:
    """
    Construct a PydanticAI Agent for a project.
    """
    mcp = get_mcp_server(
        elicitation_callback=elicitation_callback,
        process_tool_call=process_tool_call,
    )
    toolset = mcp.filtered(lambda ctx, tool: _tool_allowed(tool.name, project.tools))

    agent = Agent(
        model=infer_model(settings.model),
        toolsets=[toolset],
    )

    skills_block = to_prompt([settings.skills_dir / skill for skill in project.skills])

    @agent.system_prompt
    def system_prompt(ctx: RunContext[str]) -> str:
        parts = [
            BASE_SYSTEM_PROMPT,
            project.system_prompt or "",
            skills_block,
        ]
        return "\n\n".join([p for p in parts if p])

    return agent


async def run_project_agent(
    project: ProjectPublic,
    agent: Agent,
    user_prompt: str,
    message_history: list[ModelMessage],
) -> AgentRunResult:
    """Run the agent, returns new messages to append to history."""
    usage_limits = UsageLimits(**(project.usage_limits or {}))
    result = await agent.run(
        user_prompt,
        message_history=message_history,
        usage_limits=usage_limits,
    )
    return result


async def run_project_agent_stream(
    project: ProjectPublic,
    agent: Agent,
    user_prompt: str,
    message_history: list[ModelMessage],
) -> AsyncIterator[AgentStreamEvent | AgentRunResultEvent]:
    """
    Stream the agent run as PydanticAI events.

    Yields every event PydanticAI produces during the run -- model response part starts /
    deltas / ends, function tool call invocations, tool returns -- and finally a single
    `AgentRunResultEvent` carrying the run's new messages and usage.

    Schema reference: see `pydantic_ai.messages.AgentStreamEvent` and
    `pydantic_ai.AgentRunResultEvent`. The PydanticAI docs at
    https://pydantic.dev/docs/ai/core-concepts/agent/#running-agents describe the event
    semantics.
    """
    usage_limits = UsageLimits(**(project.usage_limits or {}))
    async for event in agent.run_stream_events(
        user_prompt,
        message_history=message_history,
        usage_limits=usage_limits,
    ):
        yield event

