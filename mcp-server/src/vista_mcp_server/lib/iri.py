"""
NERSC IRI client for job submission and filesystem access via the amscrot SDK.

This module mirrors the shape of ``lib/s3m.py`` so ``submit_job_mcp.py`` can
dispatch between Odo (S3M) and Perlmutter (IRI) without duplicating logic.

amscrot is an *optional* dependency (the ``nersc`` extras group). Importing
this module fails clearly with the install hint when amscrot is missing.
"""

from __future__ import annotations
import asyncio
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ..config import settings


try:
    from amscrot.client.job import Job, JobServiceType, JobSpec, JobType
    from amscrot.serviceclient import ServiceClient
    from amscrot.util.constants import Constants
    AMSCROT_AVAILABLE = True
except ImportError:
    AMSCROT_AVAILABLE = False


class IriResourceSpec(BaseModel):
    node_count: int | None = 1
    process_count: int | None = None
    processes_per_node: int | None = None
    cpu_cores_per_process: int | None = None
    exclusive_node_use: bool = True


class IriAttributes(BaseModel):
    """IRI-specific JobSpec attributes that vary per job (image, queue, constraint, ...)."""
    queue_name: str = "regular"
    constraint: str | None = None
    """ Slurm constraint, e.g. "gpu" on Perlmutter. Omitted on Frontier. """
    image: str | None = None
    module: str | None = None
    pre_launch: str | None = None
    """ Inline shell text run on the compute node before the main executable. """
    environment: dict[str, str] = {}


class IriDefaults(BaseModel):
    """ Per-job IRI resource and attribute defaults, loaded from cluster_defaults.json. """
    duration: int = 1800
    """ Seconds """
    resources: IriResourceSpec = IriResourceSpec()
    iri: IriAttributes = IriAttributes()


