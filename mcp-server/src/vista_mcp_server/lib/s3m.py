"""
S3M API client plus SSH/SCP file transfer helpers.

Used for Odo (OLCF) job submission. Frontier and Perlmutter both go through the
IRI path in `lib/iri.py` instead — Frontier's S3M token is IRI-scoped and has no
permission to hit the S3M /compute endpoints.

Frontier still needs SSH/SCP for filesystem operations (storage discovery on its
IRI token 401s, so we fall back to SCP — same workaround as Odo). The lifespan
in submit_job_mcp.py calls `init_s3m_ssh_conn()` which opens the Odo conn
unconditionally and the Frontier conn iff `settings.frontier_ssh_host` is set.
The Frontier IRI dispatch reaches for `get_frontier_ssh_conn()` to do file ops.
"""

from __future__ import annotations
from pathlib import Path
import asyncssh
import httpx
import logging
from pydantic import BaseModel
from .ssh import ssh_bash_retry, scp_retry, get_ssh_conn
from ..config import settings


class S3mClient:
    """
    Client for submitting jobs via the S3M API and transferring files over SSH/SCP.
    """

    def __init__(
        self, *,
        s3m_api: str,
        s3m_token: str,
        resource_id: str,
        ssh_conn: asyncssh.SSHClientConnection,
        expected_project: str,
    ):
        self.s3m_api = s3m_api.rstrip("/")
        self.s3m_token = s3m_token
        self.resource_id = resource_id
        self.ssh_conn = ssh_conn
        self.expected_project = expected_project
        self._token_validated = False

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.s3m_token}",
            "Content-Type": "application/json",
        }

    async def _validate_token(self):
        """ Verify the token is valid and in the right OLCF project. """
        # TODO This is a temporary check because of the current ssh/scp workarounds: we have to
        # make sure the token's project matches the project of our ssh session so file uploads
        # land in the right group dir. We can remove this once S3M /file endpoints land.
        # This is also hard coded to OLCF resources, each API can do tokens differently.
        if not self._token_validated:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    "https://s3m.olcf.ornl.gov/olcf/v1/token/ctls/introspect",
                    headers=self._headers(),
                    timeout=60,
                )
                resp.raise_for_status()
                token_info = resp.json()

                token_project = token_info.get("token", {}).get('project')
                if token_project != self.expected_project:
                    raise ValueError(
                        f"S3M token must be part of {self.expected_project!r} project, "
                        f"current token is in {token_project!r}"
                    )
                self._token_validated = True

    async def submit_job(self, spec: dict) -> dict:
        """ Submit a job and return the Job response. """
        await self._validate_token()
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.s3m_api}/api/v1/compute/job/{self.resource_id}",
                headers=self._headers(),
                json=spec,
                timeout=60,
            )
            resp.raise_for_status()
            return resp.json()

    async def get_job_status(self, job_id: str) -> dict:
        """ Get job status. """
        await self._validate_token()
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                # TODO: Odo seems to be ignoring include_spec=true
                f"{self.s3m_api}/api/v1/compute/status/{self.resource_id}/{job_id}?historical=true&include_spec=true",
                headers=self._headers(),
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()


    # The Odo S3M deployment does not yet implement the /file endpoints, so logs and
    # output files are transferred over an existing SSH connection via SCP.
    # TODO: Drop the SSH/SCP side when S3M file endpoints for Odo are available.

    async def bash(self, command: str | list[str]) -> str:
        """ Run a bash command on the HPC host. """
        await self._validate_token()
        return await ssh_bash_retry(self.ssh_conn, command)

    async def upload(self, local: str | Path, remote: str | Path) -> None:
        """ Upload a file or directory to the HPC host. """
        await self._validate_token()
        await scp_retry(str(local), (self.ssh_conn, str(remote)))

    async def download(self, remote: str | Path, local: str | Path) -> None:
        """ Download a file or directory from the HPC host via SCP. """
        await self._validate_token()
        await scp_retry((self.ssh_conn, str(remote)), str(local))


class S3mResourceSpec(BaseModel):
    node_count: int | None = 1
    process_count: int | None = None
    processes_per_node: int | None = None
    cpu_cores_per_process: int | None = None
    gpu_cores_per_process: int | None = None
    exclusive_node_use: bool = True
    memory: int | None = None
    """ Bytes """


class S3mDefaults(BaseModel):
    """
    Per-job S3M resource and attribute defaults, loaded from the "odo" section of
    `<job>/cluster_defaults.json`.
    """
    duration: int = 120
    """ Seconds """
    resources: S3mResourceSpec = S3mResourceSpec()


_ssh_conns: dict[str, asyncssh.SSHClientConnection] = {}
"""
SSH connections for file ops, keyed by cluster slug ("odo" / "frontier").

Initialized by submit_job_mcp's lifespan. This is a temporary hack to work around the lack of
filesystem APIs (Odo's S3M doesn't have /file endpoints; Frontier's IRI token 401s on storage
discovery). Remove these once both backends gain filesystem support.

"odo" is always opened. "frontier" is opened only when `settings.frontier_ssh_host` is set.
"""


async def init_s3m_ssh_conn():
    """ Open the SSH conn for Odo, and (if configured) for Frontier. """
    if "odo" not in _ssh_conns:
        host = settings.hpc_ssh_host
        user = settings.hpc_ssh_user
        logging.info(f"Connecting to {user}@{host[-1]} via SSH for Odo file access...")
        _ssh_conns["odo"] = await get_ssh_conn(host, user)
        logging.info(f"SSH connection established to {host[-1]} (odo)")
    if settings.frontier_ssh_host and "frontier" not in _ssh_conns:
        host = list(settings.frontier_ssh_host)
        user = settings.frontier_ssh_user or settings.hpc_ssh_user
        logging.info(f"Connecting to {user}@{host[-1]} via SSH for Frontier file access...")
        _ssh_conns["frontier"] = await get_ssh_conn(host, user)
        logging.info(f"SSH connection established to {host[-1]} (frontier)")


def close_s3m_ssh_conns():
    """ Close all open SSH conns. Safe to call when none are open. """
    for slug, conn in list(_ssh_conns.items()):
        conn.close()
        del _ssh_conns[slug]


def get_s3m_client(*, s3m_token: str) -> S3mClient:
    """ Odo-only S3M client. Frontier uses the IRI path in `lib/iri.py` for compute. """
    conn = _ssh_conns.get("odo")
    if conn is None:
        raise RuntimeError("S3M SSH connection not initialized; call init_s3m_ssh_conn() first")
    return S3mClient(
        s3m_api=settings.s3m_url,
        s3m_token=s3m_token,
        resource_id=settings.s3m_resource,
        ssh_conn=conn,
        expected_project=settings.hpc_account,
    )


def get_frontier_ssh_conn() -> asyncssh.SSHClientConnection:
    """
    SSH connection for Frontier file ops, used by the Frontier IRI dispatch in
    `submit_job_mcp.py`. Raises if the conn wasn't initialized (i.e. the deployment
    didn't set VISTA_MCP_FRONTIER_SSH_HOST).
    """
    conn = _ssh_conns.get("frontier")
    if conn is None:
        raise RuntimeError(
            "Frontier SSH connection not initialized. Set VISTA_MCP_FRONTIER_SSH_HOST "
            "and restart the MCP server to enable cluster=\"frontier\" routing."
        )
    return conn
