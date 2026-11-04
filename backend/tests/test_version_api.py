"""`GET /version`: the installed version and commit, and whether it checks for updates."""

import pytest

from vista_backend.config import settings
from vista_backend.db.schemas import LocalSettingsUpdate
from vista_backend.services import local_settings
from vista_backend.services.update_check import is_dev_build


async def _get(session) -> dict:
    from harness import api_client

    with api_client(session) as (client, _):
        resp = await client.get("/version")
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.anyio
async def test_a_release_reports_its_version_and_commit(session, monkeypatch):
    monkeypatch.setattr(settings, "version", "0.2.5")
    monkeypatch.setattr(settings, "commit", "abc1234def")

    assert await _get(session) == {
        "version": "0.2.5",
        "commit": "abc1234def",
        "checks": "on",
    }


@pytest.mark.anyio
async def test_a_checkout_is_a_dev_build(session, monkeypatch):
    monkeypatch.setattr(settings, "version", "dev")
    monkeypatch.setattr(settings, "commit", "abc1234def")

    got = await _get(session)
    assert got["version"] == "dev"
    assert got["commit"] == "abc1234def"
    assert got["checks"] == "dev_build"


@pytest.mark.anyio
async def test_a_local_package_build_is_a_dev_build(session, monkeypatch):
    monkeypatch.setattr(settings, "version", "0.2.4+3.gabc")
    monkeypatch.setattr(settings, "commit", None)

    got = await _get(session)
    assert got["checks"] == "dev_build"
    assert got["commit"] is None


@pytest.mark.anyio
async def test_checks_turned_off_say_so(session, monkeypatch):
    monkeypatch.setattr(settings, "version", "0.2.5")
    await local_settings.update(session, LocalSettingsUpdate(update_checks=False))

    assert (await _get(session))["checks"] == "off"


@pytest.mark.anyio
async def test_an_empty_commit_is_none(session, monkeypatch):
    """A manifest without a commit exports an empty `VISTA_COMMIT`."""
    monkeypatch.setattr(settings, "version", "0.2.5")
    monkeypatch.setattr(settings, "commit", "")

    assert (await _get(session))["commit"] is None


@pytest.mark.parametrize(
    "version", ["0.2.3", "v0.2.3", "0.2.2-rc1", "0.2.2rc1", "1.0", " 0.2.3\n"]
)
def test_release_versions_are_not_dev_builds(version):
    assert not is_dev_build(version)


@pytest.mark.parametrize(
    "version",
    [
        "dev",
        "unknown",
        "",
        "0.2.4+3.gabc",
        "0.0.0+g1a2b3c4",
        "0.2.4+3.gabc-dirty",
        "0.2.4-dirty",
    ],
)
def test_other_versions_are_dev_builds(version):
    assert is_dev_build(version)
