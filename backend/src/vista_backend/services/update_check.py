"""
Whether a newer VISTA release exists on GitHub (openspec change `in-app-updates`).

The installed version is `Settings.version`: the release tag for a release
package, or one of the shapes a build that is not a release carries, which
never checks (`is_dev_build`). Releases are `Genesis-VISTA/vista`'s published
GitHub releases, compared by version precedence (`packaging.version`, which
orders `0.2.2rc1` before `0.2.2`).

`check` is one lookup, given a `Fetch` so tests serve their own answers.
`UpdateChecker` decides when to look: on start, then 24 hours after the last
success (hourly after a failure), never while the researcher has checks off.
Nothing here raises to its caller: a check that fails is logged and reports
no update.
"""

import asyncio
import logging
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from packaging.version import InvalidVersion, Version
from sqlmodel.ext.asyncio.session import AsyncSession

from ..config import settings
from ..db.db import get_engine
from . import local_settings

log = logging.getLogger(__name__)

Fetch = Callable[[str], Awaitable[Any]]
"""GET a URL and return its parsed JSON body; raise `CheckFailed` otherwise."""

CHECK_PERIOD = timedelta(hours=24)
""" How long after a successful check the next one is due. """

RETRY_PERIOD = timedelta(hours=1)
"""
How long after a failed check the next is tried, and how often the schedule
looks again while checks are off (so turning them on needs no restart).
"""


class CheckFailed(Exception):
    """GitHub could not be reached, refused, or said something unreadable."""


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------


def parse_version(text: str) -> Version | None:
    """
    A version as a tag or `VERSION` file spells it: `v0.2.3`, `0.2.2-rc1` and
    `0.2.2rc1` all parse, the last two as the same version. `None` otherwise.
    """
    try:
        return Version(text.strip())
    except InvalidVersion:
        return None


def is_dev_build(version: str) -> bool:
    """
    True for a version no release carries, which is never checked for updates:
    `dev` on a checkout, `0.2.0+3.g1a2b3c4` from a local package build, anything
    ending `-dirty` from an uncommitted tree, `unknown` from a package with no
    VERSION file.
    """
    parsed = parse_version(version)
    return parsed is None or parsed.local is not None or parsed.is_devrelease


def _tag_version(tag: str) -> str:
    """A tag as the installer and asset names spell its version: `v0.2.2-rc1` → `0.2.2-rc1`."""
    tag = tag.strip()
    return tag[1:] if tag[:1] in ("v", "V") else tag


# ---------------------------------------------------------------------------
# One check
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Release:
    """A published release newer than the installed version."""

    version: str
    """ As the tag spells it, without the `v`: `0.2.5`, `0.2.6-rc1`. """
    notes_url: str | None
    prerelease: bool
    assets: tuple[dict[str, Any], ...] = ()
    """ GitHub's asset objects (`name`, `size`, `digest`, `browser_download_url`). """
    data_reset: bool | None = None
    """
    Whether installing it will ask to reset local data (the installed version is
    older than its `update.json`'s `data_compatible_from`); `None` when the
    release does not say.
    """

    def asset(self, name: str) -> dict[str, Any] | None:
        return next((a for a in self.assets if a.get("name") == name), None)


@dataclass(frozen=True)
class CheckResult:
    ok: bool
    """ Whether GitHub answered; a failed check is retried sooner. """
    latest: Release | None = None
    """ The newest release that is newer than the installed one, if any. """


