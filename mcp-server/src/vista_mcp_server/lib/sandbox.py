from __future__ import annotations
import abc
import asyncio
import os
import subprocess
from typing import Literal, Sequence
from pathlib import Path

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

Volume = tuple[Path | str, Path | str, Literal['r', 'w']]

class Sandbox(abc.ABC):
    @classmethod
    @abc.abstractmethod
    async def spawn(
        cls,
        volumes: list[Volume] | None = None,
        env: dict[str, str] | None = None,
    ) -> Sandbox: ...

    @abc.abstractmethod
    async def exec(
        self, command: str, args: list[str] | None = None,
        env: dict[str, str] | None = None, cwd: str | None = None, combine_streams = False,
    ) -> asyncio.subprocess.Process: ...
    """
    Execute a command inside the sandbox.

    Args:
        command: Command to run
        args: args to the command
        env: environment variables
        cwd: workind directory
        combine_streams: Combine stdout and stderr streams (default False)
    """

    @abc.abstractmethod
    def close(self) -> None: ...


class DockerSandbox(Sandbox):
    def __init__(self, container_id: str):
        self.container_id = container_id

    @classmethod
    async def spawn(
        cls,
        volumes: Sequence[Volume] | None = None,
        env: dict[str, str] | None = None,
        image: str | None = None,
        dockerfile: Path | str | None = None
    ) -> "DockerSandbox":
        volumes = list(volumes or [])
        env = env or {}
        if not image and not dockerfile:
            raise ValueError("You must specify image or dockerfile")

        if dockerfile:
            await check_output(
                "docker", "build", "-t", image, "-f", str(dockerfile), str(Path(dockerfile).parent),
            )
        else:
            await check_output("docker", "pull", image)

        # Run container
        run_args = ["docker", "run", "-d"]
        for src, dst, mode in volumes:
            run_args += ["-v", f"{src}:{dst}" + (":ro" if mode == 'r' else '')]

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

    def close(self) -> None:
        # proc = await asyncio.create_subprocess_exec("docker", "stop", self.container_id, "-t", "1")
        # await proc.wait()
        # proc = await asyncio.create_subprocess_exec("docker", "rm", "-f", self.container_id)
        # await proc.wait()

        # TODO: Running async processes in FastMCP lifespan function clean errors out because of
        # some issues with how FastMCP handles the event loop. So I'm using sync here for now.
        # Should change close back to async when I can
        subprocess.run(["docker", "stop", self.container_id, "-t", "1"], capture_output=True)
        subprocess.run(["docker", "rm", "-f", self.container_id], capture_output=True)
