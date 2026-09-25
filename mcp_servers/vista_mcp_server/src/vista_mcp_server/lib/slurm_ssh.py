"""
Slurm over SSH, for clusters with no IRI service (Lux).

Every operation takes a live `asyncssh.SSHClientConnection`, normally the one
`lib.ssh.get_ssh_conn_mcp_elicitation` caches per chat session, so a researcher
types their passcodes once and every later submit / status / fetch reuses it.

Commands run under a non-login `bash -c`, so a login banner or module chatter
cannot end up in output that gets parsed (a job id, a state). Anything that
needs the login environment (the per-job setup script) asks for it explicitly.

File operations use SFTP on the same connection. One SFTP channel is opened per
operation and closed again: some OLCF login nodes cap sessions per connection,
and a long-lived SFTP channel would make every command after it fail to open.
"""
from __future__ import annotations

import logging
import re
import shlex
import stat as stat_mod
from dataclasses import dataclass
from pathlib import Path

import asyncssh
import tenacity


_retry_channel_open = tenacity.retry(
    stop=tenacity.stop_after_attempt(4),
    wait=tenacity.wait_random_exponential(multiplier=0.5, max=10),
    retry=tenacity.retry_if_exception_type(asyncssh.ChannelOpenError),
    reraise=True,
)
""" Same policy as `lib.ssh.ssh_bash_retry`: retry when the login node refuses a new channel. """


class SlurmSshError(RuntimeError):
    """ A remote command failed; the message carries its output. """


@dataclass
class CommandResult:
    exit_status: int
    stdout: str
    stderr: str

    @property
    def output(self) -> str:
        return "\n".join(s for s in (self.stdout.strip(), self.stderr.strip()) if s)


@_retry_channel_open
async def run(
    conn: asyncssh.SSHClientConnection, command: str, *, input: str | None = None,
    login_shell: bool = False,
) -> CommandResult:
    """ Run `command` under bash on the remote host, returning exit status and both streams. """
    shell = "bash -lc" if login_shell else "bash -c"
    result = await conn.run(f"{shell} {shlex.quote(command)}", input=input, check=False)
    return CommandResult(
        exit_status=result.exit_status if result.exit_status is not None else -1,
        stdout=str(result.stdout or ""),
        stderr=str(result.stderr or ""),
    )


async def run_checked(conn: asyncssh.SSHClientConnection, command: str, *, what: str, **kwargs) -> str:
    """ `run`, raising `SlurmSshError` (with the command's output) on a non-zero exit. """
    result = await run(conn, command, **kwargs)
    if result.exit_status != 0:
        raise SlurmSshError(f"{what} failed (exit {result.exit_status}):\n{result.output}")
    return result.stdout


# ------------------------------------------------------------------ submission

def render_batch_script(
    *,
    job_name: str,
    account: str,
    node_count: int,
    duration_s: int,
    stdout_path: str,
    stderr_path: str,
    workdir: str,
    body: str,
    queue: str | None = None,
    constraint: str | None = None,
    exclusive: bool = False,
    ntasks_per_node: int | None = None,
    gpus_per_node: int | None = None,
) -> str:
    """
    Build the batch script handed to `sbatch` on stdin.

    Resources come only from the `#SBATCH` header written here. Slurm stops
    reading directives at the first command, so any `#SBATCH` lines left in the
    job's own script (which follows in `body`) are inert, as on the IRI path.
    """
    h, rem = divmod(int(duration_s), 3600)
    m, s = divmod(rem, 60)
    directives = [
        f"-J {job_name}",
        f"-A {account}",
        f"-N {node_count}",
        f"-t {h}:{m:02d}:{s:02d}",
        f"-o {stdout_path}",
        f"-e {stderr_path}",
        f"--chdir={workdir}",
    ]
    if queue:
        directives.append(f"-q {queue}")
    if constraint:
        directives.append(f"-C {constraint}")
    if exclusive:
        directives.append("--exclusive")
    # Launchers that srun with --export=ALL (DeepSpeed's slurm runner) pass the
    # batch env to every rank, so SLURM_NTASKS there must already be the full
    # task count -- which only an allocation that states tasks per node sets.
    if ntasks_per_node:
        directives.append(f"--ntasks-per-node={ntasks_per_node}")
    if gpus_per_node:
        directives.append(f"--gpus-per-node={gpus_per_node}")
    header = "\n".join(f"#SBATCH {d}" for d in directives)
    return f"#!/bin/bash -l\n{header}\n\n{body}"


