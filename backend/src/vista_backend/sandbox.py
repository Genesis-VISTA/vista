from __future__ import annotations
import abc
import asyncio
import os
from pathlib import Path
from pydantic_ai.mcp import MCPServerStdio
from .config import McpServerConfig

DEFAULT_SANDBOX_DOCKERFILE = Path(__file__).parents[2] / "sandbox/Dockerfile"

async def check_output(*args, **kwargs):
    proc = await asyncio.create_subprocess_exec(*args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **kwargs,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"cmd '{' '.join(args)}' failed: {stderr.decode()}")
    return stdout, stderr


class Sandbox(abc.ABC):
    @classmethod
    @abc.abstractmethod
    async def spawn(
        cls,
        volumes: dict[str, str] | None = None,
        env: dict[str, str] | None = None,
    ) -> Sandbox: ...

    @abc.abstractmethod
    async def exec(
        self, command: str, args: list[str] | None = None,
        env: dict[str, str] | None = None, combine_streams = False,
    ) -> asyncio.subprocess.Process: ...
    """
    Execute a command inside the sandbox.

    Args:
        command: Command to run
        args: args to the command
        env: environment variables
        combine_streams: Combine stdout and stderr streams (default False)
    """

    @abc.abstractmethod
    async def close(self) -> None: ...

    @abc.abstractmethod
    def mcp_server(self, config: McpServerConfig) -> MCPServerStdio:
        """Wrap an MCP server config so it runs inside this sandbox."""
        ...

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.close()


class DockerSandbox(Sandbox):
    def __init__(self, container_id: str):
        self.container_id = container_id

    @classmethod
    async def spawn(
        cls,
        volumes: dict[str, str] | None = None,
        env: dict[str, str] | None = None,
        image: str | None = None,
        dockerfile: Path | str | None = None
    ) -> "DockerSandbox":
        env = env or {}

        if not image and not dockerfile:
            dockerfile = DEFAULT_SANDBOX_DOCKERFILE
        image = image or "vista-sandbox:latest"

        if dockerfile:
            await check_output(
                "docker", "build", "-t", image, "-f", str(dockerfile), str(Path(dockerfile).parent),
            )
        else:
            await check_output("docker", "pull", image)

        # Run container
        run_args = ["docker", "run", "-d"]
        for host_path, container_path in (volumes or {}).items():
            run_args += ["-v", f"{host_path}:{container_path}"]
        run_args += [f"--env={var}" for var in env.keys()]
        run_args += [image, "sleep", "infinity"]

        stdout, stderr = await check_output(*run_args, env = {**os.environ, **env})
        return cls(container_id=stdout.decode().strip())

    async def exec(
        self, command: str, args: list[str] | None = None,
        env: dict[str, str] | None = None, combine_streams = False,
    ) -> asyncio.subprocess.Process:
        env = env or {}
        cmd = ["docker", "exec", "-i", self.container_id, command]
        cmd += [f"--env={var}" for var in env.keys()]
        cmd += (args or [])
        return await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT if combine_streams else asyncio.subprocess.PIPE,
            env={**os.environ, **env},
        )

    def mcp_server(self, config: McpServerConfig) -> MCPServerStdio:
        args = ["exec", "-i"]
        args += [f"--env={var}" for var in config.env.keys()]
        if config.cwd:
            args += ["-w", config.cwd]
        args += [self.container_id, config.command, *config.args]

        return MCPServerStdio("docker", args,
            timeout=30,
            env={**os.environ, **config.env},
        )

    async def close(self) -> None:
        proc = await asyncio.create_subprocess_exec("docker", "stop", self.container_id, "-t", "1")
        await proc.wait()
        proc = await asyncio.create_subprocess_exec("docker", "rm", "-f", self.container_id)
        await proc.wait()
