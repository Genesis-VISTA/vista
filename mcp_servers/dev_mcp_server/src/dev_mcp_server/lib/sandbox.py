import abc
import asyncio
import os
import uuid
from typing import Literal, Sequence
from pathlib import Path
import logging

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
    def __init__(self, container_id: str, proc: asyncio.subprocess.Process):
        self.container_id = container_id
        # We keep the docker run as an attached process with `docker run --rm`. This makes sure that
        # whenever the dev server dies, the container is removed.
        self._proc = proc

    @classmethod
    async def spawn(
        cls,
        volumes: Sequence[Volume] | None = None,
        env: dict[str, str] | None = None,
        image: str | None = None,
        dockerfile: Path | str | None = None,
    ) -> "DockerSandbox":
        volumes = list(volumes or [])
        env = env or {}
        if not image and not dockerfile:
            raise ValueError("You must specify image or dockerfile")
        if not image:
            image = "vista-sandbox"

        if dockerfile:
            logging.info("Building sandbox docker image...")
            await check_output(
                "docker", "build", "-t", image, "-f", str(dockerfile), str(Path(dockerfile).parent),
            )
        else:
            logging.info("Pulling sandbox docker image...")
            await check_output("docker", "pull", image)
        logging.info("Launching sandbox docker image...")

        container_name = f"vista-sandbox-{uuid.uuid4().hex[:12]}"
        run_args = [
            "docker", "run",
            "--rm", "-i", "--init",
            "--name", container_name,
        ]
        for src, dst, mode in volumes:
            run_args += ["-v", f"{src}:{dst}" + (":ro" if mode == 'r' else '')]
        run_args += [f"--env={var}" for var in env.keys()]
        # `cat` with no args reads stdin forever and exits on EOF, making container
        # lifetime a direct consequence of the host-side pipe staying open.
        run_args += [image, "cat"]

        proc = await asyncio.create_subprocess_exec(
            *run_args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            env={**os.environ, **env},
        )

        # Poll until the container is running.
        try:
            while True:
                if proc.returncode is not None:
                    raise RuntimeError(f"docker run exited with code {proc.returncode} before container started")
                try:
                    out, _ = await check_output(
                        "docker", "inspect", "--format={{.State.Running}}", container_name,
                    )
                except RuntimeError:
                    out = b""  # container doesn't exist yet
                if out.strip() == b"true":
                    break
                if out.strip() == b"false":
                    raise RuntimeError(f"container {container_name} exited immediately")
                await asyncio.sleep(0.1)
        except BaseException:
            if proc.stdin and not proc.stdin.is_closing():
                proc.stdin.close()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except (asyncio.TimeoutError, Exception):
                pass
            raise

        return cls(container_id=container_name, proc=proc)

    async def exec(
        self, command: str, args: list[str] | None = None,
        env: dict[str, str] | None = None, cwd: str | None = None, combine_streams = False,
    ) -> asyncio.subprocess.Process:
        env = env or {}
        cmd = ["docker", "exec", "-i"]
        if cwd:
            cmd += ["-w", cwd]
        cmd += [f"--env={var}={value}" for var, value in env.items()]
        cmd += [self.container_id, command]
        cmd += (args or [])
        return await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT if combine_streams else asyncio.subprocess.PIPE,
            env={**os.environ, **env},
        )

    def close(self) -> None:
        # Close the stdin pipe to the long-lived `docker run` client. PID 1 (`cat`) inside
        # the container sees EOF, exits, and `--rm` reaps the container — no `docker stop` /
        # `docker rm` calls required, so this method has nothing to await and can't get
        # caught in the SIGKILL-mid-cleanup window the old implementation had.
        if self._proc.stdin and not self._proc.stdin.is_closing():
            self._proc.stdin.close()


class UnSandbox(Sandbox):
    """
    "Sandbox" implementation that executes commands on the directly. Only intended for tests.
    """

    @classmethod
    async def spawn(cls, **kwargs) -> "UnSandbox":
        return UnSandbox()

    async def exec(
        self, command: str, args: list[str] | None = None,
        env: dict | None = None, cwd: str | None = None, combine_streams: bool = False,
    ) -> asyncio.subprocess.Process:
        return await asyncio.create_subprocess_exec(
            command, *(args or []),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT if combine_streams else asyncio.subprocess.PIPE,
            env=env,
            cwd=cwd,
        )

    def close(self) -> None:
        pass