_JOB_ID_RE = re.compile(r"^(\d+)(?:;\S+)?$")


async def sbatch(conn: asyncssh.SSHClientConnection, script: str) -> str:
    """ Submit `script` via `sbatch --parsable` on stdin; return the Slurm job id. """
    result = await run(conn, "sbatch --parsable", input=script)
    # --parsable prints "<jobid>" or "<jobid>;<cluster>" on its own line; warnings
    # can precede it on stdout, so take the last line that matches.
    for line in reversed(result.stdout.strip().splitlines()):
        match = _JOB_ID_RE.match(line.strip())
        if match and result.exit_status == 0:
            return match.group(1)
    raise SlurmSshError(f"sbatch failed (exit {result.exit_status}):\n{result.output}")


async def scancel(conn: asyncssh.SSHClientConnection, job_id: str) -> None:
    await run_checked(conn, f"scancel {shlex.quote(job_id)}", what=f"scancel {job_id}")


# ------------------------------------------------------------------ status

_RUNNING = {"RUNNING", "CONFIGURING", "COMPLETING", "STAGE_OUT", "SIGNALING", "RESIZING"}
_QUEUED = {"PENDING", "REQUEUED", "REQUEUE_HOLD", "REQUEUE_FED", "SUSPENDED", "STOPPED"}


def normalize_state(slurm_state: str) -> str:
    """
    Map a Slurm state onto the IRI/PSI-J vocabulary the other clusters report
    (NEW/QUEUED/PENDING/ACTIVE/COMPLETED/FAILED/CANCELED), so skills can use one
    set of state names for every cluster. The raw Slurm state is reported too.
    """
    base = slurm_state.strip().split()[0].rstrip("+").upper() if slurm_state.strip() else ""
    if base in _RUNNING:
        return "ACTIVE"
    if base in _QUEUED:
        return "PENDING"
    if base == "COMPLETED":
        return "COMPLETED"
    if base == "CANCELLED":
        return "CANCELED"
    if not base:
        return "UNKNOWN"
    # FAILED, TIMEOUT, NODE_FAIL, OUT_OF_MEMORY, BOOT_FAIL, DEADLINE, PREEMPTED, ...
    return "FAILED"


@dataclass
class JobState:
    state: str
    slurm_state: str
    exit_code: str | None = None
    reason: str | None = None


async def job_state(conn: asyncssh.SSHClientConnection, job_id: str) -> JobState:
    """
    `squeue` while the job is in the queue (it knows the pending reason), then
    `sacct` once it has left (squeue forgets finished jobs within minutes).
    """
    q = shlex.quote(job_id)
    queued = await run(conn, f"squeue -h -j {q} -o '%T|%r'")
    line = queued.stdout.strip().splitlines()[0] if queued.exit_status == 0 and queued.stdout.strip() else ""
    if line:
        slurm_state, _, reason = line.partition("|")
        reason = reason.strip()
        return JobState(
            state=normalize_state(slurm_state), slurm_state=slurm_state.strip(),
            reason=reason if reason and reason != "None" else None,
        )

    acct = await run(conn, f"sacct -n -X -P -j {q} -o State,ExitCode")
    line = acct.stdout.strip().splitlines()[0] if acct.stdout.strip() else ""
    if line:
        slurm_state, _, exit_code = line.partition("|")
        return JobState(
            state=normalize_state(slurm_state), slurm_state=slurm_state.strip(),
            exit_code=exit_code.strip() or None,
        )
    return JobState(state="UNKNOWN", slurm_state="UNKNOWN")


