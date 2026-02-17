from pydantic_ai import Agent
from pydantic_ai.mcp import MCPServerStdio
from pydantic_ai.models import infer_model

from .config import settings

def make_agent(extra_toolsets=None):
    # Pydantic AI will automatically pick up other env vars needed. E.g. for Azure set
    # AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT, OPENAI_API_VERSION
    model = infer_model(settings.model)

    mcp_servers = [
        MCPServerStdio(
            server.command,
            args=server.args,
            env=server.env,
        )
        for server in settings.mcp_servers
    ]

    toolsets = mcp_servers + (extra_toolsets or [])

    agent = Agent(model,
        system_prompt="You are a helpful assistant.",
        toolsets=toolsets,
    )
    return agent
