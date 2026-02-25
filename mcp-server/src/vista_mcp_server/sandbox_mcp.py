"""
MCP Server to run basic shell commands
"""

from __future__ import annotations
from typing import Annotated as A, TypedDict
import sys
from fastmcp import FastMCP, Context
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.server.lifespan import lifespan
from mcp.types import CallToolRequestParams, RequestParams
import asyncio
from .config import settings
from .lib.sandbox import Sandbox, DockerSandbox


# class SandboxMiddleware(Middleware):
#     async def on_call_tool(self, context: MiddlewareContext[CallToolRequestParams], call_next):
#         meta = {
#             "foo": "bar",
#         }
#         if context.fastmcp_context.request_context
#             meta = {**context.message.meta.model_dump(), **meta}
#         context.message.meta = RequestParams.Meta.model_validate(meta)
#         return await call_next(context)

sandbox: Sandbox
""" I should be able to use ctx.lifespan_context for this, but it doesn't work when mounted. """

@lifespan
async def app_lifespan(server):
    global sandbox
    sandbox = await DockerSandbox.spawn(
        volumes=settings.volumes,
        dockerfile=settings.dockerfile,
        image=settings.image,
    )
    try:
        yield
    finally:
        sandbox.close()

mcp = FastMCP(name="Sandbox", lifespan=app_lifespan)
# mcp.add_middleware(SandboxMiddleware())

@mcp.tool()
async def bash(
    ctx: Context,
    command: A[str, "Bash command to run"],
    description: A[str, "Why I'm running this command"] = "",
) -> str:
    """
    Run a bash command.

    Avoid commands that produce a large amount of output, and consider piping those outputs to
    files.
    """
    proc = await sandbox.exec("bash", args = ["-c", command], combine_streams=True)
    stdout, _ = await proc.communicate()
    return stdout.decode()


# TODO:
# - Output filtering in shell
# - file tools
# - Extendability
# - Handling containers per session
