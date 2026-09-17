"""In-memory stand-in for ``lib.globus.GlobusClient``."""

from __future__ import annotations

from typing import Any


class FakeGlobusClient:
    """Records Globus mkdir / ls / transfer calls."""

    def __init__(self, *, out_dir_permissions: str = "2775"):
        self.out_dir_permissions = out_dir_permissions
        # This machine's own collection: registered here, seen as connected
        # once the caller has started something. `connected` starts False so a
        # test can decide when Globus notices.
        self.created_endpoints: list[str] = []
        self.setup_key = "fake-setup-key"
        self.connected = False
        self.ls_calls: list[tuple[str, str]] = []
        self.mkdir_p_calls: list[tuple[str, str, str | None]] = []
        self.transfers: list[dict[str, Any]] = []
        # path -> list of entry dicts ({name, type, permissions})
        self.ls_entries: dict[str, list[dict[str, Any]]] = {}

    def seed_odo_out_dir(self, base: str) -> None:
        """Make ``_require_odo_out_dir`` succeed for ``base``."""
        self.ls_entries[base] = [
            {
                "name": "out",
                "type": "dir",
                "permissions": self.out_dir_permissions,
            }
        ]

    async def operation_ls(self, *, endpoint: str, path: str) -> list[dict[str, Any]]:
        self.ls_calls.append((endpoint, path))
        if path in self.ls_entries:
            return list(self.ls_entries[path])
        # Empty src dir → triggers upload path in ``_sync_job_sources``
        raise FileNotFoundError(f"no such path: {path}")

    async def operation_mkdir_p(
        self,
        *,
        endpoint: str,
        path: str,
        parents_below: str | None = None,
    ) -> None:
        self.mkdir_p_calls.append((endpoint, path, parents_below))

    async def transfer_and_wait(
        self,
        *,
        src_endpoint: str,
        dst_endpoint: str,
        items: list,
        label: str = "vista",
        sync_level: str = "checksum",
        poll_seconds: int = 10,
        timeout_seconds: int = 3600,
    ) -> dict[str, Any]:
        self.transfers.append(
            {
                "src_endpoint": src_endpoint,
                "dst_endpoint": dst_endpoint,
                "items": list(items),
                "label": label,
            }
        )
        return {"status": "SUCCEEDED", "task_id": "fake-task"}

    async def create_gcp_endpoint(self, *, display_name: str) -> str:
        self.created_endpoints.append(display_name)
        return self.setup_key

    async def gcp_connected(self, collection_id: str) -> bool:
        return self.connected
