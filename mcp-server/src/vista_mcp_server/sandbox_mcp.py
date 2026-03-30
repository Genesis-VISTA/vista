"""
MCP Server to run basic shell commands
"""

from __future__ import annotations
from typing import Annotated as A
from fastmcp import FastMCP
from fastmcp.server.lifespan import lifespan
from .config import settings
from .lib.sandbox import Sandbox, DockerSandbox
from .lib.view import view_path

sandbox: Sandbox
""" I should be able to use ctx.lifespan_context for this, but it doesn't work when mounted. """

@lifespan
async def app_lifespan(server):
    global sandbox
    # Create the volume directories first so they don't get created by docker and owned by the container user
    for host_path, container_path, mode in settings.volumes:
        host_path.mkdir(parents=True, exist_ok=True)
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
async def run_bash(
    command: A[str, "Bash command to run"],
) -> str:
    """
    Run a bash command inside the sandbox.

    ALWAYS use this tool for any question about the molten salt database.
    Never answer data questions from memory — run code to get precise values.

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
    proc = await sandbox.exec("bash", args = ["-c", command], combine_streams=True)
    stdout, _ = await proc.communicate()
    return stdout.decode()


@mcp.tool()
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


@mcp.tool()
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
# - Extendability
# - Handling containers per session
