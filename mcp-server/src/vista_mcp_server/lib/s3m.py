"""
S3M API client plus SSH/SCP file transfer helpers.
"""

from __future__ import annotations
from pathlib import Path
from typing import Literal
import asyncssh
import httpx
import logging
from pydantic import BaseModel
from .ssh import ssh_bash_retry, scp_retry, get_ssh_conn
from ..config import settings


S3mCluster = Literal["odo", "frontier"]
""" Clusters served by the S3M API. """


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
    ):
        self.s3m_api = s3m_api.rstrip("/")
        self.s3m_token = s3m_token
        self.resource_id = resource_id
        self.ssh_conn = ssh_conn
        self._token_validated = False

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.s3m_token}",
            "Content-Type": "application/json",
        }
    
    async def _validate_token(self):
        """ Verify the token is valid and in the right group """
        # TODO This is a temporary check because of the current ssh/scp workarounds, we have to make
        # sure the token matches the group of our ssh session. We can remove this once that's fixed.
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
                if token_project != 'gen150-vista':
                    raise ValueError(f"S3M token must be part of gen150-vista group, current token is {token_project}")
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


_ssh_conns: dict[S3mCluster, asyncssh.SSHClientConnection] = {}
"""
Per-cluster SSH connections used by `get_s3m_client`.

Initialized by submit_job_mcp's lifespan. This is a temporary hack to work around the S3M api's
lack of file operation support. We should remove it as soon as file support is available and
greatly simplify the MCP server launch sequence.

Key "odo" is always opened. Key "frontier" is opened only when `settings.frontier_ssh_host` is set.
"""


def _ssh_host_for(cluster: S3mCluster) -> list[str]:
    if cluster == "odo":
        return list(settings.hpc_ssh_host)
    return list(settings.frontier_ssh_host)


async def init_s3m_ssh_conn():
    """ Open the SSH conn for Odo, and (if configured) for Frontier. """
    if "odo" not in _ssh_conns:
        host = _ssh_host_for("odo")
        logging.info(f"Connecting to {host[-1]} via SSH for Odo file access...")
        _ssh_conns["odo"] = await get_ssh_conn(host, settings.hpc_ssh_user)
        logging.info(f"SSH connection established to {host[-1]} (odo)")
    if settings.frontier_ssh_host and "frontier" not in _ssh_conns:
        host = _ssh_host_for("frontier")
        logging.info(f"Connecting to {host[-1]} via SSH for Frontier file access...")
        _ssh_conns["frontier"] = await get_ssh_conn(host, settings.hpc_ssh_user)
        logging.info(f"SSH connection established to {host[-1]} (frontier)")


def close_s3m_ssh_conns():
    """ Close any open per-cluster SSH conns. Safe to call when none are open. """
    for cluster, conn in list(_ssh_conns.items()):
        conn.close()
        del _ssh_conns[cluster]


def _resource_id_for(cluster: S3mCluster) -> str:
    return settings.s3m_resource if cluster == "odo" else settings.s3m_frontier_resource


def get_s3m_client(*, s3m_token: str, cluster: S3mCluster = "odo") -> S3mClient:
    conn = _ssh_conns.get(cluster)
    if conn is None:
        if cluster == "frontier":
            raise RuntimeError(
                "Frontier SSH connection not initialized. Set VISTA_MCP_FRONTIER_SSH_HOST "
                "and restart the MCP server to enable cluster=\"frontier\" routing."
            )
        raise RuntimeError("S3M SSH connection not initialized; call init_s3m_ssh_conn() first")
    return S3mClient(
        s3m_api=settings.s3m_url,
        s3m_token=s3m_token,
        resource_id=_resource_id_for(cluster),
        ssh_conn=conn,
    )