# ------------------------------------------------------------------ files

class RemoteFileNotFound(FileNotFoundError):
    pass


@_retry_channel_open
async def _sftp(conn: asyncssh.SSHClientConnection) -> asyncssh.SFTPClient:
    return await conn.start_sftp_client()


async def makedirs(conn: asyncssh.SSHClientConnection, path: str) -> None:
    """ `mkdir -p` via the shell, so new dirs take the umask/setgid a login shell would give them. """
    await run_checked(conn, f"mkdir -p {shlex.quote(path)}", what=f"mkdir -p {path}")


async def list_file_sizes(conn: asyncssh.SSHClientConnection, path: str) -> dict[str, int]:
    """ `{name: size}` for the regular files directly in `path`; empty if it doesn't exist. """
    async with await _sftp(conn) as sftp:
        try:
            entries = await sftp.readdir(path)
        except asyncssh.SFTPNoSuchFile:
            return {}
    return {
        e.filename: e.attrs.size or 0
        for e in entries
        if e.attrs.type == asyncssh.FILEXFER_TYPE_REGULAR
        or (e.attrs.permissions is not None and stat_mod.S_ISREG(e.attrs.permissions))
    }


async def upload_files(conn: asyncssh.SSHClientConnection, files: list[Path], remote_dir: str) -> None:
    async with await _sftp(conn) as sftp:
        for f in files:
            await sftp.put(str(f), f"{remote_dir}/{f.name}")


async def download_file(conn: asyncssh.SSHClientConnection, remote_path: str, local_path: Path) -> None:
    local_path.parent.mkdir(parents=True, exist_ok=True)
    async with await _sftp(conn) as sftp:
        try:
            await sftp.get(remote_path, str(local_path))
        except asyncssh.SFTPNoSuchFile as e:
            raise RemoteFileNotFound(remote_path) from e


async def list_files(
    conn: asyncssh.SSHClientConnection, root: str, *, limit: int = 20,
    exclude: tuple[str, ...] = (".venv", "__pycache__"),
) -> list[str]:
    """ Up to `limit` file paths under `root`, relative to it, sorted. Empty if `root` is missing. """
    prune = " -o ".join(f"-name {shlex.quote(x)}" for x in exclude)
    cmd = (
        f"cd {shlex.quote(root)} 2>/dev/null || exit 0; "
        f"find . \\( {prune} \\) -prune -o -type f -print | sed 's|^\\./||' | sort | head -n {int(limit)}"
    )
    out = await run_checked(conn, cmd, what=f"list {root}")
    return [line for line in out.splitlines() if line]


class SftpLogReader:
    """
    The two calls `submit_job_mcp._tail_remote_log` makes on a Globus client --
    `stat` for the size, `read_range` for the new bytes -- served over SFTP, so
    Lux logs are tailed incrementally exactly like Odo/Frontier ones.
    `collection_id` is accepted and ignored: there is only one filesystem here.
    """

    def __init__(self, conn: asyncssh.SSHClientConnection):
        self._conn = conn

    async def stat(self, *, collection_id: str | None = None, remote_path: str) -> int:
        async with await _sftp(self._conn) as sftp:
            try:
                attrs = await sftp.stat(remote_path)
            except asyncssh.SFTPNoSuchFile as e:
                raise RemoteFileNotFound(remote_path) from e
        return attrs.size or 0

    async def read_range(
        self, *, collection_id: str | None = None, remote_path: str, start: int, end: int,
    ) -> bytes:
        async with await _sftp(self._conn) as sftp:
            try:
                async with sftp.open(remote_path, "rb", encoding=None) as f:
                    data = await f.read(end - start + 1, start)
            except asyncssh.SFTPNoSuchFile as e:
                raise RemoteFileNotFound(remote_path) from e
        logging.debug(f"sftp read {len(data)} byte(s) of {remote_path} at {start}")
        return data
