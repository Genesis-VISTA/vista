"""
S3M API client plus SSH/SCP file transfer helpers.
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
    ):
        self.s3m_api = s3m_api.rstrip("/")
        self.s3m_token = s3m_token
        self.resource_id = resource_id
        self.ssh_conn = ssh_conn

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
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://s3m.olcf.ornl.gov/olcf/v1/token/ctls/introspect",
                headers=self._headers(),
                timeout=60,
            )
            resp.raise_for_status()
            token_info = resp.json()

            if token_info.get('project') != 'gen150-vista':
                raise ValueError(f"S3M token must be part of gen150-vista group, current token is {token_info.get('project')}")

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
        return await ssh_bash_retry(self.ssh_conn, command)

    async def upload(self, local: str | Path, remote: str | Path) -> None:
        """ Upload a file or directory to the HPC host. """
        await scp_retry(str(local), (self.ssh_conn, str(remote)))

    async def download(self, remote: str | Path, local: str | Path) -> None:
        """ Download a file or directory from the HPC host via SCP. """
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


_s3m_client: S3mClient | None = None
""" The shared S3M client. Set by submit_job_mcp's lifespan. """


def get_s3m_client() -> S3mClient:
    if _s3m_client is None:
        raise RuntimeError("S3M client not initialized")
    return _s3m_client


async def create_s3m_client() -> S3mClient:
    logging.info(f"Connecting to {settings.hpc_ssh_host[-1]} via SSH for file access...")
    ssh_conn = await get_ssh_conn(settings.hpc_ssh_host, settings.hpc_ssh_user)
    logging.info(f"SSH connection established to {settings.hpc_ssh_host[-1]}")
    return S3mClient(
        s3m_api=settings.s3m_url,
        s3m_token=settings.s3m_token,
        resource_id=settings.s3m_resource,
        ssh_conn=ssh_conn,
    )
