"""
Lux on IRI: S3M's v2 compute, with files through Frontier's Globus collection.

Lux is an OLCF cluster like Odo and Frontier, with three differences these pin
down: its own S3M token, Frontier's Globus connection (Lux mounts Frontier's
Orion), and a resource request in Lux's terms (a partition, GPUs).
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest
from fastmcp.exceptions import ToolError

import vista_mcp_server.submit_job_mcp as m
from vista_mcp_server.config import settings
from vista_mcp_server.lib.iri import iri_target
from vista_mcp_server.lib.user_config import UserConfig
from fakes import FakeGlobusClient, FakeIriClient

pytestmark = [pytest.mark.unit, pytest.mark.anyio]

BASE = "/lustre/orion/abc123/proj-shared/vista"
CFG = UserConfig(
    lux_s3m_token="lux-token",
    lux_remote_dir=BASE,
    frontier_globus_token="fr-transfer",
    frontier_globus_https_token="fr-https",
)
LUX_DEFAULTS = {
    "duration": 1800,
    "resources": {
        "node_count": 2,
        "processes_per_node": 4,
        "gpus_per_node": 4,
        "exclusive_node_use": True,
    },
    "iri": {"partition": "batch", "environment": {"FOO": "bar baz"}},
}


def _write_job(root: Path, lux: dict = LUX_DEFAULTS, *, setup: bool = True) -> Path:
    job = root / "lux-demo"
    job.mkdir(parents=True)
    (job / "README.md").write_text("# lux-demo\n\nDemo job.\n", encoding="utf-8")
    (job / "cluster_defaults.json").write_text(
        json.dumps({"lux": lux}), encoding="utf-8"
    )
    (job / "job.lux.slurm").write_text(
        '#!/bin/bash -l\necho "run $@"\n', encoding="utf-8"
    )
    (job / "run.py").write_text("print('hi')\n", encoding="utf-8")
    if setup:
        (job / "setup_lux.sh").write_text(
            "set -euo pipefail\necho setup-ran\n", encoding="utf-8"
        )
    return job


class Env:
    def __init__(self, tmp_path: Path, monkeypatch):
        self.tmp_path = tmp_path
        self.monkeypatch = monkeypatch
        self.iri = FakeIriClient(job_id="396254")
        self.globus = FakeGlobusClient(cluster="lux")
        self.globus.seed_remote_dir(BASE)
        self.iri_calls: list[tuple[str, str]] = []
        self.globus_calls: list[tuple[str, object]] = []
        self.introspected: list[str] = []

        def client_for(cluster, token):
            self.iri_calls.append((cluster, token))
            return self.iri

        def create_globus_client(*, tokens, cluster):
            self.globus_calls.append((cluster, tokens))
            return self.globus

        async def introspect(token, *, introspect_url):
            self.introspected.append(token)
            return "xyz789"

        monkeypatch.setattr(m, "client_for", client_for)
        monkeypatch.setattr(m, "create_globus_client", create_globus_client)
        monkeypatch.setattr(m, "get_s3m_token_project", introspect)
        monkeypatch.setattr(
            settings, "frontier_globus_collection_id", "frontier-collection"
        )

    def jobs(self, **kwargs) -> None:
        root = self.tmp_path / "hpc_jobs"
        _write_job(root, **kwargs)
        self.monkeypatch.setattr(settings, "local_hpc_jobs_dir", root)
        self.monkeypatch.setattr(m, "AVAILABLE_JOBS", m.get_available_jobs())

    async def submit(self, cfg: UserConfig = CFG, args: str | None = "a b"):
        return await m._submit_job("lux", cfg, "lux-demo", None, None, args)


@pytest.fixture
def env(tmp_path, monkeypatch) -> Env:
    e = Env(tmp_path, monkeypatch)
    e.jobs()
    return e


# ---------------------------------------------------------------- the target


def test_lux_is_its_own_v2_resource_on_the_moderate_enclave():
    t = iri_target("lux")
    assert (t.base_url, t.api_version, t.compute_resource_id) == (
        "https://amsc-moderate.s3m.olcf.ornl.gov",
        "v2",
        "lux",
    )
    assert t.storage_resource_id is None  # files go through Globus


# ---------------------------------------------------------------- submission


async def test_a_lux_job_is_an_iri_job_charged_to_its_tokens_project(env):
    job_id, log_path, err_path, out_dir, nodes, duration = await env.submit()

    assert (job_id, nodes, duration) == ("396254", 2, 1800)
    assert (log_path, err_path, out_dir) == (
        f"{BASE}.out/log-396254.out",
        f"{BASE}.out/log-396254.err",
        f"{BASE}.out/396254",
    )
    # Lux's own token for compute and for its project; Frontier's Globus pair.
    assert env.iri_calls == [("lux", "lux-token")]
    assert env.introspected == ["lux-token"]
    [(cluster, tokens)] = env.globus_calls
    assert cluster == "lux"
    assert (tokens.transfer, tokens.https) == ("fr-transfer", "fr-https")

    [(spec, name)] = env.iri.submitted
    assert name == "vista-lux-demo"
    assert spec["attributes"] == {
        "queue_name": "batch",  # Lux's partition
        "account": "xyz789",  # the token's project; no Lux account setting
        "duration": 1800,
    }
    assert spec["resources"] == {
        "node_count": 2,
        "process_count": 8,
        "processes_per_node": 4,
        "cpu_cores_per_process": None,
        "gpu_cores_per_process": 1,  # gpus_per_node / processes_per_node
        "exclusive_node_use": True,
    }
    assert spec["directory"] == f"{BASE}.researcher.jobs"
    assert spec["stdout_path"] == f"{BASE}.out/log-%j.out"
    src = f"{BASE}.researcher.jobs/lux-demo/src"
    assert spec["environment"]["RUN_DIR_Lux"] == src
    assert spec["environment"]["VISTA_JOB_DIR"] == f"{BASE}.out/lux-demo"
    assert spec["environment"]["FOO"] == "bar baz"


async def test_sources_go_up_through_frontiers_collection(env):
    await env.submit()
    src = f"{BASE}.researcher.jobs/lux-demo/src"
    assert env.globus.mkdir_p_calls == [
        ("frontier-collection", src, "/lustre/orion/abc123/proj-shared")
    ]
    # The job and setup scripts are inlined into the job, never uploaded.
    assert [path for _, path in env.globus.uploads] == [f"{src}/run.py"]
    assert {c for c, _ in env.globus.uploads} == {"frontier-collection"}


async def test_setup_runs_in_the_allocation_after_the_prefix(env):
    """No login node any more: setup_lux.sh sees VISTA_OUT, HOME and the proxy, in the job."""
    await env.submit()
    [(spec, _)] = env.iri.submitted
    body = spec["arguments"][2]
    setup = "set -euo pipefail\necho setup-ran\n"
    order = [
        body.index("umask 002"),
        body.index("chgrp xyz789"),
        body.index("export VISTA_OUT="),
        body.index('export HOME="${HOME:-$VISTA_OUT}"'),
        body.index('export https_proxy="http://proxy.ccs.ornl.gov:3128"'),
        body.index(f"bash -lc {shlex.quote(setup)}"),
        body.index("set -- a b"),
        body.index('echo "run $@"'),
    ]
    assert order == sorted(order)
    assert "VISTA: setup_lux.sh failed with exit code" in body


async def test_without_a_partition_nothing_is_contacted(env, tmp_path, monkeypatch):
    lux = {**LUX_DEFAULTS, "iri": {"environment": {}}}
    other = Env(tmp_path / "other", monkeypatch)
    other.jobs(lux=lux)
    with pytest.raises(ValueError, match="names no lux partition"):
        await other.submit()
    assert (
        other.iri_calls == [] and other.globus_calls == [] and other.introspected == []
    )


async def test_an_explicit_queue_name_is_the_partition_when_none_is_set(
    tmp_path, monkeypatch
):
    e = Env(tmp_path, monkeypatch)
    e.jobs(lux={**LUX_DEFAULTS, "iri": {"queue_name": "extended", "environment": {}}})
    await e.submit()
    [(spec, _)] = e.iri.submitted
    assert spec["attributes"]["queue_name"] == "extended"


async def test_gpus_that_do_not_divide_among_processes_are_refused(
    tmp_path, monkeypatch
):
    e = Env(tmp_path, monkeypatch)
    resources = {**LUX_DEFAULTS["resources"], "processes_per_node": 3}
    e.jobs(lux={**LUX_DEFAULTS, "resources": resources})
    with pytest.raises(ValueError, match="must be a multiple of processes_per_node"):
        await e.submit()
    assert e.iri.submitted == []


async def test_no_lux_token_is_refused_before_anything_moves(env):
    cfg = CFG.model_copy(update={"lux_s3m_token": None})
    with pytest.raises(ToolError, match="No S3M token configured for 'lux'"):
        await env.submit(cfg)
    assert env.globus.uploads == [] and env.iri.submitted == []


async def test_no_frontier_globus_connection_names_lux(env):
    cfg = CFG.model_copy(
        update={"frontier_globus_token": None, "frontier_globus_https_token": None}
    )
    with pytest.raises(
        ToolError, match=r"not connected for Lux \(Lux uses Frontier's connection\)"
    ):
        await env.submit(cfg)
    assert env.iri.submitted == []


async def test_unset_remote_dir_is_refused_before_anything_is_contacted(env):
    with pytest.raises(ToolError, match="No Lux remote directory"):
        await env.submit(UserConfig(lux_s3m_token="lux-token"))
    assert env.introspected == [] and env.globus_calls == []


async def test_missing_lux_section_is_rejected(tmp_path, monkeypatch):
    e = Env(tmp_path, monkeypatch)
    root = tmp_path / "hpc_jobs"
    job = _write_job(root)
    (job / "cluster_defaults.json").write_text(
        json.dumps({"frontier": {}}), encoding="utf-8"
    )
    monkeypatch.setattr(settings, "local_hpc_jobs_dir", root)
    monkeypatch.setattr(m, "AVAILABLE_JOBS", m.get_available_jobs())
    with pytest.raises(ValueError, match='no "lux" section'):
        await e.submit()


def test_a_lux_token_alone_makes_lux_the_default():
    assert m._default_cluster(UserConfig(lux_s3m_token="t")) == "lux"
    with pytest.raises(ToolError, match="Multiple HPC clusters"):
        m._default_cluster(UserConfig(lux_s3m_token="t", frontier_s3m_token="f"))


# ---------------------------------------------------------------- status, outputs, cancel


async def test_status_reads_logs_and_outputs_through_frontiers_collection(
    env, tmp_path
):
    env.iri._status = {"state": "FAILED", "exit_code": 7}
    env.globus.files[f"{BASE}.out/log-396254.out"] = b"setup-ran\nrun a b\n"
    env.globus.files[f"{BASE}.out/log-396254.err"] = b"boom\n"
    env.globus.ls_entries[f"{BASE}.out/396254"] = [
        {
            "name": "result.json",
            "type": "file",
            "path": f"{BASE}.out/396254/result.json",
        }
    ]
    text = await m._get_job_status(CFG, tmp_path, "396254", cluster="lux")
    assert text.startswith("JOB_ID=396254\nCLUSTER=lux\nSTATE=FAILED\nEXIT_CODE=7")
    assert "run a b" in text and "boom" in text
    assert env.iri_calls == [("lux", "lux-token")]
    assert {c for c, _ in env.globus_calls} == {"lux"}


async def test_outputs_come_through_frontiers_collection(env, tmp_path):
    env.globus.files[f"{BASE}.out/396254/result.json"] = b"{}"
    text = await m._get_olcf_job_outputs(
        CFG, tmp_path, "396254", ["result.json"], cluster="lux"
    )
    assert text.splitlines()[1:] == ["/mnt/data/output/396254/result.json"]
    assert env.globus.downloads == [
        ("frontier-collection", f"{BASE}.out/396254/result.json")
    ]


async def test_cancel_uses_the_lux_token(env):
    from types import SimpleNamespace

    ctx = SimpleNamespace(
        request_context=SimpleNamespace(
            meta=SimpleNamespace(vista={"user": CFG.model_dump(), "project_paths": {}})
        )
    )
    text = await m.cancel_hpc_job(ctx, "396254", "lux")
    assert text == "Cancellation requested for job 396254 on lux."
    assert env.iri.cancelled == ["396254"]
    assert env.iri_calls == [("lux", "lux-token")]
