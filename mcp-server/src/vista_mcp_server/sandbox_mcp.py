"""
MCP Server to run basic shell commands
"""

from __future__ import annotations
from typing import Annotated as A
import sys
from fastmcp import FastMCP, Context
from fastmcp.server.lifespan import lifespan
import asyncio
from .config import settings
from .lib.sandbox import Sandbox, DockerSandbox

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

@mcp.tool()
async def bash(
    command: A[str, "Bash command to run"],
) -> str:
    """
    Run a bash command.

    Avoid commands that produce a large amount of output, and consider piping those outputs to
    files.
    """
    proc = await sandbox.exec("bash", args = ["-c", command], combine_streams=True)
    stdout, _ = await proc.communicate()
    return stdout.decode()


@mcp.tool()
async def create_file(*,
    path: A[str, "Path to the file"],
    content: A[str, "Content to write"],
):
    """
    Create a new file.
    """
    proc = await sandbox.exec("tee", args = [path])
    await proc.communicate(content.encode())
    return f"Successfully created {path}"

# TODO:
# - Output filtering in shell
# - file tools
# - Extendability
# - Handling containers per session
