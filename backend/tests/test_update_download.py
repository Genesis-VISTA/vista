"""
`services/update_download.py`, `/updates/download` and `GET /activity`.

The release is served by `httpx.MockTransport` from bytes the test holds, with
GitHub's per-asset digests computed from them, so a tampered byte, a missing
digest or a wrong `.sha256` is one edit to the fake.
"""

import asyncio
import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest

from vista_backend.config import settings
from vista_backend.db.schemas import (
    CampaignRunTable,
    CampaignStepTable,
    HpcJobTable,
    ProjectTable,
)
from vista_backend.services import update_download
from vista_backend.services.update_check import Release, UpdateStatus, update_checker
from vista_backend.services.update_download import (
    INSTALL_FLOOR,
    UpdateDownloader,
    clean_cache,
    platform_name,
    required_assets,
)
from vista_backend.utils.misc import now_iso

VERSION = "0.2.5"
ARCHIVE, CHECKSUM, INSTALLER = required_assets(VERSION, "mac-arm64")
GB = 1024**3


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FakeRelease:
    """A release's files, served over a mock transport, counting requests."""

    def __init__(self):
        archive = b"vista package " * 50_000
        self.files = {
            ARCHIVE: archive,
            CHECKSUM: f"{sha(archive)}  {ARCHIVE}\n".encode(),
            INSTALLER: b"#!/bin/bash\necho install\n",
            "update.json": b'{"data_compatible_from": "v0.2.3"}',
        }
        self.digests = {name: sha(data) for name, data in self.files.items()}
        self.requests: list[str] = []

    def transport(self):
        def handler(request):
            name = request.url.path.rsplit("/", 1)[-1]
            self.requests.append(name)
            if name not in self.files:
                return httpx.Response(404)
            return httpx.Response(200, content=self.files[name])

        return httpx.MockTransport(handler)

    def release(self, *, drop_digest=None, drop_asset=None) -> Release:
        assets = []
        for name, data in self.files.items():
            if name == drop_asset:
                continue
            asset = {
                "name": name,
                "size": len(data),
                "browser_download_url": f"https://dl.test/v{VERSION}/{name}",
            }
            if name != drop_digest:
                asset["digest"] = f"sha256:{self.digests[name]}"
            assets.append(asset)
        return Release(
            version=VERSION, notes_url=None, prerelease=False, assets=tuple(assets)
        )


@pytest.fixture
def fake():
    return FakeRelease()


@pytest.fixture
def root(tmp_path):
    return tmp_path / "updates"


def downloader(fake, root, *, free=100 * GB):
    return UpdateDownloader(
        root=lambda: root,
        transport=fake.transport(),
        free_bytes=lambda _: free,
        platform="mac-arm64",
    )


async def download(fake, root, release=None, **kw):
    d = downloader(fake, root, **kw)
    d.start(release or fake.release())
    await d.wait()
    return d


# ---------------------------------------------------------------------------
# Download and verification
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_a_clean_download_is_ready_with_its_hashes(fake, root):
    d = await download(fake, root)

    assert d.status.state == "ready", d.status.error
    assert d.status.bytes == d.status.total == sum(map(len, fake.files.values()))
    folder = root / VERSION
    assert (folder / ARCHIVE).read_bytes() == fake.files[ARCHIVE]
    assert not list(folder.glob("*.part"))
    ready = json.loads((folder / "ready.json").read_text(encoding="utf-8"))
    assert ready == {"version": VERSION, "files": fake.digests}
    assert d.ready_folder(VERSION) == folder
    assert d.ready_folder("0.2.6") is None


@pytest.mark.anyio
@pytest.mark.parametrize("name", [ARCHIVE, INSTALLER, CHECKSUM])
async def test_a_tampered_file_is_deleted_and_the_update_stops(fake, root, name):
    release = fake.release()  # digests of the genuine bytes
    fake.files[name] = fake.files[name][:-1] + b"X"

    d = await download(fake, root, release)

    assert d.status.state == "failed"
    assert f"{name} failed verification" in (d.status.error or "")
    folder = root / VERSION
    assert not (folder / name).exists()
    assert not (folder / f"{name}.part").exists()
    assert not (folder / "ready.json").exists()


