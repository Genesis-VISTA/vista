"""
VISTA's IRI client (lib/iri.py) against the facilities' recorded responses.

Every request goes through `httpx.MockTransport`, which serves the files under
`fixtures/iri/` (recorded from S3M v2 and NERSC v1; see the README there), so
these check the paths, methods, argument placement and bearer the client sends
for each cluster at its API version, and what it makes of each response.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

import vista_mcp_server.submit_job_mcp as submit_job_mcp
from vista_mcp_server.config import AppSettings, settings
from vista_mcp_server.lib import iri
from vista_mcp_server.lib.iri import (
    IriClient,
    IriError,
    IriFileNotFound,
    IriTaskFailed,
    IriTaskTimeout,
    iri_target,
    normalize_job_status,
    wire_job_spec,
)
from vista_mcp_server.lib.user_config import UserConfig
from fakes import FakeGlobusClient

pytestmark = [pytest.mark.unit, pytest.mark.anyio]

FIXTURES = Path(__file__).parent / "fixtures" / "iri"
HOME = "/global/homes/r/researcher/vista-probe"


def fixture(rel: str):
    return json.loads((FIXTURES / rel).read_text(encoding="utf-8"))


class Facility:
    """
    A MockTransport handler: answers each request from `routes`, keyed by
    `(method, path)`, and records every request it sees.
    """

    def __init__(self, routes: dict[tuple[str, str], object] | None = None):
        self.routes = dict(routes or {})
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.routes.get((request.method, request.url.path))
        if answer is None:
            return httpx.Response(404, json={"detail": f"no route {request.url.path}"})
        if callable(answer):
            answer = answer(request)
        if isinstance(answer, httpx.Response):
            return answer
        return httpx.Response(200, json=answer)


class Clock:
    """A sleep that only records how long it was asked to wait."""

    def __init__(self):
        self.waits: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)


def client(
    cluster: str, facility: Facility, **target_changes
) -> tuple[IriClient, Clock]:
    clock = Clock()
    target = replace(iri_target(cluster), **target_changes)
    return IriClient(
        target, f"{cluster}-token", transport=httpx.MockTransport(facility), sleep=clock
    ), clock


SPEC = {
    "executable": "bash",
    "arguments": ["-l", "-c", "echo hi"],
    "resources": {"node_count": 1, "process_count": 1, "cpu_cores_per_process": None},
    "attributes": {
        "resource_id": "ignored",
        "queue_name": "batch",
        "account": "abc123",
        "duration": 300,
        "directory": "/lustre/orion/abc123/proj-shared",
        "stdout_path": "/lustre/orion/abc123/proj-shared/log-%j.out",
        "environment": {"RUN_DIR": "/x"},
        "pre_launch": None,
    },
}

CLUSTERS = {
    # cluster: (base URL, version, resource, recorded responses)
    "odo": ("https://amsc-open.s3m.olcf.ornl.gov", "v2", "odo", "olcf_v2"),
    "frontier": (
        "https://amsc-moderate.s3m.olcf.ornl.gov",
        "v2",
        "frontier",
        "olcf_v2",
    ),
    "lux": ("https://amsc-moderate.s3m.olcf.ornl.gov", "v2", "lux", "olcf_v2"),
    "perlmutter": (
        "https://api.iri.nersc.gov",
        "v1",
        "94351904-6dba-4c16-b5cd-fbd280d8615b",
        "nersc_v1",
    ),
}


# ------------------------------------------------------------------ targets and settings


@pytest.mark.parametrize("cluster", CLUSTERS)
def test_each_cluster_has_its_own_resource_at_its_version(cluster):
    base, version, resource, _ = CLUSTERS[cluster]
    t = iri_target(cluster)
    assert (t.base_url, t.api_version, t.compute_resource_id) == (
        base,
        version,
        resource,
    )
    assert (t.storage_resource_id is not None) == (cluster == "perlmutter")


def test_frontier_and_lux_share_an_enclave_but_never_a_resource():
    assert iri_target("frontier").base_url == iri_target("lux").base_url
    assert (
        iri_target("frontier").compute_resource_id
        != iri_target("lux").compute_resource_id
    )


@pytest.mark.parametrize(
    "name", ["VISTA_MCP_PERLMUTTER_IRI_URL", "VISTA_MCP_NERSC_IRI_URL"]
)
def test_perlmutter_url_also_reads_its_old_name(monkeypatch, name):
    monkeypatch.delenv("VISTA_MCP_PERLMUTTER_IRI_URL", raising=False)
    monkeypatch.delenv("VISTA_MCP_NERSC_IRI_URL", raising=False)
    monkeypatch.setenv(name, "https://nersc.example")
    assert AppSettings().perlmutter_iri_url == "https://nersc.example"


# ------------------------------------------------------------------ compute


@pytest.mark.parametrize("cluster", CLUSTERS)
async def test_compute_calls_go_to_the_clusters_resource_with_its_token(cluster):
    base, version, resource, recorded = CLUSTERS[cluster]
    prefix = f"/api/{version}/compute"
    facility = Facility(
        {
            ("POST", f"{prefix}/job/{resource}"): fixture(f"{recorded}/submit.json"),
            ("GET", f"{prefix}/status/{resource}/42"): fixture(
                f"{recorded}/failed_exit7.json"
            ),
            ("DELETE", f"{prefix}/cancel/{resource}/42"): httpx.Response(204),
        }
    )
    c, _ = client(cluster, facility)

    job_id = await c.submit_job(SPEC, name="vista-hello")
    status = await c.get_job_status("42")
    await c.cancel_job("42")

    assert job_id == fixture(f"{recorded}/submit.json")["id"]
    assert status == {"state": "FAILED", "exit_code": 7}
    assert [str(r.url).split("?")[0] for r in facility.requests] == [
        f"{base}{prefix}/job/{resource}",
        f"{base}{prefix}/status/{resource}/42",
        f"{base}{prefix}/cancel/{resource}/42",
    ]
    assert {r.headers["Authorization"] for r in facility.requests} == {
        f"Bearer {cluster}-token"
    }


async def test_submitted_body_is_the_iri_jobspec():
    facility = Facility(
        {("POST", "/api/v2/compute/job/lux"): fixture("olcf_v2/submit.json")}
    )
    c, _ = client("lux", facility)
    await c.submit_job(SPEC, name="vista-hello")

    body = json.loads(facility.requests[0].content)
    assert body == {
        "name": "vista-hello",
        "directory": "/lustre/orion/abc123/proj-shared",
        "stdout_path": "/lustre/orion/abc123/proj-shared/log-%j.out",
        "environment": {"RUN_DIR": "/x"},
        "executable": "bash",
        "arguments": ["-l", "-c", "echo hi"],
        "resources": {"node_count": 1, "process_count": 1},
        "attributes": {"queue_name": "batch", "account": "abc123", "duration": 300},
    }


def test_an_unknown_job_attribute_is_refused_here_not_dropped_there():
    with pytest.raises(ValueError, match="partition"):
        wire_job_spec({"attributes": {"partition": "batch"}}, name="x")


async def test_a_rejected_token_names_the_setting_to_renew():
    facility = Facility(
        {
            ("POST", "/api/v2/compute/job/lux"): httpx.Response(
                401, json={"detail": "expired"}
            ),
        }
    )
    c, _ = client("lux", facility)
    with pytest.raises(IriError, match=r"lux rejected your Lux S3M token \(HTTP 401\)"):
        await c.submit_job(SPEC, name="x")


async def test_an_unknown_resource_names_the_cluster_and_id():
    facility = Facility()  # every path 404s
    c, _ = client("frontier", facility)
    with pytest.raises(IriError, match=r"no compute resource 'frontier' for frontier"):
        await c.submit_job(SPEC, name="x")


async def test_a_submit_that_times_out_is_not_retried():
    def _slow(request):
        raise httpx.ReadTimeout("slow", request=request)

    facility = Facility({("POST", "/api/v2/compute/job/odo"): _slow})
    c, _ = client("odo", facility)
    with pytest.raises(IriError, match="may have been submitted: check its status"):
        await c.submit_job(SPEC, name="x")
    assert len(facility.requests) == 1


async def test_cancel_accepts_no_content():
    facility = Facility(
        {("DELETE", "/api/v2/compute/cancel/lux/7"): httpx.Response(204)}
    )
    c, _ = client("lux", facility)
    assert await c.cancel_job("7") is None


@pytest.mark.parametrize(
    ("rel", "expected"),
    [
        (
            "olcf_v2/submit.json",
            {"state": "QUEUED", "message": "Job submitted via S3M compute API"},
        ),
        ("olcf_v2/queued.json", {"state": "QUEUED"}),
        ("olcf_v2/failed_exit7.json", {"state": "FAILED", "exit_code": 7}),
        ("olcf_v2/canceled.json", {"state": "CANCELED", "exit_code": 0}),
        ("nersc_v1/queued.json", {"state": "QUEUED"}),
        ("nersc_v1/failed_exit7.json", {"state": "FAILED", "exit_code": 7}),
    ],
)
def test_recorded_statuses_normalize(rel, expected):
    """A queued job's exit code 0 and S3M's "None" message say nothing, so neither is kept."""
    assert normalize_job_status(fixture(rel)) == expected


# ------------------------------------------------------------------ filesystem tasks

STORAGE = "65b28619-c3b6-4942-8da1-044a3b3a2a9e"


def task_route(rel_or_task, task_id="t1"):
    """The submit answer and the task poll for one recorded task."""
    task = fixture(rel_or_task) if isinstance(rel_or_task, str) else rel_or_task
    return {
        "task_id": task_id,
        "task_uri": f"https://api.iri.nersc.gov/api/v1/task/{task_id}",
    }, task


def nersc(
    routes: dict[tuple, object], version: str = "v1"
) -> tuple[IriClient, Clock, Facility]:
    """
    A Perlmutter client at `version` over `routes`, keyed `(method, op)` for a
    filesystem operation or `("GET", "task", id)` for a task poll.
    """
    paths = {}
    for key, answer in routes.items():
        method, op = key[0], key[1]
        path = (
            f"/api/{version}/task/{key[2]}"
            if op == "task"
            else f"/api/{version}/filesystem/{op}/{STORAGE}"
        )
        paths[(method, path)] = answer
    facility = Facility(paths)
    c, clock = client("perlmutter", facility, api_version=version)
    return c, clock, facility


def fs(*entries):
    """Routes for (method, op, recorded task) triples, each its own task id."""
    routes = {}
    for i, (method, op, rel) in enumerate(entries):
        submitted, task = task_route(rel, task_id=f"t{i}")
        routes[(method, op)] = submitted
        routes[("GET", "task", f"t{i}")] = task
    return routes


async def test_v1_puts_arguments_where_nersc_wants_them(tmp_path):
    local = tmp_path / "hello.txt"
    local.write_text("hello\n", encoding="utf-8")
    c, _, facility = nersc(
        fs(
            ("POST", "mkdir", "nersc_v1/mkdir.json"),
            ("GET", "ls", "nersc_v1/ls.json"),
            ("GET", "head", "nersc_v1/head.json"),
            ("POST", "upload", "nersc_v1/upload.json"),
        )
    )
    await c.mkdir(f"{HOME}/sub/deeper")
    await c.ls(HOME)
    await c.head(f"{HOME}/hello.txt", lines=2)
    await c.upload(local, f"{HOME}/hello.txt")

    mkdir, _, ls, _, head, _, upload, _ = facility.requests
    assert json.loads(mkdir.content) == {"path": f"{HOME}/sub/deeper", "parent": True}
    assert dict(ls.url.params) == {"path": HOME, "recursive": "false"}
    assert dict(head.url.params) == {"path": f"{HOME}/hello.txt", "lines": "2"}
    assert dict(upload.url.params) == {"path": f"{HOME}/hello.txt"}
    assert b'name="file"; filename="hello.txt"' in upload.content
    assert {r.headers["Authorization"] for r in facility.requests} == {
        "Bearer perlmutter-token"
    }


async def test_v2_posts_every_argument_as_json():
    c, _, facility = nersc(
        fs(("POST", "ls", "nersc_v1/ls.json"), ("POST", "head", "nersc_v1/head.json")),
        version="v2",
    )
    await c.ls(HOME, recursive=True)
    await c.head(f"{HOME}/hello.txt", lines=2)

    ls, _, head, _ = facility.requests
    assert (ls.method, json.loads(ls.content)) == (
        "POST",
        {"path": HOME, "recursive": True},
    )
    assert (head.method, json.loads(head.content)) == (
        "POST",
        {"path": f"{HOME}/hello.txt", "lines": 2},
    )
    assert all(r.url.path.startswith("/api/v2/") for r in facility.requests)


async def test_a_task_is_polled_with_backoff_until_it_completes():
    pending = fixture("nersc_v1/task_pending.json")
    done = fixture("nersc_v1/mkdir.json")
    answers = iter([pending, {**pending, "status": "active"}, done])
    c, clock, facility = nersc(
        {
            ("POST", "mkdir"): fixture("nersc_v1/task_submitted.json"),
            ("GET", "task", done["id"]): lambda _r: httpx.Response(
                200, json=next(answers)
            ),
        }
    )
    await c.mkdir(f"{HOME}/sub/deeper")
    assert clock.waits == [0.5, 1.0]
    assert facility.requests[1].url.path == f"/api/v1/task/{done['id']}"


async def test_a_task_still_running_at_its_bound_times_out():
    c, clock, _ = nersc(
        {
            ("POST", "mkdir"): fixture("nersc_v1/task_submitted.json"),
            ("GET", "task", fixture("nersc_v1/task_pending.json")["id"]): fixture(
                "nersc_v1/task_pending.json"
            ),
        }
    )
    with pytest.raises(IriTaskTimeout, match="still pending after 120 s"):
        await c.mkdir(f"{HOME}/x")
    assert clock.waits[:5] == [0.5, 1.0, 2.0, 4.0, 5.0]
    assert sum(clock.waits) >= 120


@pytest.mark.parametrize(
    ("op", "rel"),
    [("head", "nersc_v1/head_missing.json"), ("ls", "nersc_v1/ls_missing.json")],
)
async def test_a_missing_path_is_file_not_found(op, rel):
    c, _, _ = nersc(fs(("GET", op, rel)))
    with pytest.raises(IriFileNotFound) as e:
        await getattr(c, op)(f"{HOME}/no-such")
    assert e.value.path == f"{HOME}/no-such"


async def test_any_other_failure_is_a_failed_task_with_its_reason():
    task = {
        **fixture("nersc_v1/mkdir.json"),
        "status": "failed",
        "result": {"error": "Error: 403: Permission denied"},
    }
    c, _, _ = nersc(fs(("POST", "mkdir", task)))
    with pytest.raises(IriTaskFailed, match="Permission denied"):
        await c.mkdir("/global/cfs/elsewhere")


async def test_ls_names_entries_under_the_path_asked_for():
    """NERSC answers with /global/u1/...; callers compare with the /global/homes/... they asked for."""
    c, _, _ = nersc(fs(("GET", "ls", "nersc_v1/ls_recursive.json")))
    entries = await c.ls(HOME, recursive=True)
    assert {e["path"]: (e["type"], e["size"]) for e in entries} == {
        f"{HOME}/sub": ("d", 512),
        f"{HOME}/sub/deeper": ("d", 512),
        f"{HOME}/sub/blob.bin": ("f", 1024),
        f"{HOME}/hello.txt": ("f", 31),
    }
    assert submit_job_mcp._flatten_ls_paths(entries, root=HOME) == [
        "sub/blob.bin",
        "hello.txt",
    ]


def _stat_of(size: int):
    task = fixture("nersc_v1/stat.json")
    return {**task, "result": {"output": {**task["result"]["output"], "size": size}}}


@pytest.mark.parametrize(
    ("rel", "expected"),
    [
        ("nersc_v1/download_text.json", b"hello from VISTA\nline 2\nline 3\n"),
        ("nersc_v1/download_binary.json", bytes(range(256)) * 4),
    ],
)
async def test_downloads_text_and_binary_byte_for_byte(tmp_path, rel, expected):
    c, _, _ = nersc(
        fs(("GET", "stat", _stat_of(len(expected))), ("GET", "download", rel))
    )
    local = tmp_path / "out"
    assert await c.download(f"{HOME}/file", local) == str(local)
    assert local.read_bytes() == expected


async def test_a_download_matching_its_size_neither_way_is_not_saved(tmp_path):
    c, _, _ = nersc(
        fs(
            ("GET", "stat", _stat_of(99)),
            ("GET", "download", "nersc_v1/download_text.json"),
        )
    )
    local = tmp_path / "out"
    with pytest.raises(
        IriError, match=r"matches its size \(99 bytes\) neither as text nor as base64"
    ):
        await c.download(f"{HOME}/hello.txt", local)
    assert not local.exists()


async def test_the_olcf_clusters_have_no_iri_storage():
    c, _ = client("frontier", Facility())
    with pytest.raises(IriError, match="go through Globus"):
        await c.ls("/lustre/orion/abc123")


# ------------------------------------------------------------------ status text from recorded responses


@pytest.fixture
def user_cfg() -> UserConfig:
    return UserConfig(
        odo_s3m_token="odo-token",
        frontier_s3m_token="frontier-token",
        nersc_iri_token="nersc-token",
        nersc_account="m0000",
        nersc_remote_dir="/global/homes/r/researcher/vista",
        odo_remote_dir="/fake/odo/vista",
        frontier_remote_dir="/fake/frontier/vista",
        globus_token="fake-transfer-refresh",
        globus_https_token="fake-https-refresh",
    )


def _serve(monkeypatch, facility: Facility) -> None:
    def make(cluster):
        async def _create(*, iri_token: str):
            return IriClient(
                iri_target(cluster),
                iri_token,
                transport=httpx.MockTransport(facility),
                sleep=Clock(),
            )

        return _create

    monkeypatch.setattr(submit_job_mcp, "create_iri_client", make("perlmutter"))
    monkeypatch.setattr(submit_job_mcp, "create_olcf_iri_client", make("frontier"))
    monkeypatch.setattr(submit_job_mcp, "create_odo_iri_client", make("odo"))


async def test_perlmutter_failed_exit_7_reads_failed_with_its_exit_code(
    monkeypatch, user_cfg
):
    resource = settings.perlmutter_iri_resource_id
    routes = {
        ("GET", f"/api/v1/compute/status/{resource}/1001"): fixture(
            "nersc_v1/failed_exit7.json"
        )
    }
    # Every log and listing task fails as a missing path would.
    missing = fixture("nersc_v1/head_missing.json")
    for op in ("head", "ls"):
        routes[("GET", f"/api/v1/filesystem/{op}/{STORAGE}")] = {
            "task_id": "m",
            "task_uri": "x",
        }
    routes[("GET", "/api/v1/task/m")] = missing
    _serve(monkeypatch, Facility(routes))

    text = await submit_job_mcp._get_perlmutter_job_status(user_cfg, "1001")
    head = text.split("\n\n")[0].splitlines()
    assert head == ["JOB_ID=1001", "CLUSTER=perlmutter", "STATE=FAILED", "EXIT_CODE=7"]


async def test_a_queued_olcf_job_reads_queued_with_no_exit_code_or_message(
    monkeypatch, user_cfg, tmp_path
):
    routes = {
        ("GET", "/api/v2/compute/status/frontier/2001"): fixture("olcf_v2/queued.json")
    }
    _serve(monkeypatch, Facility(routes))
    monkeypatch.setattr(
        submit_job_mcp, "create_globus_client", lambda **_: FakeGlobusClient()
    )

    text = await submit_job_mcp._get_olcf_job_status(
        user_cfg, tmp_path, "2001", cluster="frontier"
    )
    head = text.split("\n\n")[0].splitlines()
    assert head == ["JOB_ID=2001", "CLUSTER=frontier", "STATE=QUEUED"]
    assert "has not started yet" in text


def test_nothing_imports_amscrot():
    """Task 3.5: the private SDK stays installed until it is dropped, but nothing uses it."""
    src = Path(iri.__file__).parents[1]
    importers = [
        p.relative_to(src).as_posix()
        for p in src.rglob("*.py")
        if any(
            m in p.read_text(encoding="utf-8")
            for m in ("import amscrot", "from amscrot", "amsc_iri")
        )
    ]
    assert importers == []
