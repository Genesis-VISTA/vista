import asyncio
import os
import shutil
import uuid
from typing import Sequence
from pathlib import Path
import logging

from .sandbox import Sandbox, Volume
from .util import check_output


def resolve_container_runtime(runtime: str | None = None) -> str:
    if runtime:
        path = shutil.which(runtime)
        if not path:
            raise RuntimeError(f"{runtime} not found on PATH")
    else:
        for candidate in ("docker", "podman"):
            path = shutil.which(candidate)
            if path:
                break
        else:
            raise RuntimeError("Neither 'podman' nor 'docker' found on PATH")
    return path


class ContainerSandbox(Sandbox):
    """
    Sandbox that runs commands inside a container managed by podman or docker.
    """
    runtime: str

    def __init__(self, container_id: str, proc: asyncio.subprocess.Process, runtime: str):
        self.container_id = container_id
        self.runtime = runtime
        # We keep the container run as an attached process with `--rm`. This makes sure that
        # whenever the dev server dies, the container is removed.
        self._proc = proc

    @classmethod
    async def build(
        cls,
        dockerfile: Path | str | None = None,
        image: str | None = None,
        runtime: str | None = None,
    ) -> None:
        runtime = resolve_container_runtime(runtime)
        if not image and not dockerfile:
            raise ValueError("You must specify image or dockerfile")
        if not image:
            image = "vista-sandbox"
        if dockerfile:
            logging.info("Building sandbox image...")
            await check_output(
                runtime, "build", "-t", image, "-f", str(dockerfile), str(Path(dockerfile).parent),
            )
        else:
            logging.info("Pulling sandbox image...")
            await check_output(runtime, "pull", image)

    @classmethod
    async def spawn(
        cls,
        volumes: Sequence[Volume] | None = None,
        env: dict[str, str] | None = None,
        image: str | None = None,
        dockerfile: Path | str | None = None,
        runtime: str | None = None,
    ) -> "ContainerSandbox":
        runtime = resolve_container_runtime(runtime)
        volumes = list(volumes or [])
        env = env or {}
        if not image and not dockerfile:
            raise ValueError("You must specify image or dockerfile")
        if not image:
            image = "vista-sandbox"

        await cls.build(dockerfile=dockerfile, image=image, runtime=runtime)
        logging.info("Launching sandbox container...")

        container_name = f"vista-sandbox-{uuid.uuid4().hex[:12]}"
        run_args = [
            runtime, "run",
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
                    raise RuntimeError(f"{runtime} run exited with code {proc.returncode} before container started")
                try:
                    out, _ = await check_output(
                        runtime, "inspect", "--format={{.State.Running}}", container_name,
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

        return cls(container_id=container_name, proc=proc, runtime=runtime)

    async def exec(
        self, command: str, args: list[str] | None = None,
        env: dict[str, str] | None = None, cwd: str | None = None, combine_streams: bool = False,
    ) -> asyncio.subprocess.Process:
        env = env or {}
        cmd = [self.runtime, "exec", "-i"]
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

    async def close(self) -> None:
        # Close the stdin pipe to the long-lived `run` client. PID 1 (`cat`) inside
        # the container sees EOF, exits, and `--rm` reaps the container — no `stop` /
        # `rm` calls required, so this method has nothing to await and can't get
        # caught in the SIGKILL-mid-cleanup window the old implementation had.
        if self._proc.stdin and not self._proc.stdin.is_closing():
            self._proc.stdin.close()
