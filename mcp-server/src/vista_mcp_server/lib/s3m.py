"""
S3M API client plus SSH/SCP file transfer helpers.
"""

from __future__ import annotations
from pathlib import Path
import asyncssh
import httpx
from pydantic import BaseModel
from .ssh import ssh_bash_retry, scp_retry


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

    async def submit_job(self, spec: dict) -> dict:
        """ Submit a job and return the Job response. """
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
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.s3m_api}/api/v1/compute/status/{self.resource_id}/{job_id}",
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