async def check(
    current: str,
    *,
    prereleases: bool,
    fetch: Fetch,
    api_base: str | None = None,
    repo: str | None = None,
) -> CheckResult:
    """
    Look up the newest published release and say whether it is newer than
    `current`. Stable releases come from `releases/latest`, which GitHub never
    answers with a draft or prerelease; with `prereleases`, the last 20 releases
    are read and drafts left out. Never raises.
    """
    installed = parse_version(current)
    if installed is None or is_dev_build(current):
        return CheckResult(ok=True)
    base = f"{(api_base or settings.updates.github_api).rstrip('/')}/repos/{repo or settings.updates.repo}"
    try:
        if prereleases:
            body = await fetch(f"{base}/releases?per_page=20")
            if not isinstance(body, list):
                raise CheckFailed("the releases list is not a list")
            candidates = [r for r in body if isinstance(r, dict)]
        else:
            body = await fetch(f"{base}/releases/latest")
            if not isinstance(body, dict):
                raise CheckFailed("the latest release is not an object")
            candidates = [body]
        newest = _newest(candidates, prereleases=prereleases)
    except Exception as exc:  # noqa: BLE001 - a check never breaks VISTA
        log.warning("Update check failed: %s", exc)
        return CheckResult(ok=False)

    if newest is None or newest[0] <= Version(installed.public):
        return CheckResult(ok=True)
    _, raw = newest
    notes_url, assets = raw.get("html_url"), raw.get("assets")
    release = Release(
        version=_tag_version(raw["tag_name"]),
        notes_url=notes_url if isinstance(notes_url, str) else None,
        prerelease=bool(raw.get("prerelease")),
        assets=tuple(a for a in assets if isinstance(a, dict))
        if isinstance(assets, list)
        else (),
    )
    release = replace(release, data_reset=await _data_reset(release, installed, fetch))
    log.info("VISTA %s is available (installed: %s).", release.version, current)
    return CheckResult(ok=True, latest=release)


def _newest(
    releases: list[dict[str, Any]], *, prereleases: bool
) -> tuple[Version, dict[str, Any]] | None:
    """The highest-versioned release that may be offered, with its parsed version."""
    best: tuple[Version, dict[str, Any]] | None = None
    for raw in releases:
        if raw.get("draft") or (raw.get("prerelease") and not prereleases):
            continue
        tag = raw.get("tag_name")
        if not isinstance(tag, str) or is_dev_build(tag):
            continue
        version = parse_version(tag)
        if version is not None and (best is None or version > best[0]):
            best = (version, raw)
    return best


async def _data_reset(
    release: Release, installed: Version, fetch: Fetch
) -> bool | None:
    """
    Read the release's `update.json` for `data_compatible_from`. Releases made
    before it existed have none, and any trouble reading it is the same as
    none: the confirm dialog then says a reset may be asked for.
    """
    asset = release.asset("update.json")
    url = asset.get("browser_download_url") if asset else None
    if not isinstance(url, str):
        return None
    try:
        body = await fetch(url)
        oldest = parse_version(str(body["data_compatible_from"]))
    except Exception as exc:  # noqa: BLE001
        log.info("Could not read update.json for %s: %s", release.version, exc)
        return None
    if oldest is None:
        log.info(
            "update.json for %s has an unreadable data_compatible_from", release.version
        )
        return None
    return Version(installed.public) < oldest


