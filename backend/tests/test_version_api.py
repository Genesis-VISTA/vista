"""
`GET /version` and `POST /updates/check`: the installed version and commit,
whether it checks for updates, and what the last check found.

The update tests serve a fake GitHub in-process (`updates.github_api` pointed
at it, the real `github_fetch` over an ASGI transport), so the request shapes,
redirects and JSON handling are the ones a release really meets.
"""

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse, RedirectResponse
from sqlmodel.ext.asyncio.session import AsyncSession

from vista_backend.config import settings
from vista_backend.db.schemas import LocalSettingsUpdate
from vista_backend.services import local_settings
from vista_backend.services.update_check import (
    UpdateStatus,
    github_fetch,
    update_checker,
)

FAKE = "http://fake-github.test"


class FakeGitHub:
    """GitHub's two release endpoints and one asset, with request counting."""

    def __init__(self):
        self.latest = {"tag_name": "v0.2.5", "prerelease": False, "draft": False}
        self.releases: list[dict] = []
        self.update_json: dict | None = None
        self.status = 200
        self.requests: list[str] = []
        self.app = FastAPI()
        repo = "/repos/Genesis-VISTA/vista"

        @self.app.get(f"{repo}/releases/latest")
        async def latest():
            self.requests.append("latest")
            if self.status != 200:
                return JSONResponse({"message": "rate limited"}, self.status)
            body = dict(self.latest)
            body["html_url"] = f"https://github.com/x/releases/tag/{body['tag_name']}"
            if self.update_json is not None:
                body["assets"] = [
                    {
                        "name": "update.json",
                        "browser_download_url": f"{FAKE}/download/update.json",
                    }
                ]
            return body

        @self.app.get(f"{repo}/releases")
        async def releases(per_page: int = 30):
            self.requests.append("releases")
            return self.releases[:per_page]

        # Release assets redirect to an object store, as GitHub's do.
        @self.app.get("/download/update.json")
        async def download():
            return RedirectResponse(f"{FAKE}/objects/update.json", status_code=302)

        @self.app.get("/objects/update.json")
        async def update_json():
            return self.update_json


@pytest.fixture
def github(session, monkeypatch):
    fake = FakeGitHub()
    monkeypatch.setattr(settings.updates, "github_api", FAKE)
    monkeypatch.setattr(
        update_checker,
        "fetch",
        github_fetch(user_agent="VISTA/test", transport=httpx.ASGITransport(fake.app)),
    )
    monkeypatch.setattr(
        update_checker, "session_factory", lambda: AsyncSession(session.bind)
    )
    monkeypatch.setattr(update_checker, "status", UpdateStatus())
    monkeypatch.setattr(settings, "version", "0.2.3")
    return fake


async def _call(session, method="GET", path="/version") -> dict:
    from harness import api_client

    with api_client(session) as (client, _):
        resp = await client.request(method, path)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# The version
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_a_release_reports_its_version_and_commit(session, github, monkeypatch):
    monkeypatch.setattr(settings, "version", "0.2.5")
    monkeypatch.setattr(settings, "commit", "abc1234def")

    got = await _call(session)
    assert got["version"] == "0.2.5"
    assert got["commit"] == "abc1234def"
    assert got["checks"] == "on"
    assert got["latest"] is None  # nothing checked yet in this process
    assert github.requests == []  # GET /version never asks GitHub itself


@pytest.mark.anyio
async def test_a_checkout_is_a_dev_build(session, github, monkeypatch):
    monkeypatch.setattr(settings, "version", "dev")
    monkeypatch.setattr(settings, "commit", "abc1234def")

    got = await _call(session, "POST", "/updates/check")
    assert got["version"] == "dev"
    assert got["commit"] == "abc1234def"
    assert got["checks"] == "dev_build"
    assert github.requests == []


@pytest.mark.anyio
async def test_a_local_package_build_is_a_dev_build(session, github, monkeypatch):
    monkeypatch.setattr(settings, "version", "0.2.4+3.gabc")
    monkeypatch.setattr(settings, "commit", None)

    got = await _call(session, "POST", "/updates/check")
    assert got["checks"] == "dev_build"
    assert got["commit"] is None
    assert github.requests == []


@pytest.mark.anyio
async def test_an_empty_commit_is_none(session, github, monkeypatch):
    """A manifest without a commit exports an empty `VISTA_COMMIT`."""
    monkeypatch.setattr(settings, "commit", "")
    assert (await _call(session))["commit"] is None


# ---------------------------------------------------------------------------
# What a check found
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_check_now_offers_a_newer_release(session, github):
    github.update_json = {"data_compatible_from": "v0.2.4"}

    got = await _call(session, "POST", "/updates/check")

    assert got["latest"] == "0.2.5"
    assert got["notes_url"] == "https://github.com/x/releases/tag/v0.2.5"
    assert got["data_reset"] is True  # 0.2.3 is older than 0.2.4
    assert got["checked_at"] is not None
    # And GET /version keeps saying so without asking again.
    assert (await _call(session))["latest"] == "0.2.5"
    assert github.requests == ["latest"]


@pytest.mark.anyio
async def test_a_release_without_update_json_has_unknown_data_reset(session, github):
    got = await _call(session, "POST", "/updates/check")
    assert got["latest"] == "0.2.5"
    assert got["data_reset"] is None


@pytest.mark.anyio
async def test_already_current_is_no_update(session, github, monkeypatch):
    monkeypatch.setattr(settings, "version", "0.2.5")
    got = await _call(session, "POST", "/updates/check")
    assert got["latest"] is None
    assert got["checked_at"] is not None


@pytest.mark.anyio
@pytest.mark.parametrize("status", [403, 429])
async def test_a_rate_limited_check_is_silent(session, github, status):
    github.status = status
    got = await _call(session, "POST", "/updates/check")
    assert got["checks"] == "on"
    assert got["latest"] is None
    assert got["checked_at"] is None


@pytest.mark.anyio
async def test_checks_off_ask_nothing_and_offer_nothing(session, github):
    await _call(session, "POST", "/updates/check")
    await local_settings.update(session, LocalSettingsUpdate(update_checks=False))
    await session.commit()

    got = await _call(session, "POST", "/updates/check")

    assert got["checks"] == "off"
    assert got["latest"] is None and got["checked_at"] is None
    assert github.requests == ["latest"]  # only the check made while on


@pytest.mark.anyio
async def test_prereleases_come_from_the_list_once_opted_in(session, github):
    github.releases = [
        {"tag_name": "v0.2.6-rc1", "prerelease": True, "draft": False},
        {"tag_name": "v0.2.5", "prerelease": False, "draft": False},
    ]
    await local_settings.update(session, LocalSettingsUpdate(update_prereleases=True))
    await session.commit()

    got = await _call(session, "POST", "/updates/check")
    assert got["latest"] == "0.2.6-rc1"

    # Opting out again drops the RC at once, before any new check.
    await local_settings.update(session, LocalSettingsUpdate(update_prereleases=False))
    await session.commit()
    assert (await _call(session))["latest"] is None
