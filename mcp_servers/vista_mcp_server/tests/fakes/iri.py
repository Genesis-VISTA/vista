"""In-memory stand-in for ``lib.iri.IriClient``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from vista_mcp_server.lib.iri import IriFileNotFound


class FakeIriClient:
    """
    Records IRI calls and returns canned ids, statuses and files.

    `status` is what `get_job_status` returns, already normalized as the real
    client's is (`{state, exit_code?, message?}`). `ls_paths` maps a folder to the
    paths under it; `head_content` maps a file to its text, and a file not in it
    raises `IriFileNotFound`, as the facility does.
    """

    def __init__(
        self,
        *,
        compute_resource_id: str = "fake-compute-resource",
        storage_resource_id: str = "fake-storage-resource",
        job_id: str = "iri-job-1",
        status: dict[str, Any] | None = None,
    ):
        self._compute_resource_id = compute_resource_id
        self._storage_resource_id = storage_resource_id
        self._next_job_id = job_id
        self._status = status or {
            "state": "COMPLETED",
            "exit_code": 0,
            "message": "ok",
        }
        self.submitted: list[tuple[dict[str, Any], str]] = []
        self.cancelled: list[str] = []
        self.mkdirs: list[str] = []
        self.uploads: list[tuple[str, str]] = []
        self.downloads: list[tuple[str, str]] = []
        self.ls_paths: dict[str, list[str]] = {}
        self.head_content: dict[str, str] = {}
        self._job_counter = 0

    @property
    def compute_resource_id(self) -> str:
        return self._compute_resource_id

    @property
    def storage_resource_id(self) -> str:
        return self._storage_resource_id

    async def submit_job(self, spec: dict[str, Any], *, name: str) -> str:
        self.submitted.append((spec, name))
        self._job_counter += 1
        if self._job_counter == 1:
            return self._next_job_id
        return f"{self._next_job_id}-{self._job_counter}"

    async def get_job_status(self, job_id: str) -> dict:
        return dict(self._status)

    async def cancel_job(self, job_id: str) -> None:
        self.cancelled.append(job_id)

    async def mkdir(self, path: str | Path, *, parents: bool = True) -> None:
        self.mkdirs.append(str(path))

    async def ls(
        self, path: str | Path, *, recursive: bool = False
    ) -> list[dict[str, Any]]:
        return [
            {"path": p, "type": "f", "size": 0}
            for p in self.ls_paths.get(str(path), [])
        ]

    async def head(self, path: str | Path, *, lines: int | None = None) -> str:
        if str(path) not in self.head_content:
            raise IriFileNotFound(str(path))
        return self.head_content[str(path)]

    async def upload(self, local: str | Path, remote: str | Path) -> None:
        self.uploads.append((str(local), str(remote)))

    async def download(self, remote: str | Path, local: str | Path) -> str:
        self.downloads.append((str(remote), str(local)))
        return str(local)
