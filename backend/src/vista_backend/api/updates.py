"""
`GET /version` and `/updates/*`: which VISTA this is, and whether a newer
release exists, for Settings > About (openspec change `in-app-updates`).
"""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from ..config import settings
from ..db.db import SessionDep
from ..services import local_settings
from ..services.update_check import install_command, is_dev_build, update_checker

router = APIRouter(tags=["updates"])


class VersionInfo(BaseModel):
    version: str
    """ The installed version, as the package's manifest and launcher carry it. """
    commit: str | None = None
    """ The commit it was built from, when known. """
    checks: Literal["on", "off", "dev_build"]
    """
    Whether this install checks for updates: `dev_build` for a build no release
    carries (never checked), otherwise the researcher's `update_checks`.
    """
    checked_at: str | None = None
    """ When the last successful check finished (ISO 8601, UTC), if checks are on. """
    latest: str | None = None
    """ A newer release's version, when the last check found one. """
    notes_url: str | None = None
    """ Its release notes on GitHub. """
    install_command: str | None = None
    """ Its one-line installer for this platform, for a browser, where the window cannot update. """
    data_reset: bool | None = None
    """
    Whether installing it will ask to reset local data; `None` when the release
    does not say (every release before `update.json` existed).
    """


async def _version_info(session: SessionDep) -> VersionInfo:
    row = await local_settings.get(session)
    info = VersionInfo(
        version=settings.version,
        commit=settings.commit or None,
        checks="dev_build"
        if is_dev_build(settings.version)
        else "on"
        if row.update_checks
        else "off",
    )
    if info.checks != "on":
        return info
    info.checked_at = row.update_checked_at
    status = update_checker.status
    # A release found before the prerelease switch changed is not offered: an
    # RC must not linger after opting out. The next check finds the right one.
    if status.latest is not None and status.prereleases == row.update_prereleases:
        release = status.latest
        info.latest = release.version
        info.notes_url = release.notes_url
        info.install_command = install_command(release.version)
        info.data_reset = release.data_reset
    return info


@router.get("/version")
async def get_version(session: SessionDep) -> VersionInfo:
    return await _version_info(session)


@router.post("/updates/check")
async def check_for_updates(session: SessionDep) -> VersionInfo:
    """
    Check now (About's "Check now"). Does nothing while checks are off or on a
    development build, and answers as `GET /version` either way; a check that
    fails leaves the last result in place.
    """
    await update_checker.check_now()
    return await _version_info(session)
