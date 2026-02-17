from __future__ import annotations
import abc
import asyncio
from pathlib import Path
from pydantic_ai.mcp import MCPServerStdio
from .config import McpServerConfig

DOCKERFILE_PATH = Path(__file__).parents[2] / "Dockerfile.sandbox"
IMAGE_NAME = "vista-sandbox"

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


def flatten(arr: list[list]):
    return [item for sub_list in arr for item in sub_list]


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
        self, command: str, args: list[str] | None = None
    ) -> asyncio.subprocess.Process: ...

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
    ) -> "DockerSandbox":
        # Build the sandbox image
        await check_output(
            "docker", "build", "-t", IMAGE_NAME, "-f", str(DOCKERFILE_PATH), DOCKERFILE_PATH.parent,
        )

        # Run container
        run_args = ["docker", "run", "-d"]
        run_args += flatten([
            ["-v", f"{host_path}:{container_path}"]
            for host_path, container_path in (volumes or {}).items()
        ])
        run_args += flatten([["-e", f"{k}={v}"] for k, v in (env or {}).items()])
        run_args += [IMAGE_NAME, "sleep", "infinity"]

        stdout, stderr = await check_output(*run_args)
        return cls(container_id=stdout.decode().strip())

    async def exec(
        self, command: str, args: list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> asyncio.subprocess.Process:
        cmd = ["docker", "exec", "-i", self.container_id, command]
        cmd += flatten([["-e", f"{k}={v}"] for k, v in (env or {}).items()])
        if args:
            cmd += args
        return await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    def mcp_server(self, config: McpServerConfig) -> MCPServerStdio:
        env_flags = flatten([["-e", f"{k}={v}"] for k, v in (config.env or {}).items()])
        return MCPServerStdio(
            "docker",
            args=[
                "exec", "-i",
                *env_flags,
                self.container_id,
                config.command,
                *config.args,
            ],
            timeout=30,
        )

    async def close(self) -> None:
        await check_output("docker", "stop", self.container_id, "-t", "2")
        await check_output("docker", "rm", self.container_id)
