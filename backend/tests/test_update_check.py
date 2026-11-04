"""
`services/update_check.py`: whether a newer release exists, and when to ask.

`check` runs against a fake `Fetch` (a dict of URL → body), the real
`github_fetch` against `httpx.MockTransport`, and the schedule against a fake
clock whose `sleep` moves time on.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from vista_backend.config import settings
from vista_backend.db.schemas import LocalSettingsUpdate
from vista_backend.services import local_settings
from vista_backend.services.update_check import (
    CHECK_PERIOD,
    CheckFailed,
    UpdateChecker,
    check,
    github_fetch,
    install_command,
    is_dev_build,
)

API = "https://api.test"
LATEST = f"{API}/repos/Genesis-VISTA/vista/releases/latest"
LIST = f"{API}/repos/Genesis-VISTA/vista/releases?per_page=20"
UPDATE_JSON = "https://dl.test/v0.2.5/update.json"


def release(tag, *, prerelease=False, draft=False, update_json=False):
    assets = []
    if update_json:
        assets.append({"name": "update.json", "browser_download_url": UPDATE_JSON})
    return {
        "tag_name": tag,
        "html_url": f"https://github.com/Genesis-VISTA/vista/releases/tag/{tag}",
        "prerelease": prerelease,
        "draft": draft,
        "assets": assets,
    }


class FakeFetch:
    """URL → body; a body that is an exception is raised. Counts requests."""

    def __init__(self, answers):
        self.answers = answers
        self.urls: list[str] = []

    async def __call__(self, url):
        self.urls.append(url)
        if url not in self.answers:
            raise CheckFailed(f"GET {url}: HTTP 404")
        answer = self.answers[url]
        if isinstance(answer, Exception):
            raise answer
        return answer


async def run(current, answers, *, prereleases=False):
    return await check(
        current,
        prereleases=prereleases,
        fetch=FakeFetch(answers),
        api_base=API,
        repo="Genesis-VISTA/vista",
    )


# ---------------------------------------------------------------------------
# One check
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_a_newer_stable_release_is_offered():
    result = await run("0.2.3", {LATEST: release("v0.2.5")})

    assert result.ok
    assert result.latest is not None
    assert result.latest.version == "0.2.5"
    assert result.latest.notes_url == (
        "https://github.com/Genesis-VISTA/vista/releases/tag/v0.2.5"
    )
    assert result.latest.data_reset is None  # no update.json


@pytest.mark.anyio
async def test_the_same_version_is_no_update():
    result = await run("0.2.5", {LATEST: release("v0.2.5")})
    assert result.ok and result.latest is None


@pytest.mark.anyio
async def test_an_older_release_is_no_update():
    result = await run("0.2.6", {LATEST: release("v0.2.5")})
    assert result.ok and result.latest is None


@pytest.mark.anyio
async def test_a_release_candidate_is_older_than_its_release():
    result = await run("0.2.2-rc1", {LATEST: release("v0.2.2")})
    assert result.latest is not None and result.latest.version == "0.2.2"


@pytest.mark.anyio
async def test_a_prerelease_is_hidden_unless_opted_in():
    answers = {
        LATEST: release("v0.2.5"),
        LIST: [
            release("v0.2.6-rc1", prerelease=True),
            release("v0.2.7", draft=True),
            release("v0.2.5"),
        ],
    }

    hidden = await run("0.2.5", answers)
    assert hidden.ok and hidden.latest is None

    shown = await run("0.2.5", answers, prereleases=True)
    assert shown.latest is not None
    assert shown.latest.version == "0.2.6-rc1"  # the draft is never offered
    assert shown.latest.prerelease


@pytest.mark.anyio
async def test_opted_in_a_newer_stable_beats_an_older_rc():
    answers = {
        LIST: [release("v0.2.6-rc1", prerelease=True), release("v0.2.6")],
    }
    result = await run("0.2.5", answers, prereleases=True)
    assert result.latest is not None and result.latest.version == "0.2.6"


@pytest.mark.anyio
async def test_malformed_release_fields_are_ignored():
    body = release("v0.2.5") | {"assets": 7, "html_url": None}
    result = await run("0.2.3", {LATEST: body})
    assert result.latest is not None
    assert result.latest.assets == () and result.latest.notes_url is None


@pytest.mark.anyio
async def test_unparseable_tags_are_skipped():
    answers = {LIST: [release("nightly"), release("v0.2.4"), {"no": "tag"}]}
    result = await run("0.2.3", answers, prereleases=True)
    assert result.latest is not None and result.latest.version == "0.2.4"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("installed", "compatible_from", "reset"),
    [("0.2.3", "v0.2.3", False), ("0.2.2", "v0.2.3", True), ("0.2.4", "0.2.3", False)],
)
async def test_update_json_says_whether_data_resets(installed, compatible_from, reset):
    answers = {
        LATEST: release("v0.2.5", update_json=True),
        UPDATE_JSON: {"data_compatible_from": compatible_from},
    }
    result = await run(installed, answers)
    assert result.latest is not None and result.latest.data_reset is reset


@pytest.mark.anyio
@pytest.mark.parametrize(
    "body",
    [CheckFailed("HTTP 404"), {"other": 1}, {"data_compatible_from": "soon"}, []],
)
async def test_an_unreadable_update_json_is_unknown_not_a_failure(body):
    answers = {LATEST: release("v0.2.5", update_json=True), UPDATE_JSON: body}
    result = await run("0.2.3", answers)
    assert result.ok
    assert result.latest is not None and result.latest.data_reset is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    "answer",
    [CheckFailed("HTTP 403"), CheckFailed("HTTP 429"), ValueError("boom"), ["a list"]],
)
async def test_a_failed_check_is_no_update_and_logged(answer, caplog):
    with caplog.at_level(logging.WARNING, logger="vista_backend.services.update_check"):
        result = await run("0.2.3", {LATEST: answer})

    assert not result.ok
    assert result.latest is None
    assert "Update check failed" in caplog.text


@pytest.mark.anyio
async def test_a_dev_build_asks_nothing():
    fetch = FakeFetch({LATEST: release("v0.2.5")})
    for version in ("dev", "0.2.4+3.gabc", "0.2.4-dirty"):
        result = await check(version, prereleases=False, fetch=fetch, api_base=API)
        assert result.ok and result.latest is None
    assert fetch.urls == []


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
        "0.2.4.dev1",
    ],
)
def test_other_versions_are_dev_builds(version):
    assert is_dev_build(version)


def test_install_command_pins_the_release():
    assert install_command("0.2.5", platform="darwin", repo="Genesis-VISTA/vista") == (
        "curl -fsSL https://github.com/Genesis-VISTA/vista/releases/download/v0.2.5/"
        "install.sh | bash -s -- --version 0.2.5"
    )
    windows = install_command("0.2.6-rc1", platform="win32", repo="Genesis-VISTA/vista")
    assert windows.startswith('$env:VISTA_INSTALL_VERSION="0.2.6-rc1"; ')
    assert "releases/download/v0.2.6-rc1/install.ps1 | iex" in windows


# ---------------------------------------------------------------------------
# The real fetch
# ---------------------------------------------------------------------------


def _fetch_answering(handler):
    return github_fetch(
        user_agent="VISTA/0.2.3", transport=httpx.MockTransport(handler)
    )


@pytest.mark.anyio
async def test_github_fetch_sends_only_a_user_agent_and_reads_json():
    seen = {}

    def handler(request):
        seen.update(request.headers)
        return httpx.Response(200, json={"tag_name": "v0.2.5"})

    assert await _fetch_answering(handler)(LATEST) == {"tag_name": "v0.2.5"}
    assert seen["user-agent"] == "VISTA/0.2.3"
    assert "authorization" not in seen


@pytest.mark.anyio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(403, json={"message": "API rate limit exceeded"}),
        httpx.Response(429),
        httpx.Response(200, text="<html>not json</html>"),
    ],
)
async def test_github_fetch_refuses_what_it_cannot_use(response):
    with pytest.raises(CheckFailed):
        await _fetch_answering(lambda _: response)(LATEST)


@pytest.mark.anyio
async def test_github_fetch_turns_a_network_error_into_a_failed_check():
    def handler(request):
        raise httpx.ConnectError("offline", request=request)

    with pytest.raises(CheckFailed):
        await _fetch_answering(handler)(LATEST)


# ---------------------------------------------------------------------------
# The schedule
# ---------------------------------------------------------------------------


class FakeClock:
    def __init__(self):
        self.t = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)
        self.start = self.t
        self.sleeps = 0

    def now(self):
        return self.t

    def until(self, hours, stop):
        async def sleep(seconds):
            self.sleeps += 1
            self.t += timedelta(seconds=seconds)
            if self.t - self.start >= timedelta(hours=hours):
                stop.set()
            await asyncio.sleep(0)

        return sleep


def scheduled_url():
    """The URL the checker asks, from `Settings.updates` as the test has it."""
    return f"{settings.updates.github_api}/repos/Genesis-VISTA/vista/releases/latest"


def _checker(session, fetch, clock, stop, *, hours, version="0.2.5"):
    return UpdateChecker(
        session_factory=lambda: AsyncSession(session.bind),
        fetch=fetch,
        version=version,
        now=clock.now,
        sleep=clock.until(hours, stop),
    )


@pytest.mark.anyio
async def test_one_request_per_day_while_on(session):
    fetch = FakeFetch({scheduled_url(): release("v0.2.5")})
    clock, stop = FakeClock(), asyncio.Event()

    await _checker(session, fetch, clock, stop, hours=72).run(stop)

    # On start, then at 24h and 48h; the 72h one is past the end.
    assert len(fetch.urls) == 3
    row = await local_settings.get(session)
    assert row.update_checked_at == (clock.start + 2 * CHECK_PERIOD).isoformat()


@pytest.mark.anyio
async def test_no_request_while_off(session):
    await local_settings.update(session, LocalSettingsUpdate(update_checks=False))
    await session.commit()
    fetch = FakeFetch({})
    clock, stop = FakeClock(), asyncio.Event()

    await _checker(session, fetch, clock, stop, hours=72).run(stop)

    assert fetch.urls == []
    assert clock.sleeps > 0  # it kept looking, so turning checks on needs no restart


@pytest.mark.anyio
async def test_a_dev_build_never_schedules(session):
    fetch = FakeFetch({})
    clock, stop = FakeClock(), asyncio.Event()

    await _checker(session, fetch, clock, stop, hours=72, version="dev").run(stop)

    assert fetch.urls == [] and clock.sleeps == 0


@pytest.mark.anyio
async def test_a_failed_check_is_retried_hourly(session):
    fetch = FakeFetch({})  # every URL 404s
    clock, stop = FakeClock(), asyncio.Event()

    await _checker(session, fetch, clock, stop, hours=3).run(stop)

    assert len(fetch.urls) == 3  # 0h, 1h, 2h
    assert (await local_settings.get(session)).update_checked_at is None


@pytest.mark.anyio
async def test_a_restart_checks_once_then_waits_for_the_day(session):
    url = scheduled_url()
    clock = FakeClock()
    # The last process checked 20 hours ago.
    row = await local_settings.get(session)
    row.update_checked_at = (clock.t - timedelta(hours=20)).isoformat()
    session.add(row)
    await session.commit()

    fetch, stop = FakeFetch({url: release("v0.2.5")}), asyncio.Event()
    await _checker(session, fetch, clock, stop, hours=30).run(stop)

    assert len(fetch.urls) == 2  # on start, and 24h after it


@pytest.mark.anyio
async def test_check_now_while_off_asks_nothing_and_forgets(session):
    url = scheduled_url()
    fetch = FakeFetch({url: release("v0.2.6")})
    clock, stop = FakeClock(), asyncio.Event()
    checker = _checker(session, fetch, clock, stop, hours=1)

    assert await checker.check_now() is True
    assert checker.status.latest is not None

    await local_settings.update(session, LocalSettingsUpdate(update_checks=False))
    await session.commit()
    assert await checker.check_now() is None
    assert checker.status.latest is None
    assert len(fetch.urls) == 1
