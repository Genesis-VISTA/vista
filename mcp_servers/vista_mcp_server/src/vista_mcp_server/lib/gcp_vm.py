"""The Globus Connect Personal endpoint, and the microVM it runs in.

Globus Transfer is a broker: it copies between two *collections* and never
through the calling process, so this machine has to be a collection, which is
what Globus Connect Personal makes it. Globus ships a scriptable command-line
build for Linux only, so on every other platform that Linux build runs inside a
microsandbox microVM -- the same runtime the agent's code-execution sandbox
already uses, from a separate image, so no host container runtime is required.

Only `globusconnectpersonal` runs inside. Resolving the binary, installing it,
creating the microVM and deciding every argument all happen on the host, so the
native and microVM paths differ by exactly one thing: whether the command is
prefixed with `msb exec`. The confinement string and the mount list therefore
exist once, which matters because both are security boundaries and a second
copy is a second thing to forget.

The module lives here, rather than in `scripts/`, because `scripts/` is not
installed by the packaged artifact; and in Python, rather than in the launcher,
because the same arguments have to serve a development checkout. That it is not
an MCP tool while living in the MCP server is a cosmetic awkwardness, accepted
deliberately.
"""

from __future__ import annotations

import atexit
import enum
import json
import logging
import os
import platform
import subprocess
import sys
import tarfile
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from .dns import host_nameservers

IMAGE = "vista-globus:latest"
"""Pre-loaded by the launcher from the package's payload. Never built here: the
microsandbox backend treats a dockerfile as "build this with docker or podman
first" (see dev_mcp_server's microsandbox_sandbox.py `_build`), which is the one
dependency this whole change exists to remove."""

SANDBOX_NAME = "vista-globus"
GUEST_USER = "ubuntu"
"""Globus Connect Personal refuses to run as root. ubuntu:24.04 carries this
user at uid 1000, and microsandbox's bind identity map rewrites a mount's host
owner to it, so an ordinary host directory is writable from the guest with no
mount options at all."""

MEMORY_MB = 512
"""Enough in every probe. The agent's sandbox asks for 1024, but that one runs
arbitrary generated code; this one runs a single long-lived daemon."""

GCP_TARBALL_URLS = {
    "x86_64": "https://downloads.globus.org/globus-connect-personal/linux/stable/globusconnectpersonal-latest.tgz",
    "aarch64": "https://downloads.globus.org/globus-connect-personal/linux_aarch64/stable/globusconnectpersonal-aarch64-latest.tgz",
}


class EndpointError(RuntimeError):
    """A condition the caller should report to the researcher, not a traceback."""


class State(enum.StrEnum):
    """What `status` found. Distinct causes, because they have distinct remedies."""

    NOT_SET_UP = "not-set-up"
    """No collection has been created yet; first-run setup has not been done."""
    NO_RUNTIME = "no-runtime"
    """The bundled `msb` binary could not be found. A packaging fault."""
    NO_IMAGE = "no-image"
    """`msb` is present but the Globus image was never loaded."""
    NOT_STARTED = "not-started"
    """Everything is in place and nothing is running."""
    RUNNING = "running"
    STOPPED = "stopped"
    """It was started and is no longer running. Reported, never restarted --
    a process failing for a durable reason produces a machine that spins."""


@dataclass(frozen=True)
class Status:
    state: State
    detail: str
    """One line, fit to print at startup or return from a tool."""

    @property
    def running(self) -> bool:
        return self.state is State.RUNNING


def uses_microvm() -> bool:
    """Whether the endpoint needs a Linux guest to run in."""
    return sys.platform != "linux"


