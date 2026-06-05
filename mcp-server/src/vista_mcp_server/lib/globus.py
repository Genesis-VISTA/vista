"""
Globus file-transfer client for Frontier (cluster="frontier") file operations.

Vista submits jobs to Frontier through the OLCF AmSC IRI service for compute,
but the IRI service's per-user token doesn't grant storage scope; file ops
therefore go through Globus. The Vista server runs a Globus Connect (Personal
or Server) instance exposing local_hpc_jobs_dir + output_dir; the user's
per-record Globus Auth + Transfer tokens authenticate as their OLCF identity
for access to the OLCF DTN collection.

This module is the async wrapper around `globus_sdk.TransferClient`. All
calls run in worker threads (globus-sdk is sync) and the underlying
`RefreshTokenAuthorizer` refreshes the access token transparently when it
expires.
"""

from __future__ import annotations
import asyncio
import logging
import time
from typing import Any, Literal

import globus_sdk

from ..config import settings


SyncLevel = Literal["exists", "size", "mtime", "checksum"]
""" Globus TransferData sync_level values. Default in this module is "checksum". """

TransferItem = tuple[str, str, bool]
""" (src_path, dst_path, recursive) tuple, matching `TransferData.add_item` shape. """


class GlobusClient:
    """
    Async wrapper around `globus_sdk.TransferClient` with refresh-token auth.

    Created per submission via `create_globus_client`. The underlying authorizer
    auto-refreshes the access token using the user's refresh token, so a single
    client survives the ~48h Globus access-token TTL without re-authentication.
    """

    def __init__(
        self, *,
        access_token: str | None = None,
        refresh_token: str | None = None,
        client_id: str | None = None,
    ):
        client_id = client_id or settings.globus_native_app_client_id
        if refresh_token:
            # RefreshTokenAuthorizer mints a fresh access token using the refresh
            # token on first call, then auto-renews when each minted token expires.
            # We deliberately don't pass `access_token` here: globus-sdk v4.x requires
            # access_token + expires_at together or neither, and we don't track
            # expires_at on the user record — letting the authorizer mint from
            # scratch is simpler than threading a third DB column.
            native_app = globus_sdk.NativeAppAuthClient(client_id)
            self._authorizer: globus_sdk.authorizers.GlobusAuthorizer = (
                globus_sdk.RefreshTokenAuthorizer(
                    refresh_token=refresh_token,
                    auth_client=native_app,
                )
            )
        elif access_token:
            # Fallback for callers that have only a short-lived access token (no
            # refresh). Will hard-fail after ~48h when the token expires.
            self._authorizer = globus_sdk.AccessTokenAuthorizer(access_token)
        else:
            raise ValueError("GlobusClient requires either refresh_token or access_token")
        self._tc = globus_sdk.TransferClient(authorizer=self._authorizer)

    # --- transfer task submission + wait -----------------------------------

    async def submit_transfer(
        self, *,
        src_endpoint: str,
        dst_endpoint: str,
        items: list[TransferItem],
        label: str = "vista",
        sync_level: SyncLevel = "checksum",
    ) -> str:
        """
        Submit a Globus transfer task. Returns the task id; does NOT wait for
        completion (call `wait_for_task` separately).
        """
        return await asyncio.to_thread(
            self._submit_transfer, src_endpoint, dst_endpoint, items, label, sync_level,
        )

    def _submit_transfer(
        self,
        src_endpoint: str,
        dst_endpoint: str,
        items: list[TransferItem],
        label: str,
        sync_level: SyncLevel,
    ) -> str:
        tdata = globus_sdk.TransferData(
            source_endpoint=src_endpoint,
            destination_endpoint=dst_endpoint,
            label=label,
            sync_level=sync_level,
            verify_checksum=True,
            preserve_timestamp=True,
        )
        for src_path, dst_path, recursive in items:
            tdata.add_item(src_path, dst_path, recursive=recursive)
        result = self._tc.submit_transfer(tdata)
        task_id = result["task_id"]
        logging.info(
            f"Globus task {task_id} submitted ({label}): "
            f"{src_endpoint} → {dst_endpoint}, {len(items)} item(s)"
        )
        return task_id

    async def wait_for_task(
        self, task_id: str, *,
        poll_seconds: int = 10,
        timeout_seconds: int = 3600,
    ) -> dict[str, Any]:
        """
        Block until the task reaches SUCCEEDED or FAILED; raise TimeoutError
        after `timeout_seconds`. Returns the final task dict.
        """
        return await asyncio.to_thread(
            self._wait_for_task, task_id, poll_seconds, timeout_seconds,
        )

    def _wait_for_task(
        self, task_id: str, poll_seconds: int, timeout_seconds: int,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        while True:
            task = self._tc.get_task(task_id)
            status = task["status"]
            if status in ("SUCCEEDED", "FAILED"):
                return dict(task)
            if time.monotonic() > deadline:
                raise TimeoutError(
                    f"Globus task {task_id} did not finish in {timeout_seconds}s "
                    f"(status={status})"
                )
            time.sleep(poll_seconds)

    async def transfer_and_wait(
        self, *,
        src_endpoint: str,
        dst_endpoint: str,
        items: list[TransferItem],
        label: str = "vista",
        sync_level: SyncLevel = "checksum",
        poll_seconds: int = 10,
        timeout_seconds: int = 3600,
    ) -> dict[str, Any]:
        """
        Convenience: submit a transfer and block until it finishes. Raises
        RuntimeError on FAILED.
        """
        task_id = await self.submit_transfer(
            src_endpoint=src_endpoint,
            dst_endpoint=dst_endpoint,
            items=items,
            label=label,
            sync_level=sync_level,
        )
        task = await self.wait_for_task(task_id, poll_seconds=poll_seconds, timeout_seconds=timeout_seconds)
        if task["status"] != "SUCCEEDED":
            raise RuntimeError(
                f"Globus task {task_id} ({label}) failed: status={task['status']} "
                f"nice_status={task.get('nice_status')}"
            )
        return task

    # --- filesystem operations on a collection ------------------------------

    async def operation_ls(
        self, *, endpoint: str, path: str, recursive: bool = False,
    ) -> list[dict[str, Any]]:
        """
        List entries under `path` on the given collection. Returns a list of dicts
        each with at least `name`, `type` ("file"/"dir"), `size`, plus a `path`
        key injected by this wrapper holding the absolute path.

        When `recursive=True`, BFS-walks the tree (Globus has no native recursive
        ls). Subtree errors during the walk are logged and skipped.
        """
        return await asyncio.to_thread(self._operation_ls, endpoint, path, recursive)

    def _operation_ls(
        self, endpoint: str, path: str, recursive: bool,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        queue = [path]
        while queue:
            cur = queue.pop(0)
            try:
                resp = self._tc.operation_ls(endpoint, path=cur)
            except globus_sdk.TransferAPIError as e:
                if recursive and e.http_status == 404:
                    continue
                if not recursive:
                    raise
                logging.debug(f"globus ls subtree skipped ({cur}): {e.message}")
                continue
            cur_norm = cur.rstrip("/")
            for entry in resp.get("DATA", []):
                name = entry.get("name", "")
                if not name:
                    continue
                full = f"{cur_norm}/{name}" if cur_norm else name
                entry["path"] = full
                results.append(entry)
                if recursive and entry.get("type") == "dir":
                    queue.append(full)
            if not recursive:
                break
        return results

    async def operation_mkdir(self, *, endpoint: str, path: str) -> None:
        """
        Create a directory on the collection. Idempotent: succeeds if the dir
        already exists (Globus returns ExternalError.MkdirFailed.Exists, which
        we swallow).
        """
        await asyncio.to_thread(self._operation_mkdir, endpoint, path)

    def _operation_mkdir(self, endpoint: str, path: str) -> None:
        try:
            self._tc.operation_mkdir(endpoint, path=path)
        except globus_sdk.TransferAPIError as e:
            code = (e.code or "") + " " + (e.message or "")
            if "Exists" in code or "exists" in code:
                return
            raise


def create_globus_client(
    *,
    access_token: str | None = None,
    refresh_token: str | None = None,
) -> GlobusClient:
    """
    Construct a GlobusClient. Pass `refresh_token` (preferred) for auto-renewal,
    or just `access_token` for one-shot use until it expires (~48h).
    """
    return GlobusClient(access_token=access_token, refresh_token=refresh_token)
