"""
Build a `ProjectAgent` that runs a scripted LLM against fake MCP tools.

`ProjectAgent.run_stream` goes straight to `self.agent.run_stream_events`, so a
turn can be driven without `async with agent:` — which is what keeps the real
STDIO / HTTP MCP servers out of unit tests. `Agent.override` swaps in the
scripted model and the fake toolset for the duration of the block.

`override(toolsets=...)` replaces the *already filtered* toolsets the agent was
built with, so the project's tool allow/deny patterns would silently stop
applying. `agent_under_test` re-applies `_tool_allowed` to the fake toolset to
keep that behavior under test.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from pydantic_ai.models import Model

from vista_backend.agents.agents import ProjectAgent
from vista_backend.db.schemas import ProjectPublic, UserPublicWithConfig

from .fake_mcp import FakeMcp, fake_mcp


@contextmanager
def agent_under_test(
    project: ProjectPublic,
    user: UserPublicWithConfig,
    model: Model,
    *,
    session_id: uuid.UUID | None = None,
    mcp: FakeMcp | None = None,
) -> Iterator[tuple[ProjectAgent, FakeMcp]]:
    """
    Yield a `(ProjectAgent, FakeMcp)` pair wired to `model` and fake MCP tools.

    The overrides are active only inside the block.
    """
    mcp = mcp or fake_mcp()
    agent = ProjectAgent(project, user, session_id or uuid.uuid4())
    filtered = mcp.toolset.filtered(
        lambda ctx, tool: agent._tool_allowed(tool.name)  # noqa: SLF001
    )
    with agent.agent.override(model=model, toolsets=[filtered]):
        yield agent, mcp