@pytest.mark.anyio
async def test_an_archive_that_does_not_match_its_sha256_file_is_deleted(fake, root):
    # GitHub's digests match what is served; the .sha256 itself names another hash.
    fake.files[CHECKSUM] = f"{'0' * 64}  {ARCHIVE}\n".encode()
    fake.digests[CHECKSUM] = sha(fake.files[CHECKSUM])

    d = await download(fake, root)

    assert d.status.state == "failed"
    assert "does not match its .sha256" in (d.status.error or "")
    assert not (root / VERSION / ARCHIVE).exists()
    assert not (root / VERSION / "ready.json").exists()


@pytest.mark.anyio
@pytest.mark.parametrize("name", [ARCHIVE, CHECKSUM, INSTALLER])
async def test_an_asset_without_a_digest_is_refused_before_downloading(
    fake, root, name
):
    d = await download(fake, root, fake.release(drop_digest=name))

    assert d.status.state == "failed"
    assert f"no digest for {name}" in (d.status.error or "")
    assert fake.requests == []


@pytest.mark.anyio
async def test_a_missing_asset_is_refused(fake, root):
    d = await download(fake, root, fake.release(drop_asset=INSTALLER))
    assert d.status.state == "failed"
    assert f"has no {INSTALLER}" in (d.status.error or "")


@pytest.mark.anyio
async def test_update_json_is_optional(fake, root):
    d = await download(fake, root, fake.release(drop_asset="update.json"))
    assert d.status.state == "ready", d.status.error


@pytest.mark.anyio
async def test_too_little_disk_names_the_space_needed(fake, root):
    d = await download(fake, root, free=3 * GB)

    assert d.status.state == "failed"
    needed = (sum(map(len, fake.files.values())) + INSTALL_FLOOR) / GB
    assert f"needs about {needed:.1f} GB free" in (d.status.error or "")
    assert "there is 3.0 GB" in (d.status.error or "")
    assert fake.requests == []


@pytest.mark.anyio
async def test_an_http_error_fails_with_the_reason(fake, root):
    del fake.files[INSTALLER]
    release = FakeRelease().release()  # still lists it

    d = await download(fake, root, release)

    assert d.status.state == "failed"
    assert "HTTP 404" in (d.status.error or "")


@pytest.mark.anyio
async def test_a_finished_download_is_reused_after_a_restart(fake, root):
    await download(fake, root)
    fake.requests.clear()

    d = await download(fake, root)  # a new process: nothing in memory

    assert d.status.state == "ready"
    assert fake.requests == []


@pytest.mark.anyio
async def test_a_changed_cached_file_is_downloaded_again(fake, root):
    await download(fake, root)
    (root / VERSION / ARCHIVE).write_bytes(b"changed")
    fake.requests.clear()

    d = await download(fake, root)

    assert d.status.state == "ready"
    assert ARCHIVE in fake.requests


@pytest.mark.anyio
async def test_a_newer_download_replaces_the_older_one(fake, root):
    (root / "0.2.4").mkdir(parents=True)
    (root / "last-failure.json").write_text("{}", encoding="utf-8")

    await download(fake, root)

    assert not (root / "0.2.4").exists()
    assert (root / "last-failure.json").exists()


@pytest.mark.anyio
async def test_starting_the_same_download_twice_runs_once(fake, root):
    d = downloader(fake, root)
    d.start(fake.release())
    d.start(fake.release())
    await d.wait()
    assert fake.requests.count(ARCHIVE) == 1


def test_platform_names():
    assert platform_name("darwin", "arm64") == "mac-arm64"
    assert platform_name("linux", "x86_64") == "linux-x86"
    assert platform_name("win32", "AMD64") == "win-x86"
    assert platform_name("darwin", "x86_64") is None
    assert required_assets("0.2.6-rc1", "win-x86") == (
        "vista-0.2.6-rc1-win-x86.zip",
        "vista-0.2.6-rc1-win-x86.zip.sha256",
        "install.ps1",
    )


# ---------------------------------------------------------------------------
# The cache on start
# ---------------------------------------------------------------------------


def test_the_running_version_cleans_its_own_and_older_caches(root):
    for name in ("0.2.4", "0.2.5", "0.2.6"):
        (root / name).mkdir(parents=True)
    (root / "last-failure.json").write_text("{}", encoding="utf-8")

    clean_cache("0.2.5", root)

    assert sorted(p.name for p in root.iterdir()) == ["0.2.6", "last-failure.json"]


def test_a_dev_build_cleans_nothing_versioned(root):
    (root / "0.2.5").mkdir(parents=True)
    clean_cache("dev", root)
    assert (root / "0.2.5").exists()


def test_no_cache_is_fine(root):
    clean_cache("0.2.5", root)  # no folder at all


