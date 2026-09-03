"""
S3 client for reading back HPC job output.

Vista submits jobs to OLCF through the AmSC IRI service for compute, but the
IRI service's per-user token doesn't grant storage scope, so there is no way to
pull files off Odo or Frontier with it. Rather than hold a second credential
against the cluster's filesystem — which is what Globus was for, and what
OLCF's 3-day High Assurance session timeout made unworkable for unattended
operation — the *job* pushes its own output to an S3 bucket Vista owns
(`jobscripts/s3_put.py` runs on the compute node), and this module reads it
back.

Nothing here writes or deletes — this is the read half of that arrangement,
and `aws/job-output-s3-policy.json` is scoped to match. It shares one credential
with the job's push (`settings.s3.key_id` / `secret`), which is why that key has
to grant reads as well as writes, and falls back to the boto3 default credential
chain (an EC2 instance profile / IRSA role) when it is unset. See `openspec/changes/replace-globus-with-s3-push/design.md`.

This module is the async wrapper around `boto3`'s S3 client. All calls run in
worker threads (boto3 is sync).
"""

from __future__ import annotations
import asyncio
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError

from ..config import settings


class S3Client:
    """
    Async wrapper around the boto3 S3 client, scoped to one bucket.

    Created per tool call via `create_s3_client`. Uses the configured
    credential when there is one, and otherwise leaves boto3 to its default
    chain — passing `None` explicitly would defeat that lookup, so the keys are
    only added to the kwargs when both are set.
    """

    def __init__(self, *, bucket: str):
        self.bucket = bucket
        kwargs: dict[str, Any] = {
            "region_name": settings.s3.region,
            "endpoint_url": settings.s3.endpoint or None,
        }
        if settings.s3.key_id and settings.s3.secret:
            kwargs["aws_access_key_id"] = settings.s3.key_id
            kwargs["aws_secret_access_key"] = settings.s3.secret
        self._s3 = boto3.client("s3", **kwargs)

    async def list_objects(self, *, prefix: str) -> list[dict[str, Any]]:
        """
        List every object under `prefix`, following pagination. Returns dicts
        with at least `key` and `size`, plus a `path` key holding the portion
        of the key below `prefix` (the shape `_flatten_ls_paths` consumers
        expect).

        Unlike the Globus path this replaced, one call covers a whole tree —
        S3 keys are flat, so there is no directory walk and no per-level
        round trip.
        """
        return await asyncio.to_thread(self._list_objects, prefix)

    def _list_objects(self, prefix: str) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        base = prefix.rstrip("/") + "/"
        paginator = self._s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=base):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                results.append({
                    "key": key,
                    "size": obj.get("Size", 0),
                    "path": key[len(base):] if key.startswith(base) else key,
                })
        return results

    async def object_exists(self, *, key: str) -> bool:
        """
        True iff `key` exists. Used to test for the upload manifest, which the
        job writes last — its absence on a finished job means the push was
        truncated rather than that the job produced nothing.
        """
        return await asyncio.to_thread(self._object_exists, key)

    def _object_exists(self, key: str) -> bool:
        try:
            self._s3.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    async def get_text(self, *, key: str, max_bytes: int | None = None) -> str:
        """
        Read an object as UTF-8 text, optionally only its first `max_bytes`
        (via a ranged GET, so a huge log costs one small request). Decoding is
        lenient: a job log can contain arbitrary bytes and a status call must
        not fail over that.

        An empty object reads as `""` rather than raising. That case is real:
        a job that sends everything to stderr, or is killed before writing a
        line, still uploads a zero-byte `log.out`, and S3 answers a ranged GET
        against it with 416 InvalidRange.
        """
        return await asyncio.to_thread(self._get_text, key, max_bytes)

    def _get_text(self, key: str, max_bytes: int | None) -> str:
        kwargs: dict[str, Any] = {"Bucket": self.bucket, "Key": key}
        if max_bytes:
            kwargs["Range"] = f"bytes=0-{max_bytes - 1}"
        try:
            body = self._s3.get_object(**kwargs)["Body"].read()
        except ClientError as e:
            # Only an empty object can be outside `bytes=0-...`, so this is not
            # worth a preceding head_object on every call.
            if e.response.get("Error", {}).get("Code") in ("InvalidRange", "416"):
                return ""
            raise
        return body.decode("utf-8", errors="replace")

    async def download_file(self, *, key: str, local_path: str | Path) -> None:
        """
        Download one object to `local_path`, creating parent directories.
        Binary-safe — which the IRI filesystem API's text-only `download` is
        not, and which is why `.pt` checkpoints needed a transfer service
        before this.
        """
        await asyncio.to_thread(self._download_file, key, str(local_path))

    def _download_file(self, key: str, local_path: str) -> None:
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        self._s3.download_file(self.bucket, key, local_path)


def create_s3_client() -> S3Client:
    """
    Construct an S3Client for the configured job-output bucket, raising a
    `ToolError` if none is set. See `S3Client` for credential resolution.
    """
    return S3Client(bucket=settings.require_s3_bucket())
