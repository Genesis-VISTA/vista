from pydantic_ai import Agent, AbstractToolset, RunContext
from pydantic_ai.mcp import MCPServerStdio, CallToolFunc
from pydantic_ai.messages import BinaryContent, ToolReturn
from pydantic_ai.models import infer_model
from pydantic_ai.ui.vercel_ai.response_types import FileChunk
from typing import Any

from .config import settings
from .sandbox import Sandbox
from .shell_tool import make_shell_toolset

async def _pack_binary_results(ctx: RunContext[Any], direct_call_tool: CallToolFunc, name: str, tool_args: dict[str, Any]):
    """
    Pydantic AI handles binary tool results weirdly, splitting them up into "multi-channel" messages
    and doesn't send them to the Vercel Frontend at all. It also doesn't properly filter results
    out based on audience annotations, resulting in sending unnecessary data to the model.

    There's issues to fix these issues, see:
    - https://github.com/pydantic/pydantic-ai/pull/3826
    - https://github.com/pydantic/pydantic-ai/pull/3826

    This is a workaround that addresses these issues by wrap MCP binary results in ToolReturn so
    FileChunks are sent to the frontend via SSE metadata. When the issues are fixed we should remove
    this and the changes in App.tsx.
    """
    result = await direct_call_tool(name, tool_args, None)

    items = result if isinstance(result, list) else [result]
    binary_items = [item for item in items if isinstance(item, BinaryContent)]
    if not binary_items:
        return result
    text_items = [item for item in items if not isinstance(item, BinaryContent)]
    file_chunks = [FileChunk(url=b.data_uri, media_type=b.media_type) for b in binary_items]
    return ToolReturn(
        return_value=(text_items[0] if len(text_items) == 1 else text_items),
        content=[], # Don't send binary_items to the model
        metadata=file_chunks,   # frontend gets the image via SSE FileChunk, not sent to model
    )


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

    toolsets: list[AbstractToolset] = []
    toolsets += [
        MCPServerStdio(
            server.command,
            args=server.args,
            env=server.env,
            cwd=server.cwd,
            timeout=15,
            process_tool_call=_pack_binary_results,
        )
        for server in settings.mcp_servers
    ]
    toolsets.append(make_shell_toolset(sandbox))
    toolsets += [
        sandbox.mcp_server(server, process_tool_call=_pack_binary_results)
        for server in settings.sandboxed_mcp_servers
    ]

    agent = Agent(model,
        system_prompt="You are a helpful assistant.",
        toolsets=toolsets,
    )
    return agent
