"""In-memory stand-in for ``lib.globus.GlobusClient``.

Models the two surfaces the real client has: Transfer for directory metadata,
and the collection's HTTPS interface for bytes. ``files`` is the remote
filesystem — seed it to make a read succeed, and read it back to check what an
upload wrote.

It reproduces the two behaviours the real collections have that callers must
work around, because code that gets them wrong passes against a naive fake:
a range outside the file is refused, and a read of a path that is not there
raises ``GlobusFileNotFound`` rather than returning empty.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from vista_mcp_server.lib.globus import GlobusFileNotFound


class FakeGlobusClient:
    """Records Globus mkdir / ls calls, and serves an in-memory remote tree."""

    def __init__(self, *, out_dir_permissions: str = "2775", cluster: str = "odo"):
        self.out_dir_permissions = out_dir_permissions
        self.cluster = cluster
        self.ls_calls: list[tuple[str, str]] = []
        self.mkdir_p_calls: list[tuple[str, str, str | None]] = []
        self.uploads: list[tuple[str, str]] = []
        """(collection_id, remote_path) per ``upload_file``."""
        self.downloads: list[tuple[str, str]] = []
        """(collection_id, remote_path) per ``download_file``."""
        self.range_reads: list[tuple[str, int, int]] = []
        """(remote_path, start, end) per ``read_range`` — the incremental
        tail's whole behaviour is which ranges it asks for."""
        self.stats: list[str] = []
        self.files: dict[str, bytes] = {}
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

    # --- Transfer -----------------------------------------------------------

    async def operation_ls(
        self, *, endpoint: str, path: str, recursive: bool = False
    ) -> list[dict[str, Any]]:
        self.ls_calls.append((endpoint, path))
        if path in self.ls_entries:
            return list(self.ls_entries[path])
        if recursive:
            # What the real client does: a recursive walk swallows a 404 per
            # subtree, so a directory that is not there yet comes back empty
            # rather than raising. That is what makes "no output files yet" the
            # right answer for a job that has not written any, and what leaves
            # a raised error meaning something actually went wrong.
            return []
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

    # --- the HTTPS interface ------------------------------------------------

    def _content(self, remote_path: str) -> bytes:
        try:
            return self.files[remote_path]
        except KeyError:
            raise GlobusFileNotFound(f"{remote_path} is not on {self.cluster}.")

    async def stat(self, *, collection_id: str, remote_path: str) -> int:
        self.stats.append(remote_path)
        return len(self._content(remote_path))

    async def read_range(
        self, *, collection_id: str, remote_path: str, start: int, end: int
    ) -> bytes:
        self.range_reads.append((remote_path, start, end))
        content = self._content(remote_path)
        if start > end:
            return b""
        if start >= len(content):
            # What a real collection answers for a range past the end. Returning
            # b"" instead would hide an off-by-one in the caller's offset.
            raise AssertionError(
                f"range {start}-{end} starts past the end of {remote_path} "
                f"({len(content)} bytes)"
            )
        return content[start : end + 1]

    async def download_file(
        self, *, collection_id: str, remote_path: str, local_path: Path
    ) -> None:
        self.downloads.append((collection_id, remote_path))
        content = self._content(remote_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(content)

    async def upload_file(
        self, *, collection_id: str, local_path: Path, remote_path: str
    ) -> None:
        self.uploads.append((collection_id, remote_path))
        self.files[remote_path] = local_path.read_bytes()
