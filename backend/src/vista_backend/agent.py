from pydantic_ai import Agent, AbstractToolset
from pydantic_ai.mcp import MCPServerStdio
from pydantic_ai.models import infer_model

from .config import settings
from .sandbox import Sandbox
from .shell_tool import make_shell_toolset

SYSTEM_PROMPT="""
You are a helpful assistant familiar with data analysis.

You have access to a development environment which contains:
- Python
- pandas, numpy, scipy, matplotlib
- node 24

Important directories:
- /mnt/user-data/uploads - contains readonly user uploaded data
- /mnt/user-data/outputs - Place outputs such as scripts and files to show the user here.
""".strip()


def make_agent(sandbox: Sandbox):
    # Pydantic AI will automatically pick up other env vars needed. E.g. for Azure set
    # AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT, OPENAI_API_VERSION
    model = infer_model(settings.model)

    toolsets: list[AbstractToolset] = [
        MCPServerStdio(
            server.command,
            args=server.args,
            env=server.env,
            timeout=15,
        )
        for server in settings.mcp_servers
    ]

    toolsets.append(make_shell_toolset(sandbox))

    for server in settings.sandboxed_mcp_servers:
        toolsets.append(sandbox.mcp_server(server))

    agent = Agent(model,
        system_prompt="You are a helpful assistant.",
        toolsets=toolsets,
    )
    return agent