def github_fetch(
    *, user_agent: str, transport: httpx.AsyncBaseTransport | None = None
) -> Fetch:
    """
    The real `Fetch`: one GET, redirects followed (release assets redirect to
    GitHub's object store), nothing sent but a `User-Agent` naming VISTA.
    """

    async def fetch(url: str) -> Any:
        async with httpx.AsyncClient(
            transport=transport,
            timeout=15.0,
            follow_redirects=True,
            headers={
                "User-Agent": user_agent,
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        ) as client:
            try:
                response = await client.get(url)
            except httpx.HTTPError as exc:
                raise CheckFailed(f"GET {url}: {exc!r}") from exc
        if response.status_code != 200:
            # 403 and 429 are GitHub's rate limit (60 an hour per address).
            raise CheckFailed(f"GET {url}: HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise CheckFailed(f"GET {url}: not JSON") from exc

    return fetch


def install_command(
    version: str, *, platform: str = sys.platform, repo: str | None = None
) -> str:
    """
    The release's own one-line installer, pinned to `version`, for a researcher
    using VISTA in a browser, where the window cannot restart it. Pasted into a
    terminal on macOS and Linux, into PowerShell on Windows.
    """
    url = f"https://github.com/{repo or settings.updates.repo}/releases/download/v{version}"
    if platform.startswith("win"):
        return (
            f'$env:VISTA_INSTALL_VERSION="{version}"; '
            f'powershell -ExecutionPolicy Bypass -c "irm {url}/install.ps1 | iex"'
        )
    return f"curl -fsSL {url}/install.sh | bash -s -- --version {version}"


# ---------------------------------------------------------------------------
# The schedule
# ---------------------------------------------------------------------------


@dataclass
class UpdateStatus:
    """What the last successful check in this process found."""

    checked: bool = False
    latest: Release | None = None
    prereleases: bool = False
    """ Whether prereleases were considered, so a switch since is noticed. """


def _parse_time(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


@dataclass
class UpdateChecker:
    """
    Runs `check` on its schedule and keeps the result in memory, with the time
    of the last success on the settings row so the schedule survives a restart.
    One per process (`update_checker`); tests make their own.
    """

    session_factory: Callable[[], AsyncSession] = field(
        default=lambda: AsyncSession(get_engine())
    )
    fetch: Fetch | None = None
    """ `None` builds `github_fetch` for each check. """
    version: str | None = None
    """ The installed version; `None` reads `Settings.version`. """
    now: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    status: UpdateStatus = field(default_factory=UpdateStatus)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def installed(self) -> str:
        return self.version if self.version is not None else settings.version

    def _fetch(self) -> Fetch:
        return self.fetch or github_fetch(user_agent=f"VISTA/{self.installed}")

    async def check_now(self) -> bool | None:
        """
        One check, if this install may check: `True` when GitHub answered,
        `False` when it could not be asked, `None` when checks are off or this is
        a development build (and nothing was requested).
        """
        if is_dev_build(self.installed):
            return None
        async with self._lock:
            async with self.session_factory() as db:
                row = await local_settings.get(db)
                enabled, prereleases = row.update_checks, row.update_prereleases
            if not enabled:
                self.status = UpdateStatus()
                return None
            result = await check(
                self.installed, prereleases=prereleases, fetch=self._fetch()
            )
            if not result.ok:
                return False
            self.status = UpdateStatus(
                checked=True, latest=result.latest, prereleases=prereleases
            )
            async with self.session_factory() as db:
                row = await local_settings.get(db)
                row.update_checked_at = self.now().isoformat()
                db.add(row)
                await db.commit()
            return True

    async def step(self) -> timedelta:
        """One turn of the schedule: check if a check is due, and say how long until the next."""
        async with self.session_factory() as db:
            row = await local_settings.get(db)
            enabled, last = row.update_checks, _parse_time(row.update_checked_at)
        if not enabled:
            self.status = UpdateStatus()
            return RETRY_PERIOD
        now = self.now()
        # A new process always checks once: what the last one found is gone.
        if self.status.checked and last is not None and now < last + CHECK_PERIOD:
            return max(last + CHECK_PERIOD - now, timedelta(minutes=1))
        return CHECK_PERIOD if await self.check_now() else RETRY_PERIOD

    async def run(self, stop: asyncio.Event | None = None) -> None:
        """The schedule, until `stop` is set or the task is cancelled. Never raises."""
        if is_dev_build(self.installed):
            log.info("Update checks are off for development build %s.", self.installed)
            return
        while stop is None or not stop.is_set():
            try:
                delay = await self.step()
            except Exception:  # noqa: BLE001 - e.g. the database is closing
                log.exception("Update check schedule failed; trying again later.")
                delay = RETRY_PERIOD
            await self.sleep(delay.total_seconds())


update_checker = UpdateChecker()
"""The process's checker: started by the lifespan, read by `GET /version`."""