# ---------------------------------------------------------------------------
# Over HTTP
# ---------------------------------------------------------------------------


@pytest.fixture
def offered(fake, root, monkeypatch):
    """0.2.3 installed, checks on, and the last check offered 0.2.5."""
    monkeypatch.setattr(settings, "version", "0.2.3")
    monkeypatch.setattr(
        update_checker,
        "status",
        UpdateStatus(checked=True, latest=fake.release(), prereleases=False),
    )
    d = downloader(fake, root)
    monkeypatch.setattr(update_download, "update_downloader", d)
    from vista_backend.api import updates

    monkeypatch.setattr(updates, "update_downloader", d)
    return d


@pytest.mark.anyio
async def test_downloading_the_offered_release(session, offered):
    from harness import api_client

    with api_client(session) as (client, _):
        resp = await client.post("/updates/download", json={"version": VERSION})
        assert resp.status_code == 200, resp.text
        assert resp.json()["state"] == "downloading"
        await offered.wait()
        got = (await client.get("/updates/download")).json()
    assert got["state"] == "ready"
    assert got["version"] == VERSION
    assert got["bytes"] == got["total"] > 0


@pytest.mark.anyio
async def test_a_version_not_on_offer_is_refused(session, offered, fake):
    from harness import api_client

    with api_client(session) as (client, _):
        resp = await client.post("/updates/download", json={"version": "0.2.9"})
    assert resp.status_code == 409
    assert fake.requests == []


@pytest.mark.anyio
async def test_nothing_is_downloaded_with_checks_off(session, offered, fake):
    from harness import api_client

    from vista_backend.db.schemas import LocalSettingsUpdate
    from vista_backend.services import local_settings

    await local_settings.update(session, LocalSettingsUpdate(update_checks=False))
    with api_client(session) as (client, _):
        resp = await client.post("/updates/download", json={"version": VERSION})
    assert resp.status_code == 409
    assert fake.requests == []


@pytest.mark.anyio
async def test_a_failed_download_is_reported(session, offered, fake):
    from harness import api_client

    fake.files[ARCHIVE] = b"tampered"
    with api_client(session) as (client, _):
        await client.post("/updates/download", json={"version": VERSION})
        await offered.wait()
        got = (await client.get("/updates/download")).json()
    assert got["state"] == "failed"
    assert "failed verification" in got["error"]


# ---------------------------------------------------------------------------
# GET /activity
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_activity_lists_runs_debates_and_watched_jobs(session, monkeypatch):
    from harness import api_client

    from vista_backend.api import debate, updates

    project = ProjectTable(name="activity-project")
    session.add(project)
    await session.flush()
    now = now_iso()
    run = CampaignRunTable(
        domain="splash",
        planner_skill="splash-planner",
        project_id=project.id,
        created_at=now,
        updated_at=now,
    )
    session.add(run)
    await session.flush()
    step = CampaignStepTable(
        run_id=run.id, cycle=0, kind="neutronics", created_at=now, updated_at=now
    )
    session.add(step)
    await session.flush()
    session.add(
        HpcJobTable(job_id="42", step_id=step.id, cluster="frontier", job_name="n")
    )
    session.add(
        HpcJobTable(job_id="43", step_id=step.id, cluster="odo", result_collected=True)
    )
    await session.flush()

    chat = SimpleNamespace(
        chat_session_id=project.id,
        project_id=project.id,
        pending={"p1"},
        finished=False,
    )
    done = SimpleNamespace(
        chat_session_id=project.id, project_id=project.id, pending=set(), finished=True
    )
    monkeypatch.setattr(updates.chat_run_registry, "active", lambda: [chat, done])
    debate_task = asyncio.create_task(asyncio.sleep(60))
    monkeypatch.setattr(debate, "_BACKGROUND", {debate_task})
    try:
        with api_client(session) as (client, _):
            resp = await client.get("/activity")
    finally:
        debate_task.cancel()
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "chat_runs": [
            {
                "chat_session_id": str(project.id),
                "project_id": str(project.id),
                "needs_you": True,
            }
        ],
        "debates": 1,
        "watched_jobs": [
            {
                "job_id": "42",
                "cluster": "frontier",
                "job_name": "n",
                "state": "submitted",
            }
        ],
    }


@pytest.mark.anyio
async def test_activity_when_idle(session):
    from harness import api_client

    with api_client(session) as (client, _):
        resp = await client.get("/activity")
    assert resp.json() == {"chat_runs": [], "debates": 0, "watched_jobs": []}
