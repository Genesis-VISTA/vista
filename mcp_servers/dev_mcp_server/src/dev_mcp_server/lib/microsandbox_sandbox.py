import asyncio
import logging
import os
import pty
import uuid
from pathlib import Path

from microsandbox import Sandbox as MsbSandbox, Volume as MsbVolume, Network, PullPolicy
from microsandbox._runtime import msb_path as _msb_path

from .sandbox import Sandbox, Volume
from .util import check_output, find_free_port
from .container_sandbox import resolve_container_runtime


REGISTRY_CONTAINER_NAME = "vista-sandbox-registry"


async def _ensure_local_registry(runtime: str) -> str:
    """
    Ensure a local OCI registry container is running and return its URL
    (``localhost:<port>``).

    If the registry container is already running its host-side port is read from the
    container's port bindings so every caller gets a consistent reference regardless of
    which port was chosen when the container was first started.
    """
    # Check whether the registry container is already running and get its bound port.
    port_fmt = "{{(index (index .NetworkSettings.Ports \"5000/tcp\") 0).HostPort}}"
    try:
        out, _ = await check_output(
            runtime, "container", "inspect", f"--format={port_fmt}", REGISTRY_CONTAINER_NAME,
        )
        host_port = out.strip().decode()
        if host_port:
            logging.info("Registry container already running on port %s", host_port)
            return f"localhost:{host_port}"
    except RuntimeError:
        pass

    port = find_free_port()
    logging.info("Starting local OCI registry on :%d...", port)
    try:
        await check_output(
            runtime, "run", "-d", "--rm",
            "-p", f"{port}:5000",
            "-v", f"{REGISTRY_CONTAINER_NAME}-data:/var/lib/registry",
            "--name", REGISTRY_CONTAINER_NAME,
            "registry:2",
        )
    except RuntimeError:
        # Another process may have raced us; try to read the port from the now-running container.
        try:
            out, _ = await check_output(
                runtime, "container", "inspect", f"--format={port_fmt}", REGISTRY_CONTAINER_NAME,
            )
            host_port = out.strip().decode()
            if host_port:
                logging.info("Registry started by concurrent process on port %s", host_port)
                return f"localhost:{host_port}"
        except RuntimeError:
            pass
        raise

    return f"localhost:{port}"


async def _build_and_push_local_image(dockerfile: Path, image_name: str) -> str:
    """
    Build a local OCI image with docker/podman and push it to a local registry so the
    microsandbox runtime can pull it. See https://docs.microsandbox.dev/recipes/local-images.

    Returns the registry-qualified image reference suitable for ``Sandbox.create(image=...)``.
    """
    dockerfile = Path(dockerfile).resolve()
    runtime = resolve_container_runtime()

    registry_url = await _ensure_local_registry(runtime)
    image_tag = f"{registry_url}/{image_name}:latest"

    logging.info("Building sandbox image %s...", image_tag)
    await check_output(
        runtime, "build", "-t", image_tag, "-f", str(dockerfile), str(dockerfile.parent),
    )

    push_args = [runtime, "push"]
    if Path(runtime).name == "podman":
        push_args.append("--tls-verify=false")
    push_args += ["-q", image_tag]
    await check_output(*push_args)

    return image_tag


class MicrosandboxSandbox(Sandbox):
    """
    Sandbox backed by https://github.com/superradcompany/microsandbox MicroVMs.

    Each spawn creates a uniquely-named MicroVM with ``Network.public_only`` and tears it down
    on close. If a dockerfile is given the image is built locally and pushed to a localhost OCI
    registry so the microsandbox runtime can pull it; with a plain image reference no registry
    is started.
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
            image_ref = await _build_and_push_local_image(Path(dockerfile), image or "vista-sandbox")
        elif image:
            image_ref = image
        else:
            raise ValueError("You must specify image or dockerfile")

        # For local (HTTP) registries pull the image explicitly with --insecure so the msb
        # runtime does not need any persistent config changes.
        msb_args = [str(_msb_path()), "pull", image_ref]
        if image_ref.startswith("localhost:"):
            msb_args.append("--insecure")
        logging.info(f"Pulling sandbox image {image_ref}...")
        pull_proc = await asyncio.create_subprocess_exec(
            *msb_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await pull_proc.communicate()
        if pull_proc.returncode != 0:
            raise RuntimeError(
                f"msb pull --insecure {image_ref!r} failed "
                f"(exit {pull_proc.returncode}):\n{stderr.decode()}"
            )
        return image_ref

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
            str(dst): MsbVolume.bind(str(src), readonly=(mode == 'r'))
            for src, dst, mode in volumes
        }

        image_ref = await cls._build(dockerfile=dockerfile, image=image)

        name = f"vista-sandbox-{uuid.uuid4().hex[:12]}"
        logging.info(f"Launching microsandbox {name} from {image_ref}...")
        sandbox = await MsbSandbox.create(
            name,
            image=image_ref,
            pull_policy=PullPolicy.IF_MISSING,
            replace=True,
            cpus=cpus,
            memory=memory,
            shell="/bin/bash",
            volumes=msb_volumes,
            env=dict(env) if env else {},
            network=Network.public_only(),
        )
        return cls(sandbox=sandbox)

    async def exec(
        self, command: str, args: list[str] | None = None,
        env: dict[str, str] | None = None, cwd: str | None = None,
        combine_streams: bool = False,
    ) -> asyncio.subprocess.Process:
        cmd = [str(_msb_path()), "exec", "--quiet", await self._sandbox.name]
        if cwd:
            cmd += ["--workdir", cwd]
        for key, value in (env or {}).items():
            cmd += ["--env", f"{key}={value}"]
        cmd += ["--", command]
        cmd += (args or [])
        # msb allocates a guest PTY (enabling line-by-line streaming) only when its
        # own stdin is a TTY. We give it a pty slave so isatty(stdin) is true while
        # keeping proc.stdout as a normal pipe for async readline.
        master, slave = pty.openpty()
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=slave,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT if combine_streams else asyncio.subprocess.PIPE,
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
