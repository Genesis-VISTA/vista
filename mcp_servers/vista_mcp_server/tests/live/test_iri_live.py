"""
VISTA's IRI client against the real facilities: submit, status and cancel on
each cluster, and Perlmutter's filesystem tasks.

Marked `live` and `hpc`, so it runs only with `VISTA_RUN_LIVE=1 VISTA_RUN_HPC=1`,
and each cluster is skipped unless its token and folder are in the environment:

    VISTA_LIVE_ODO_S3M_TOKEN       VISTA_LIVE_ODO_DIR
    VISTA_LIVE_FRONTIER_S3M_TOKEN  VISTA_LIVE_FRONTIER_DIR
    VISTA_LIVE_LUX_S3M_TOKEN       VISTA_LIVE_LUX_DIR
    VISTA_LIVE_NERSC_IRI_TOKEN     VISTA_LIVE_PERLMUTTER_DIR  VISTA_LIVE_NERSC_ACCOUNT

Each `_DIR` is a folder the job may write in: the OLCF jobs run as the
project's automation user, so a project-shared folder rather than a home. The
job only sleeps, and is cancelled as soon as its status has been read, so the
queue wait never matters and nothing runs. Each job writes nothing unless it
starts before the cancel, and then only its empty `vista-live-iri-*` logs.

Perlmutter's round trip uses `<VISTA_LIVE_PERLMUTTER_DIR>/vista-live-iri/`,
overwriting the same two files each run, since IRI has no `rm`.

Tokens are read from the environment and never printed; a failure message
from the client names the cluster and the HTTP status, not the token.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from pathlib import Path

import pytest

from vista_mcp_server.config import settings
from vista_mcp_server.lib.iri import IriCluster, IriFileNotFound, client_for
from vista_mcp_server.lib.olcf_token import get_s3m_token_project

pytestmark = [pytest.mark.live, pytest.mark.hpc, pytest.mark.anyio]

_STATES = {
    "QUEUED",
    "PENDING",
    "ACTIVE",
    "RUNNING",
    "COMPLETED",
    "FAILED",
    "CANCELED",
    "TIMEOUT",
}
_CANCEL_WAIT_S = 300


def _need(*names: str) -> list[str]:
    values = [os.environ.get(n, "").strip() for n in names]
    missing = [n for n, v in zip(names, values) if not v]
    if missing:
        pytest.skip(f"set {', '.join(missing)}")
    return values


async def _job_spec(cluster: IriCluster, token: str, directory: str) -> dict:
    tag = f"vista-live-iri-{uuid.uuid4().hex[:8]}"
    spec: dict = {
        "executable": "bash",
        "arguments": ["-c", "sleep 600"],
        "directory": directory,
        "stdout_path": f"{directory}/{tag}-%j.out",
        "stderr_path": f"{directory}/{tag}-%j.err",
        "resources": {"node_count": 1},
        "attributes": {"duration": 900},
    }
    if cluster == "perlmutter":
        (account,) = _need("VISTA_LIVE_NERSC_ACCOUNT")
        spec["attributes"] |= {
            "queue_name": "debug",
            "account": account,
            "custom_attributes": {"constraint": "cpu"},
        }
    else:
        url = (
            settings.odo_introspect_url
            if cluster == "odo"
            else settings.frontier_introspect_url
        )
        spec["attributes"] |= {
            "queue_name": "batch",
            "account": await get_s3m_token_project(token, introspect_url=url),
        }
        if (
            cluster == "lux"
        ):  # Lux hands out no GPU unless asked, and takes the queue as its partition
            spec["resources"] |= {"process_count": 1, "gpu_cores_per_process": 1}
    return spec


@pytest.mark.parametrize("cluster", ["odo", "frontier", "lux", "perlmutter"])
async def test_submit_status_cancel(cluster: IriCluster):
    if cluster == "perlmutter":
        token, directory = _need(
            "VISTA_LIVE_NERSC_IRI_TOKEN", "VISTA_LIVE_PERLMUTTER_DIR"
        )
    else:
        up = cluster.upper()
        token, directory = _need(f"VISTA_LIVE_{up}_S3M_TOKEN", f"VISTA_LIVE_{up}_DIR")
    iri = client_for(cluster, token)
    spec = await _job_spec(cluster, token, directory.rstrip("/"))

    job_id = await iri.submit_job(spec, name=f"vista-live-iri-{cluster}")
    try:
        status = await iri.get_job_status(job_id)
        assert status["state"] in _STATES, status
        assert "exit_code" not in status or status["state"] in {
            "COMPLETED",
            "FAILED",
            "CANCELED",
            "TIMEOUT",
        }
    finally:
        await iri.cancel_job(job_id)

    deadline = time.monotonic() + _CANCEL_WAIT_S
    while (status := await iri.get_job_status(job_id))["state"] != "CANCELED":
        assert status["state"] not in {"COMPLETED", "TIMEOUT"}, (
            f"job {job_id} was not cancelled: {status}"
        )
        assert time.monotonic() < deadline, (
            f"job {job_id} still {status} {_CANCEL_WAIT_S} s after cancel"
        )
        await asyncio.sleep(10)


async def test_perlmutter_filesystem_round_trip(tmp_path: Path):
    token, base = _need("VISTA_LIVE_NERSC_IRI_TOKEN", "VISTA_LIVE_PERLMUTTER_DIR")
    iri = client_for("perlmutter", token)
    folder = f"{base.rstrip('/')}/vista-live-iri"
    text = "line one\nline two: ünïcode\nline three\n".encode("utf-8")
    binary = (
        bytes(range(256)) * 64 + uuid.uuid4().bytes
    )  # not UTF-8, so it comes back as base64

    await iri.mkdir(folder)
    (tmp_path / "up.txt").write_bytes(text)
    (tmp_path / "up.bin").write_bytes(binary)
    await iri.upload(tmp_path / "up.txt", f"{folder}/round-trip.txt")
    await iri.upload(tmp_path / "up.bin", f"{folder}/round-trip.bin")

    entries = {e["path"]: e for e in await iri.ls(folder)}
    assert entries[f"{folder}/round-trip.txt"]["size"] == len(text)
    assert entries[f"{folder}/round-trip.bin"]["size"] == len(binary)
    assert int((await iri.stat(f"{folder}/round-trip.bin")).get("size") or 0) == len(
        binary
    )
    assert (await iri.head(f"{folder}/round-trip.txt", lines=2)).splitlines()[:2] == [
        "line one",
        "line two: ünïcode",
    ]

    await iri.download(f"{folder}/round-trip.txt", tmp_path / "down.txt")
    await iri.download(f"{folder}/round-trip.bin", tmp_path / "down.bin")
    assert (tmp_path / "down.txt").read_bytes() == text
    assert (tmp_path / "down.bin").read_bytes() == binary

    with pytest.raises(IriFileNotFound):
        await iri.stat(f"{folder}/no-such-file-{uuid.uuid4().hex[:8]}")