def msb_path() -> Path | None:
    """The microsandbox binary bundled inside `dev_mcp_server`'s environment.

    Found rather than imported. `microsandbox` is `dev_mcp_server`'s dependency,
    each MCP server gets its own virtual environment in the packaged layout, and
    declaring it here as well would ship a second copy of a large binary in
    order to call a command-line tool. `scripts/package_launcher.sh` performs
    the same search to load the images.
    """
    override = os.environ.get("VISTA_MSB_PATH")
    if override:
        return Path(override) if os.access(override, os.X_OK) else None

    # Both layouts put this file under an `mcp_servers` directory: the checkout
    # directly, the package by way of the virtual environment installed inside
    # `mcp_servers/vista_mcp_server/.venv`.
    for parent in Path(__file__).resolve().parents:
        if parent.name != "mcp_servers":
            continue
        found = sorted(
            (parent / "dev_mcp_server" / ".venv").glob(
                "**/microsandbox/_bundled/bin/msb"
            )
        )
        if found:
            return found[0]

    from shutil import which

    on_path = which("msb")
    return Path(on_path) if on_path else None


@dataclass
class Endpoint:
    """One Globus Connect Personal endpoint, and everything needed to run it.

    `start`, `stop` and `status` are callable at any time and in any order, so
    moving the trigger -- from launch to a button in the interface -- is a
    change of caller rather than a change of shape.
    """

    data_dir: Path
    hpc_jobs_dir: Path
    _process: subprocess.Popen[bytes] | None = field(
        default=None, repr=False, compare=False
    )

    # ─── the directories, named once ────────────────────────────────────────

    @property
    def volumes_dir(self) -> Path:
        """Per-project outputs: downloads and fetched logs land here."""
        return self.data_dir / "volumes"

    @property
    def config_dir(self) -> Path:
        return self.data_dir / "globusonline"

    @property
    def home_dir(self) -> Path:
        """`HOME` for the endpoint. Under the data directory so it survives when
        `data/` is a mounted volume."""
        return self.data_dir / "gcphome"

    @property
    def client_id_file(self) -> Path:
        return self.config_dir / "lta" / "client-id.txt"

    @property
    def is_set_up(self) -> bool:
        return self.client_id_file.exists()

    def ensure_directories(self) -> None:
        for directory in (
            self.hpc_jobs_dir,
            self.volumes_dir,
            self.config_dir,
            self.home_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    # ─── what the endpoint may touch ────────────────────────────────────────

    @property
    def restrict_paths(self) -> str:
        """Globus Connect Personal's own confinement, enforced inside the guest.

        Job sources are read out of the HPC jobs directory; outputs and logs are
        written into the volumes directory. Nothing else is exposed, whatever
        else happens to be mounted.
        """
        return f"r{self.hpc_jobs_dir}/,rw{self.volumes_dir}/"

    def mounts(self) -> list[Path]:
        """Host paths visible in the guest, mounted at their host paths.

        The paths have to match, because every absolute path the MCP server
        hands a transfer is a host path -- `submit_job_mcp.py` passes `str(f)`
        and `str(local_log_path)` straight through -- so a mount whose
        destination differs from its source resolves to nothing inside.

        The repository root is deliberately absent. The container this replaces
        mounted it only because the launch script re-executed itself inside;
        nothing of VISTA's runs in the guest now, and leaving the repository out
        is what stops the endpoint being able to read `.env` and both deployment
        refresh tokens.

        Both are mounted read-write, and no `:ro` is passed. microsandbox 0.5.7
        mis-parses `source:destination:options`: it appends the option's last
        character to the destination and mounts read-write regardless, so
        `-v /x/hpc_jobs:/x/hpc_jobs:ro` arrives in the guest as `/x/hpc_jobso`
        and every upload would find nothing there. Confinement is
        `-restrict-paths`, which is also all the container relied on -- it
        mounted every directory read-write.
        """
        mounts = [self.data_dir]
        # Normally siblings. A jobs directory configured inside the data
        # directory is already mounted, and mounting it again would be a mount
        # over a mount.
        if not self.hpc_jobs_dir.is_relative_to(self.data_dir):
            mounts.append(self.hpc_jobs_dir)
        return mounts

    # ─── the commands, built where they can be asserted on ──────────────────

    def create_argv(self, msb: Path, nameservers: Sequence[str]) -> list[str]:
        """Arguments that create the microVM.

        No mount options are passed. microsandbox's bind identity map already
        maps the host owner to the guest user, so widening host permissions is
        unnecessary. In particular `stat-virt=off` must never appear: it passes
        real host ownership through and denies every write, and its name reads
        like a harmless simplification.
        """
        argv = [
            str(msb),
            "create",
            IMAGE,
            "--name",
            SANDBOX_NAME,
            "--user",
            GUEST_USER,
            "--memory",
            f"{MEMORY_MB}M",
            # The image is loaded from the package's payload; a pull would need
            # a registry this artifact deliberately does not depend on.
            "--pull",
            "never",
            "--replace",
        ]
        for nameserver in nameservers:
            argv += ["--dns-nameserver", nameserver]
        for path in self.mounts():
            argv += ["--volume", f"{path}:{path}"]
        return argv

    def exec_argv(self, msb: Path, command: Sequence[str], *, tty: bool) -> list[str]:
        argv = [str(msb), "exec", "--quiet", SANDBOX_NAME, "--user", GUEST_USER]
        if tty:
            argv.append("--tty")
        return [*argv, "--", *command]

    def start_command(self, gcp: Path) -> list[str]:
        return [
            str(gcp),
            "-dir",
            str(self.config_dir),
            "-start",
            "-restrict-paths",
            self.restrict_paths,
        ]

    def setup_command(self, gcp: Path, setup_key: str | None) -> list[str]:
        command = [str(gcp), "-dir", str(self.config_dir), "-setup"]
        # With no key, Globus Connect Personal prints a URL and waits for what
        # the browser login returns -- which needs a terminal, not a pipe.
        return [*command, setup_key] if setup_key else [*command, "--no-gui"]

    # ─── running it ─────────────────────────────────────────────────────────

    def status(self) -> Status:
        if not self.is_set_up:
            return Status(
                State.NOT_SET_UP,
                "Globus file transfer is not set up; no collection has been created yet",
            )

        if not uses_microvm():
            return self._native_status()

        msb = msb_path()
        if msb is None:
            return Status(
                State.NO_RUNTIME,
                "the bundled microsandbox binary could not be found, so the "
                "Globus endpoint cannot run",
            )
        if not _succeeds([str(msb), "image", "inspect", "--format=json", IMAGE]):
            return Status(
                State.NO_IMAGE,
                f"the Globus image {IMAGE} has not been loaded",
            )

        sandbox = _sandbox_status(msb)
        if sandbox is None:
            return Status(State.NOT_STARTED, "the Globus endpoint is not running")
        if sandbox.lower() == "running":
            return Status(State.RUNNING, "the Globus endpoint is running")
        return Status(
            State.STOPPED,
            f"the Globus endpoint's microVM is not running (microsandbox reports "
            f"{sandbox.lower()}); file transfer is unavailable until VISTA is restarted",
        )

    def _native_status(self) -> Status:
        if self._process is None:
            return Status(
                State.NOT_STARTED, "the Globus endpoint was not started by this process"
            )
        if self._process.poll() is None:
            return Status(State.RUNNING, "the Globus endpoint is running")
        return Status(
            State.STOPPED,
            f"the Globus endpoint exited with status {self._process.returncode}; "
            "file transfer is unavailable until VISTA is restarted",
        )

    def start(self) -> subprocess.Popen[bytes]:
        """Start the endpoint and return the process holding it.

        This replaces the `os.execv` the launch script used to perform. Holding
        the process rather than becoming it is what ties the microVM's lifetime
        to the caller's: `stop` runs at exit, so a normal termination takes the
        microVM with it.
        """
        if not self.is_set_up:
            raise EndpointError(
                "Globus Connect Personal is not set up; run setup before starting the endpoint"
            )

        self.ensure_directories()
        gcp = self.resolve_gcp()
        command = self.start_command(gcp)

        if uses_microvm():
            msb = self.create_vm()
            command = self.exec_argv(msb, command, tty=False)

        logging.info("Starting the Globus endpoint: %s", " ".join(command))
        # stdin is closed rather than inherited: `msb exec` blocks on an open
        # stdin, and nothing here reads any.
        self._process = subprocess.Popen(command, stdin=subprocess.DEVNULL)
        atexit.register(self.stop)
        return self._process

    def create_vm(self) -> Path:
        """Create the microVM, replacing any earlier one. Returns the `msb` path."""
        msb = msb_path()
        if msb is None:
            raise EndpointError(
                "the bundled microsandbox binary could not be found; "
                "set VISTA_MSB_PATH to its location"
            )
        if not _succeeds([str(msb), "image", "inspect", "--format=json", IMAGE]):
            raise EndpointError(
                f"the Globus image {IMAGE} has not been loaded; "
                "it ships in the package's payload and is imported on first run"
            )

        argv = self.create_argv(msb, host_nameservers())
        logging.info("Creating the Globus microVM: %s", " ".join(argv))
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True)
        if result.returncode != 0:
            raise EndpointError(
                f"could not create the Globus microVM: {result.stderr.decode().strip()}"
            )
        return msb

    def run_in_guest(
        self, command: Sequence[str], *, tty: bool = False
    ) -> subprocess.CompletedProcess[bytes]:
        """Run one command to completion, in the guest or natively.

        Non-interactive invocations get a closed stdin. `msb exec` blocks
        indefinitely on an open one, which looks exactly like a hung endpoint.
        """
        if uses_microvm():
            msb = msb_path()
            if msb is None:
                raise EndpointError(
                    "the bundled microsandbox binary could not be found"
                )
            command = self.exec_argv(msb, command, tty=tty)
        return subprocess.run(list(command), stdin=None if tty else subprocess.DEVNULL)

    def stop(self) -> None:
        """Stop the endpoint and remove its microVM. Idempotent."""
        process, self._process = self._process, None
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

        if not uses_microvm():
            return
        msb = msb_path()
        if msb is None:
            return
        for verb in ("stop", "remove"):
            # A microVM that was never created makes both of these fail, which
            # is not worth reporting: the postcondition is already met.
            subprocess.run(
                [str(msb), verb, SANDBOX_NAME],
                stdin=subprocess.DEVNULL,
                capture_output=True,
            )

    # ─── the binary itself ──────────────────────────────────────────────────

    def resolve_gcp(self) -> Path:
        """Locate Globus Connect Personal, installing it if this is the first run.

        Installed under the data directory so it persists when `data/` is a
        mounted volume, and so the host and the guest see it at one path.
        """
        if not uses_microvm():
            # Only meaningful natively. Under the microVM the host's own
            # `globusconnectpersonal` would be a Darwin executable that cannot
            # run in a Linux guest.
            from shutil import which

            on_path = which("globusconnectpersonal")
            if on_path:
                return Path(on_path)

        install_dir = self.data_dir / "globusconnectpersonal"
        gcp = install_dir / "globusconnectpersonal"
        if gcp.exists():
            return gcp

        # Always the Linux build, whether it runs natively or in the guest.
        arch = platform.machine()
        arch = {"arm64": "aarch64", "amd64": "x86_64"}.get(arch, arch)
        url = GCP_TARBALL_URLS.get(arch)
        if url is None:
            raise EndpointError(
                f"no Globus Connect Personal build for architecture {arch!r}"
            )

        logging.info("Installing Globus Connect Personal to %s", install_dir)
        install_dir.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url) as response:
            with tarfile.open(fileobj=response, mode="r|gz") as tar:

                def strip_top_level(
                    member: tarfile.TarInfo, path: str
                ) -> tarfile.TarInfo | None:
                    """Drop the leading 'globusconnectpersonal-x.y.z/' component."""
                    _, _, member.name = member.name.partition("/")
                    return member if member.name else None

                tar.extractall(install_dir, filter=strip_top_level)
        return gcp


def _succeeds(argv: Sequence[str]) -> bool:
    return (
        subprocess.run(
            list(argv), stdin=subprocess.DEVNULL, capture_output=True
        ).returncode
        == 0
    )


def _sandbox_status(msb: Path) -> str | None:
    """The microVM's reported status, or None when no such microVM exists."""
    result = subprocess.run(
        [str(msb), "status", "--format=json", SANDBOX_NAME],
        stdin=subprocess.DEVNULL,
        capture_output=True,
    )
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout).get("status")
    except json.JSONDecodeError, AttributeError:
        return None
