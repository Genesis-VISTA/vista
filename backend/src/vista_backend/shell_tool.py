from pydantic_ai import FunctionToolset, RunContext
from .sandbox import Sandbox

def make_shell_toolset(sandbox: Sandbox):
    """
    I can't find a "shell" mcp server I like so just adding a custom shell tool.
    I'm not putting this in the vista_mcp_server as that should run *outside* the sandbox so
    it can pull AmSC datasets and provide MCP UI resources safely.

    TODO: Should refactor this. Maybe move this out into a second MCP server that runs inside the
    container. Or change it to use Pydantic AI deps instead of creating the toolset on the fly.
    """
    shell_toolset = FunctionToolset()

    @shell_toolset.tool(
        docstring_format='google',
        require_parameter_descriptions=True,
    )
    async def bash(ctx: RunContext, command: str, description: str = ""):
        """
        Run a bash command.

        Avoid commands that produce a large amount of output, and consider piping those outputs to
        files.

        Args:
            command: Bash command to run
            description: Why I'm running this command
        """
        proc = await sandbox.exec(
            command="/bin/bash", args = ["-c", command],
            combine_streams=True,
        )
        stdout, _ = await proc.communicate()
        return stdout

    return shell_toolset
