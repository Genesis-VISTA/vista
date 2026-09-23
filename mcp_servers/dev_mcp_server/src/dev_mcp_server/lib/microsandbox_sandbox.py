import asyncio
import logging
import tempfile
import uuid
from pathlib import Path

from microsandbox import (
    ExecEventType,
    ExecHandle,
    ExecSink,
    Image,
    Network,
    NetworkProfile,
    PullPolicy,
    Sandbox as MsbSandbox,
    Stdin,
    Volume as MsbVolume,
)
from microsandbox.errors import ImageNotFoundError
from microsandbox._runtime import msb_path as _msb_path

from .sandbox import Sandbox, SandboxProcess, Volume
from .util import check_output, parse_output
from .container_sandbox import resolve_container_runtime


async def _msb_image_digest(image: str) -> str | None:
    """Config digest of an image in the microsandbox store, or None if it isn't there."""
    try:
        detail = await Image.inspect(image)
    except ImageNotFoundError:
        return None
    return detail.config.digest.split(":")[-1] if detail.config else None


class _ExecStdin:
    """The subset of `asyncio.StreamWriter` that callers use on `proc.stdin`.

    `ExecSink` is async-only, while `StreamWriter.write` and `.close` are synchronous, so writes
    and the close are chained onto one task to keep them in order.
    """

    def __init__(self, sink: ExecSink):
        self._sink = sink
        self._closing = False
        self._tail: asyncio.Future = asyncio.get_running_loop().create_future()
        self._tail.set_result(None)

    def _chain(self, op) -> None:
        previous = self._tail

        async def run():
            await previous
            await op()

        self._tail = asyncio.ensure_future(run())

    def write(self, data: bytes) -> None:
        self._chain(lambda: self._sink.write(data))

    async def drain(self) -> None:
        await self._tail

    def close(self) -> None:
        if not self._closing:
            self._closing = True
            self._chain(self._sink.close)

    def is_closing(self) -> bool:
        return self._closing

    async def wait_closed(self) -> None:
        await self._tail


class _ExecProcess(SandboxProcess):
    """An `asyncio.subprocess.Process` look-alike over a microsandbox `ExecHandle`."""

    def __init__(self, handle: ExecHandle, combine_streams: bool):
        self._handle = handle
        self.returncode: int | None = None
        self.stdout = asyncio.StreamReader()
        self.stderr = None if combine_streams else asyncio.StreamReader()
        sink = handle.take_stdin()
        self.stdin = _ExecStdin(sink) if sink else None
        self._pump = asyncio.ensure_future(self._pump_events())

    async def _pump_events(self) -> None:
        stderr = self.stderr or self.stdout
        try:
            async for event in self._handle:
                if event.event_type == ExecEventType.STDOUT and event.data:
                    self.stdout.feed_data(event.data)
                elif event.event_type == ExecEventType.STDERR and event.data:
                    stderr.feed_data(event.data)
                elif event.event_type == ExecEventType.EXITED:
                    self.returncode = event.code
                elif event.event_type == ExecEventType.FAILED:
                    if event.data:
                        stderr.feed_data(event.data)
                    self.returncode = event.code if event.code is not None else -1
            if self.returncode is None:
                self.returncode, _ = await self._handle.wait()
        finally:
            self.stdout.feed_eof()
            if self.stderr:
                self.stderr.feed_eof()

    async def wait(self) -> int:
        await self._pump
        assert self.returncode is not None
        return self.returncode

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes | None]:
        if self.stdin:
            if input:
                self.stdin.write(input)
            self.stdin.close()
            await self.stdin.wait_closed()
        stdout, stderr = await asyncio.gather(
            self.stdout.read(),
            self.stderr.read() if self.stderr else asyncio.sleep(0, result=None),
        )
        await self.wait()
        return stdout, stderr

    async def kill(self) -> None:
        await self._handle.kill()


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
            if await _msb_image_digest(image) != oci_digest:
                with tempfile.TemporaryDirectory() as tmpdir:
                    archive = str(Path(tmpdir) / "image.tar")
                    await check_output(runtime, "save", "-o", archive, image)
                    await Image.load(archive, tag=image)
        elif image:
            if await _msb_image_digest(image) is None:
                logging.info(f"Pulling sandbox image {image}...")
                # The SDK only pulls as part of creating a sandbox, and build() must not create one.
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
            # DNS is left to microsandbox, which follows the host's own resolver on every
            # platform (SCDynamicStore on macOS, resolv.conf on Linux, the DNS Client on Windows),
            # including VPN split-DNS that a flattened nameserver list would bypass.
            # TODO: Note, there's currently and issue where microsandbox writes /etc/resolv.conf with mode 0700, so if we make the sandbox image non root dns fails
            network=Network.from_profiles(NetworkProfile.PUBLIC),
        )
        return cls(sandbox=sandbox)

    async def exec(
        self,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        combine_streams: bool = False,
    ) -> SandboxProcess:
        handle = await self._sandbox.exec_stream(
            command,
            list(args or []),
            cwd=cwd,
            env=env,
            stdin=Stdin.pipe(),
            # A guest terminal makes programs line-buffer, so a combined stream arrives line by
            # line. It also merges stderr into stdout, which is why only combined execs get one:
            # the others need the two streams apart and the bytes unaltered.
            tty=combine_streams,
        )
        return _ExecProcess(handle, combine_streams=combine_streams)

    async def close(self) -> None:
        name = await self._sandbox.name
        try:
            await self._sandbox.stop()
        except Exception:
            logging.exception(f"Failed to stop microsandbox {name}")
        try:
            await MsbSandbox.remove(name)
        except Exception:
            logging.exception(f"Failed to remove microsandbox {name}")
