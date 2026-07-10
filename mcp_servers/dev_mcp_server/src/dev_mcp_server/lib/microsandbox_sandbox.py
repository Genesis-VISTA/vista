import asyncio
import logging
import os
import pty
import tempfile
import uuid
from pathlib import Path

from microsandbox import Sandbox as MsbSandbox, Volume as MsbVolume, Network, PullPolicy
from microsandbox.types import DnsConfig  # not re-exported from package root
from microsandbox._runtime import msb_path as _msb_path

from .sandbox import Sandbox, Volume
from .util import check_output, parse_output
from .container_sandbox import resolve_container_runtime


class MicrosandboxSandbox(Sandbox):
    """
    Sandbox backed by https://github.com/superradcompany/microsandbox MicroVMs.
    """

    def __init__(self, sandbox: MsbSandbox):
        self._sandbox = sandbox

    @classmethod
    async def _build(
        cls,
        dockerfile: Path | str | None = None,
        image: str | None = None,
    ):
        if dockerfile:
            image = image or "vista-sandbox:latest"
            dockerfile = Path(dockerfile).resolve()
            runtime = resolve_container_runtime()
            logging.info(
                f"Building sandbox image {image}...",
            )
            await check_output(
                runtime,
                "build",
                "-t",
                image,
                "-f",
                str(dockerfile),
                str(dockerfile.parent),
            )

            oci_inspect = await parse_output(runtime, "image", "inspect", image)
            oci_digest = oci_inspect[0]["Id"].split(":")[
                -1
            ]  # podman doesn't prefix sha256:
            # TODO: in microsandbox 0.5.5, we should be able to use the python SDK for this
            msb_inspect = await parse_output(
                str(_msb_path()), "image", "inspect", "--format=json", image
            )
            msb_digest = (
                msb_inspect["config"]["digest"].split(":")[-1] if msb_inspect else None
            )
            if msb_digest != oci_digest:
                with tempfile.TemporaryDirectory() as tmpdir:
                    archive = str(Path(tmpdir) / "image.tar")
                    await check_output(runtime, "save", "-o", archive, image)
                    await check_output(
                        str(_msb_path()), "load", "-i", archive, "-t", image
                    )
        elif image:
            msb_inspect = await parse_output(
                str(_msb_path()), "image", "inspect", "--format=json", image
            )
            if not msb_inspect:
                logging.info(f"Pulling sandbox image {image}...")
                await check_output(str(_msb_path()), "pull", image)
        else:
            raise ValueError("You must specify image or dockerfile")

        return image

    @classmethod
    async def build(
        cls,
        dockerfile: Path | str | None = None,
        image: str | None = None,
    ) -> None:
        await cls._build(dockerfile=dockerfile, image=image)

    @classmethod
    async def spawn(
        cls,
        volumes: list[Volume] | None = None,
        env: dict[str, str] | None = None,
        image: str | None = None,
        dockerfile: Path | str | None = None,
        cpus: int = 1,
        memory: int = 1024,
    ) -> "MicrosandboxSandbox":
        volumes = list(volumes or [])

        msb_volumes = {
            str(dst): MsbVolume.bind(str(src), readonly=(mode == "r"))
            for src, dst, mode in volumes
        }

        image = await cls._build(dockerfile=dockerfile, image=image)

        name = f"vista-sandbox-{uuid.uuid4().hex[:12]}"
        logging.info(f"Launching microsandbox {name}...")
        sandbox = await MsbSandbox.create(
            name,
            image=image,
            pull_policy=PullPolicy.NEVER,  # Should have already been built or pulled
            replace=True,
            cpus=cpus,
            memory=memory,
            shell="/bin/bash",
            volumes=msb_volumes,
            env=dict(env) if env else {},
            # TODO: Note, there's currently and issue where microsandbox writes /etc/resolv.conf with mode 0700, so if we make the sandbox image non root dns fails
            network=Network(
                # policy=NetworkPolicy(
                #     default_egress=Action.DENY,
                #     rules=tuple([
                #         *Rule.allow_dns(),
                #         *[Rule.allow(direction=Direction.EGRESS, destination=Destination.domain(d), port=443, protocol=Protocol.TCP) for d in ["www.example.com"]],
                #     ]),
                # ),
                policy="public_only",
                dns=DnsConfig(nameservers=("1.1.1.1", "8.8.8.8")),
            ),
        )
        return cls(sandbox=sandbox)

    async def exec(
        self,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        combine_streams: bool = False,
    ) -> asyncio.subprocess.Process:
        cmd = [str(_msb_path()), "exec", "--quiet", await self._sandbox.name]
        if cwd:
            cmd += ["--workdir", cwd]
        for key, value in (env or {}).items():
            cmd += ["--env", f"{key}={value}"]
        cmd += ["--", command]
        cmd += args or []
        # msb allocates a guest PTY (enabling line-by-line streaming) only when its
        # own stdin is a TTY. We give it a pty slave so isatty(stdin) is true while
        # keeping proc.stdout as a normal pipe for async readline.
        master, slave = pty.openpty()
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=slave,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT
                if combine_streams
                else asyncio.subprocess.PIPE,
            )
        except:
            os.close(master)
            raise
        finally:
            os.close(slave)

        async def _close_master(proc: asyncio.subprocess.Process, fd: int) -> None:
            await proc.wait()
            os.close(fd)

        proc._pty_cleanup = asyncio.ensure_future(_close_master(proc, master))  # type: ignore[attr-defined]
        return proc

    async def close(self) -> None:
        name = await self._sandbox.name
        try:
            await self._sandbox.stop_and_wait()
        except Exception:
            logging.exception(f"Failed to stop microsandbox {name}")
        try:
            await MsbSandbox.remove(name)
        except Exception:
            logging.exception(f"Failed to remove microsandbox {name}")
