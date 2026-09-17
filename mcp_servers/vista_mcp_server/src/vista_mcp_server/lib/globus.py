"""
Globus file-transfer client for Odo and Frontier file operations.

Vista submits jobs to OLCF through the AmSC IRI service for compute, but the
IRI service's per-user token doesn't grant storage scope; file ops therefore
go through Globus. The Vista server runs a Globus Connect (Personal or Server)
instance exposing local_hpc_jobs_dir + output_dir, and a deployment-wide,
developer-owned Globus refresh token authorizes both sides of every transfer.
Odo (open enclave) and Frontier (moderate enclave) sit behind different OLCF SSO
session domains, so there is one token per enclave
(`settings.odo_globus_refresh_token` / `settings.frontier_globus_refresh_token`,
selected via `settings.require_globus_token(cluster)`). Per-user authorization is
the S3M token project check in `lib/olcf_token.py`.

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

    Created per submission via `create_globus_client`. The underlying
    `RefreshTokenAuthorizer` mints a fresh ~48h-lived access token from the
    deployment's long-lived refresh token on first use, then auto-renews when
    each minted access token expires.
    """

    def __init__(self, *, refresh_token: str, client_id: str | None = None):
        client_id = client_id or settings.globus_native_app_client_id
        native_app = globus_sdk.NativeAppAuthClient(client_id)
        self._authorizer = globus_sdk.RefreshTokenAuthorizer(
            refresh_token=refresh_token,
            auth_client=native_app,
        )
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
                # `task` is a globus_sdk.GlobusHTTPResponse; its __iter__ yields
                # ints (treats the response like a sequence), so `dict(task)`
                # raises KeyError. Use `.data` — the parsed JSON body dict.
                return dict(task.data)
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
        Create a single directory on the collection. Idempotent: succeeds if the
        dir already exists (Globus returns ExternalError.MkdirFailed.Exists, which
        we swallow). Fails if the parent doesn't exist — use `operation_mkdir_p`
        for `mkdir -p` semantics.
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

    async def operation_mkdir_p(
        self, *, endpoint: str, path: str, parents_below: str,
    ) -> None:
        """
        `mkdir -p` equivalent for a Globus collection: idempotently create
        `path` plus any missing intermediate dirs BELOW `parents_below`.

        `parents_below` is the deepest ancestor we assume already exists (and
        do NOT try to create). For Vista's Frontier path this is the user's
        `frontier_remote_dir` — Globus would reject mkdir of /lustre, /lustre/orion,
        etc. anyway, and walking that high is wasteful.

        Globus's MKD is one-level-only, so this helper walks the suffix between
        `parents_below` and `path` and calls `operation_mkdir` for each segment.
        Already-existing segments are no-ops thanks to operation_mkdir's
        Exists-swallowing behavior.
        """
        path = path.rstrip("/")
        parents_below = parents_below.rstrip("/")
        if not (path == parents_below or path.startswith(parents_below + "/")):
            raise ValueError(
                f"path {path!r} is not under parents_below {parents_below!r}"
            )
        suffix = path[len(parents_below):].lstrip("/")
        if not suffix:
            return
        current = parents_below
        for part in suffix.split("/"):
            current = f"{current}/{part}"
            await self.operation_mkdir(endpoint=endpoint, path=current)


    # --- this machine's own collection -------------------------------------

    async def create_gcp_endpoint(self, *, display_name: str) -> str:
        """Register a Globus Connect Personal collection and return its setup key.

        The key is what lets `gcp_vm.Endpoint.setup` run without a terminal:
        Globus Connect Personal's interactive setup exists to obtain exactly
        this, by sending the researcher through a browser login.

        Single use, and single use per collection -- calling this twice makes
        two collections, so the caller checks whether one already exists.
        """
        return await asyncio.to_thread(self._create_gcp_endpoint, display_name)

    def _create_gcp_endpoint(self, display_name: str) -> str:
        # `TransferClient.create_endpoint` was removed in globus-sdk 4, and the
        # path needs its version prefix or the API answers 404 with no body,
        # which reads like a refusal and is not one.
        response = self._tc.post(
            "/v0.10/endpoint",
            data={"DATA_TYPE": "endpoint", "display_name": display_name,
                  "is_globus_connect": True},
        )
        key = response.data.get("globus_connect_setup_key")
        if not key:
            raise RuntimeError(
                "Globus created the collection but returned no setup key, so it "
                "cannot be brought online from here"
            )
        return key

    async def gcp_connected(self, collection_id: str) -> bool:
        """Whether Globus itself can see this collection right now.

        The question a caller waiting for the endpoint actually has. A running
        microVM only means the process started; this is the far end agreeing
        that it did, which is what a transfer needs.
        """
        return await asyncio.to_thread(self._gcp_connected, collection_id)

    def _gcp_connected(self, collection_id: str) -> bool:
        try:
            return bool(self._tc.get_endpoint(collection_id).get("gcp_connected"))
        except globus_sdk.TransferAPIError:
            # Newly created and not yet visible, most likely. The caller is in
            # a polling loop; a transient no is the right answer to give it.
            return False


def create_globus_client(*, refresh_token: str) -> GlobusClient:
    """ Construct a GlobusClient. The authorizer auto-refreshes the access token. """
    return GlobusClient(refresh_token=refresh_token)