class IriClient:
    """
    Submits jobs and accesses files on a NERSC IRI facility (Perlmutter) via amscrot.

    All amscrot calls are synchronous; we run them in a worker thread to avoid
    blocking the MCP server's event loop.
    """

    def __init__(self, *, api_endpoint: str, api_key: str, machine: str, profile: str = "nersc-iri"):
        if not AMSCROT_AVAILABLE:
            raise RuntimeError(
                "NERSC support requires amscrot. Install with: uv sync --extra nersc"
            )
        self.api_endpoint = api_endpoint
        self.machine = machine
        self.profile = profile

        # Pass credentials in-memory; skips the ~/.amscrot/credentials.yml read.
        self._service_client = ServiceClient.create(
            type=Constants.ServiceType.AMSC_IRI,
            name=profile,
            credential={"api_key": api_key, "api_endpoint": api_endpoint},
        )
        self._compute_resource_id: str | None = None
        self._storage_resource_id: str | None = None

    async def init_resources(self) -> None:
        """ Resolve compute and storage resource IDs by calling the IRI discover() API. """
        await asyncio.to_thread(self._resolve_resources)
        logging.info(
            f"IRI resources resolved: compute={self._compute_resource_id} "
            f"storage={self._storage_resource_id}"
        )

    def _resolve_resources(self) -> None:
        discovery = self._service_client.discover()
        if not discovery.compute:
            raise RuntimeError(
                f"No IRI compute resources discovered for profile {self.profile!r} "
                f"(check token validity / endpoint)"
            )

        # Match logic from amscrot_vit.py: in NERSC_IRI normalized discovery, resources are
        # grouped under the facility (site); the entry with group=<machine> & name="compute"
        # is the right one. Fall back to the first compute resource if no exact match.
        compute_res = None
        for c in discovery.compute:
            if (c.data and c.data.get("group") == self.machine and c.name == "compute") or c.name == self.machine:
                compute_res = c
                break
        if not compute_res:
            compute_res = discovery.compute[0]
            logging.warning(
                f"IRI: no exact compute match for '{self.machine}', using fallback {compute_res.name!r}"
            )
        self._compute_resource_id = compute_res.data.get("id")

        # Storage: prefer one with "home" in its name (mirrors IriServiceClient._get_storage_resource_id).
        for s in discovery.storage:
            if "home" in (s.name or "").lower():
                self._storage_resource_id = s.data.get("id")
                break
        if not self._storage_resource_id and discovery.storage:
            self._storage_resource_id = discovery.storage[0].data.get("id")

    @property
    def compute_resource_id(self) -> str:
        if not self._compute_resource_id:
            raise RuntimeError("IRI resources not initialized; call init_resources() first")
        return self._compute_resource_id

    @property
    def storage_resource_id(self) -> str:
        if not self._storage_resource_id:
            raise RuntimeError("IRI storage resource not initialized")
        return self._storage_resource_id

    async def submit_job(self, spec: dict[str, Any], *, name: str) -> str:
        """
        Submit a job. ``spec`` follows the same shape as amscrot ``JobSpec`` kwargs:
        ``executable``, ``arguments``, ``resources``, ``attributes``.

        Returns the IRI job id.
        """
        return await asyncio.to_thread(self._submit_job, spec, name)

    def _submit_job(self, spec: dict[str, Any], name: str) -> str:
        job_spec = JobSpec(
            executable=spec.get("executable", "bash"),
            arguments=spec.get("arguments", []),
            resources=spec.get("resources", {}),
            attributes=spec.get("attributes", {}),
        )
        job = Job(
            name=name,
            type=JobType.COMPUTE,
            service_type=JobServiceType.BATCH,
            service_client=self._service_client,
            job_spec=job_spec,
        )
        # `skip_checks=True` downgrades plan errors to logged warnings instead of raising.
        # Needed because OLCF's moderate-enclave IRI doesn't update its per-resource
        # status feed — Frontier shows `current_status="unknown"` from
        # /api/v1/status/resources/<id> even though the list endpoint and OLCF's own
        # status board show "up"/"OPERATIONAL". `create()` itself doesn't depend on
        # `plan()` succeeding, so this only loses non-status checks (resource_id
        # lookup, executable presence, spec conversion) as failures — they still log
        # as warnings, and `create()` would re-do them anyway.
        self._service_client.plan(job, skip_checks=True)
        self._service_client.create(job)
        if not job.id:
            raise RuntimeError(f"IRI submission for '{name}' returned no job id")
        return str(job.id)

    async def get_job_status(self, job_id: str) -> dict:
        """ Returns the amscrot ``JobStatus.to_dict()`` for the given job id. """
        return await asyncio.to_thread(self._get_job_status, job_id)

    def _get_job_status(self, job_id: str) -> dict:
        job = Job(
            name="status-probe",
            type=JobType.COMPUTE,
            service_type=JobServiceType.BATCH,
            service_client=self._service_client,
        )
        job.id = job_id
        job.resource_id = self.compute_resource_id
        return self._service_client.status(job).to_dict()

    async def cancel_job(self, job_id: str) -> None:
        await asyncio.to_thread(self._cancel_job, job_id)

    def _cancel_job(self, job_id: str) -> None:
        job = Job(
            name="cancel-probe",
            type=JobType.COMPUTE,
            service_type=JobServiceType.BATCH,
            service_client=self._service_client,
        )
        job.id = job_id
        job.resource_id = self.compute_resource_id
        self._service_client.destroy(job)

    async def mkdir(self, path: str | Path, *, parents: bool = True) -> dict:
        return await asyncio.to_thread(
            lambda: self._service_client.filesystem.mkdir(
                self.storage_resource_id, str(path), p=parents,
            )
        )

    async def ls(self, path: str | Path, *, recursive: bool = False) -> dict:
        return await asyncio.to_thread(
            lambda: self._service_client.filesystem.ls(
                self.storage_resource_id, str(path), recursive=recursive,
            )
        )

    async def head(self, path: str | Path, *, lines: int | None = None) -> str:
        return await asyncio.to_thread(
            lambda: self._service_client.filesystem.head(
                self.storage_resource_id, str(path), lines=lines,
            )
        )

    async def download(self, remote: str | Path, local: str | Path) -> str:
        """
        Download a file via the IRI Filesystem API.

        Note: amscrot's ``IriFilesystem.download`` writes text only. Binary file
        download (e.g. ``.pt`` checkpoints) is not yet supported through this path.
        """
        return await asyncio.to_thread(
            lambda: self._service_client.filesystem.download(
                self.storage_resource_id, str(remote), str(local),
            )
        )

    async def upload(self, local: str | Path, remote: str | Path) -> dict:
        return await asyncio.to_thread(
            lambda: self._service_client.filesystem.upload(
                self.storage_resource_id, str(local), str(remote),
            )
        )


# TODO maybe should cache this per session
async def create_iri_client(*, iri_token: str) -> IriClient:
    """ NERSC IRI client (Perlmutter). """
    client = IriClient(
        api_endpoint=settings.nersc_iri_url,
        api_key=iri_token,
        machine=settings.nersc_machine,
        profile="nersc-iri",
    )
    await client.init_resources()
    return client


async def create_olcf_iri_client(*, iri_token: str) -> IriClient:
    """
    OLCF AmSC IRI client (Frontier, moderate enclave).

    Reuses the user's S3M token — the same bearer that authenticates against Odo's
    S3M endpoint also works against the OLCF moderate-enclave IRI service. The
    token's `iri-frontend-moderate` scope authorizes compute discovery/submit but
    NOT storage; the caller must handle file ops out-of-band (SSH) since
    `storage_resource_id` will raise on use.
    """
    client = IriClient(
        api_endpoint=settings.olcf_iri_url,
        api_key=iri_token,
        machine=settings.olcf_machine,
        profile="olcf-iri",
    )
    await client.init_resources()
    return client
