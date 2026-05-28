"""
MCP Server to run basic shell and file commands
"""
import argparse
import asyncio

from .config import settings # import before fastmcp so fastmcp reads our env vars

from typing import Annotated as A, Callable, Awaitable
from fastmcp import FastMCP, Context
from fastmcp.server.lifespan import lifespan
from mcp.types import ToolAnnotations
from .lib.sandbox import Sandbox
from .lib.container_sandbox import ContainerSandbox
from .lib.microsandbox_sandbox import MicrosandboxSandbox
from .lib.view import view_path

SANDBOX_SPAWN_FUNCS: dict[str, Callable[..., Awaitable[Sandbox]]] = {
    "microsandbox": lambda volumes=None, env=None: MicrosandboxSandbox.spawn(
        volumes=volumes, env=env,
        dockerfile=settings.dockerfile, image=settings.image,
    ),
    "container": lambda volumes=None, env=None: ContainerSandbox.spawn(
        volumes=volumes, env=env,
        dockerfile=settings.dockerfile, image=settings.image,
    ),
    "docker": lambda volumes=None, env=None: ContainerSandbox.spawn(
        volumes=volumes, env=env,
        dockerfile=settings.dockerfile, image=settings.image,
        runtime="docker",
    ),
    "podman": lambda volumes=None, env=None: ContainerSandbox.spawn(
        volumes=volumes, env=env,
        dockerfile=settings.dockerfile, image=settings.image,
        runtime="podman",
    ),
}

SANDBOX_BUILD_FUNCS: dict[str, Callable[[], Awaitable[None]]] = {
    "microsandbox": lambda: MicrosandboxSandbox.build(
        dockerfile=settings.dockerfile, image=settings.image,
    ),
    "container": lambda: ContainerSandbox.build(
        dockerfile=settings.dockerfile, image=settings.image,
    ),
    "docker": lambda: ContainerSandbox.build(
        dockerfile=settings.dockerfile, image=settings.image,
        runtime="docker",
    ),
    "podman": lambda: ContainerSandbox.build(
        dockerfile=settings.dockerfile, image=settings.image,
        runtime="podman",
    ),
}

sandbox: Sandbox
""" I should be able to use ctx.lifespan_context for this, but it doesn't work when mounted. """

@lifespan
async def app_lifespan(server):
    global sandbox
    # Create the volume directories first so they don't get created by docker and owned by the container user
    for host_path, container_path, mode in settings.volumes:
        host_path.mkdir(parents=True, exist_ok=True)
    sandbox = await SANDBOX_SPAWN_FUNCS[settings.sandbox_mode](volumes=settings.volumes)
    try:
        yield
    finally:
        await sandbox.close()

mcp = FastMCP(name="Dev MCP Server", lifespan=app_lifespan)

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False))
async def run_bash(
    command: A[str, "Bash command to run"],
    ctx: Context,
) -> str:
    """
    Run a bash command inside the sandbox.

    The sandbox has Python 3, numpy, matplotlib, and scipy.
    Skills are mounted at /mnt/skills/, output goes to /mnt/data/output/.

    Key paths:
      Database JSON: /mnt/skills/salt-analysis/assets/Molten_Salt_Thermophysical_Properties.json
      Analysis script: /mnt/skills/salt-analysis/scripts/analyze_salt.py
      Phase diagram script: /mnt/skills/salt-analysis/scripts/plot_phase_diagram.py

    When a plot is saved, it is automatically displayed to the user.
    Always set MPLBACKEND=Agg before running matplotlib.
    Avoid commands that produce a large amount of output — pipe to files instead.
    """
    proc = await sandbox.exec("bash", args=["-c", command], combine_streams=True)
    # `msb exec` forwards its stdin to the guest and won't exit until that pipe closes.
    if proc.stdin and not proc.stdin.is_closing(): proc.stdin.close()
    lines = []
    while True:
        line = (await proc.stdout.readline()).decode().replace("\r\n", "\n")
        if not line: break
        lines.append(line)
        await ctx.info(line.rstrip("\n"))
    await proc.wait()
    return "".join(lines)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False))
async def create_file(
    path: A[str, "Path to the file"],
    content: A[str, "Content to write"],
):
    """
    Create a new file.
    """
    proc = await sandbox.exec("tee", args = [path])
    await proc.communicate(content.encode())
    return f"Successfully created {path}"


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False))
async def view(
    path: A[str, "Path to the file or directory"],
    # Using tuple[int, int] creates a "prefixItems" schema that confuses openai.azure.com
    range: A[list[int]|None,
        "Optional range to view for files. Format: [start_line, end_line]. lines are indexed at 1. Negative numbers index from end of file."
    ] = None
):
    """
    View files and directories. For text files, displays numbered lines.
    Handles truncating large outputs.
    """
    if range is not None and len(range) != 2:
        raise ValueError("range must be exactly [start, end]")
    return await view_path(sandbox, path, tuple(range) if range is not None else None)


# TODO:
# - Output filtering in shell
# - file tools

def main():
    parser = argparse.ArgumentParser(description="Dev MCP Server")
    parser.add_argument("--transport", choices=["stdio", "http"], default="http")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8002)
    parser.add_argument(
        "--pre-build",
        action="store_true",
        help="Build/pull the sandbox image for the configured sandbox mode and exit without launching the MCP server."
    )
    args = parser.parse_args()
    if args.pre_build:
        asyncio.run(SANDBOX_BUILD_FUNCS[settings.sandbox_mode]())
        return
    if args.transport == "http":
        mcp.run(transport="http", host=args.host, port=args.port)
    else:
        mcp.run(transport="stdio")
