import asyncio
import json
import logging
import os
import pty
import tarfile
import tempfile
import uuid
from pathlib import Path, PurePosixPath

from microsandbox import Sandbox as MsbSandbox, Volume as MsbVolume, Network, PullPolicy
from microsandbox.types import DnsConfig  # not re-exported from package root
from microsandbox._runtime import msb_path as _msb_path

from .sandbox import Sandbox, Volume
from .util import check_output, parse_output
from .container_sandbox import resolve_container_runtime


async def _msb_image_digest(image: str) -> str | None:
    """
    The config digest microsandbox has stored for `image`, or None if it holds no
    such image. Used both to decide whether seeding is needed and to compare a
    freshly built image against the stored one.
    """
    # TODO: in microsandbox 0.5.5+, `Image.inspect` does this through the SDK.
    inspect = await parse_output(
        str(_msb_path()), "image", "inspect", "--format=json", image
    )
    if not inspect:
        return None
    # podman doesn't prefix sha256:, so compare the bare hex either way.
    return inspect["config"]["digest"].split(":")[-1]


def _tar_digest(oci_image_tar: Path) -> str:
    """
    The config digest of the image inside `oci_image_tar`, read from the archive
    itself.

    The tar is the only input: an image archive names its config blob by that
    blob's own sha256, so the digest is readable straight out of `manifest.json`
    without unpacking a layer and without docker or podman — which is the whole
    point of this path. That is the same value `image inspect` reports as the
    image id and microsandbox stores as `config.digest`, so it is directly
    comparable with `_msb_image_digest`.

    Only tar headers are walked, never layer data, so this stays cheap on a
    multi-gigabyte archive.

    Raises on an archive it cannot read. That is fatal either way — the tar *is*
    the sandbox image, so degrading to "load only when it is absent" would only
    trade this error for a worse one out of `msb load`, or silently keep running
    whatever stale image the store already holds.
    """
    with tarfile.open(oci_image_tar) as archive:
        try:
            manifest = archive.extractfile("manifest.json")
        except KeyError:
            manifest = None
        if manifest is None:
            raise ValueError(
                f"{oci_image_tar} has no readable manifest.json — not an image archive"
            )
        config = json.loads(manifest.read())[0]["Config"]
    # "<hex>.json" from docker's classic archive, "blobs/sha256/<hex>" from an
    # OCI-layout one. Both name the blob by its digest; take the bare hex.
    return PurePosixPath(config).name.removesuffix(".json")


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
        oci_image_tar: Path | str | None = None,
    ):
        if oci_image_tar:
            if dockerfile:
                raise ValueError("Can't specify both dockerfile and oci_image_tar")
            image = image or "vista-sandbox:latest"
            oci_image_tar = Path(oci_image_tar).resolve()
            # Presence alone is not enough: the tag is fixed and microsandbox's
            # store (MSB_HOME) outlives the container on the beta host, so a
            # rebuilt tar shipped in a new server image would never be picked up.
            # The comparison reads the archive rather than shelling out, so this
            # path still needs no container runtime. `to_thread` because that is
            # blocking file I/O on what may be a multi-gigabyte tar.
            stored = await _msb_image_digest(image)
            if stored != await asyncio.to_thread(_tar_digest, oci_image_tar):
                logging.info(f"Loading sandbox image {image} from {oci_image_tar}...")
                await check_output(
                    str(_msb_path()), "load", "-i", str(oci_image_tar), "-t", image
                )
        elif dockerfile:
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
                    await check_output(
                        str(_msb_path()), "load", "-i", archive, "-t", image
                    )
        elif image:
            if not await _msb_image_digest(image):
                logging.info(f"Pulling sandbox image {image}...")
                await check_output(str(_msb_path()), "pull", image)
        else:
            raise ValueError("You must specify image, dockerfile or oci_image_tar")

        return image

    @classmethod
    async def build(
        cls,
        dockerfile: Path | str | None = None,
        image: str | None = None,
        oci_image_tar: Path | str | None = None,
    ) -> None:
        await cls._build(
            dockerfile=dockerfile, image=image, oci_image_tar=oci_image_tar
        )

    @classmethod
    async def spawn(
        cls,
        volumes: list[Volume] | None = None,
        env: dict[str, str] | None = None,
        image: str | None = None,
        dockerfile: Path | str | None = None,
        oci_image_tar: Path | str | None = None,
        cpus: int = 1,
        memory: int = 1024,
    ) -> "MicrosandboxSandbox":
        volumes = list(volumes or [])

        msb_volumes = {
            str(dst): MsbVolume.bind(str(src), readonly=(mode == "r"))
            for src, dst, mode in volumes
        }

        image = await cls._build(
            dockerfile=dockerfile, image=image, oci_image_tar=oci_image_tar
        )

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
            await self._sandbox.stop()
        except Exception:
            logging.exception(f"Failed to stop microsandbox {name}")
        try:
            await MsbSandbox.remove(name)
        except Exception:
            logging.exception(f"Failed to remove microsandbox {name}")
