"""
Shared harness for hermetic `ProjectAgent` tests (testing roadmap Milestone B).

Nothing here touches the network, a real LLM, or the MCP servers: turns run on
a scripted `FunctionModel` against an in-process fake toolset.

    from harness import agent_under_test, call, fake_mcp, make_project, say, step_model

    with agent_under_test(project, user, step_model([call("rag_search", query="NaCl"), say("done")])) as (agent, mcp):
        events = [e async for e in agent.run_stream(user_prompt="hi")]
    assert mcp.names_called() == ["rag_search"]
"""

from .agent import agent_under_test
from .api import FakeAgentPool, api_client, parse_sse
from .fake_mcp import FakeMcp, ToolCallRecord, fake_mcp
from .factories import make_project, make_user, seed_project, seed_user
from .scripted import always_calls, call, say, scripted_model, step_model

__all__ = [
    "FakeAgentPool",
    "FakeMcp",
    "ToolCallRecord",
    "agent_under_test",
    "always_calls",
    "api_client",
    "call",
    "fake_mcp",
    "make_project",
    "make_user",
    "parse_sse",
    "say",
    "scripted_model",
    "seed_project",
    "seed_user",
    "step_model",
]
