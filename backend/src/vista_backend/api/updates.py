"""
`GET /version`, `/updates/*` and `GET /activity`: which VISTA this is,
whether a newer release exists, downloading it, and what restarting into it
would interrupt, for Settings > About (openspec change `in-app-updates`).
"""

import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import col, select

from ..config import settings
from ..db.db import SessionDep
from ..db.schemas import ChatSessionTable, ProjectTable
from ..services import campaign as campaign_service
from ..services import local_settings
from ..services.chat_run import chat_run_registry
from ..services.update_check import (
    Release,
    is_dev_build,
    update_checker,
)
from ..services.update_download import update_downloader

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
    pending: bool = False
    """
    Checks are on and this backend has not finished its first check yet (the one
    it makes on start), so `latest` may be about to change.
    """
    latest: str | None = None
    """ A newer release's version, when the last check found one. """
    notes_url: str | None = None
    """ Its release notes on GitHub. """
    data_reset: bool | None = None
    """
    Whether installing it will ask to reset local data; `None` when the release
    does not say (every release before `update.json` existed).
    """


def _checks(row) -> Literal["on", "off", "dev_build"]:
    if is_dev_build(settings.version):
        return "dev_build"
    return "on" if row.update_checks else "off"


def _offered(row) -> Release | None:
    """
    The release this install is offered, if any. One found before the
    prerelease switch changed is not: an RC must not linger after opting out,
    and the next check finds the right one.
    """
    status = update_checker.status
    if _checks(row) != "on" or status.latest is None:
        return None
    return status.latest if status.prereleases == row.update_prereleases else None


async def _version_info(session: SessionDep) -> VersionInfo:
    row = await local_settings.get(session)
    info = VersionInfo(
        version=settings.version, commit=settings.commit or None, checks=_checks(row)
    )
    if info.checks != "on":
        return info
    info.checked_at = row.update_checked_at
    info.pending = not update_checker.attempted
    release = _offered(row)
    if release is not None:
        info.latest = release.version
        info.notes_url = release.notes_url
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


# ---------------------------------------------------------------------------
# The download
# ---------------------------------------------------------------------------


class DownloadRequest(BaseModel):
    version: str
    """ The version the page offered, which must still be the one on offer. """


class DownloadStatusPublic(BaseModel):
    state: Literal["idle", "downloading", "ready", "failed"]
    version: str | None = None
    bytes: int = 0
    total: int = 0
    error: str | None = None


def _download_status() -> DownloadStatusPublic:
    s = update_downloader.status
    return DownloadStatusPublic(
        state=s.state, version=s.version, bytes=s.bytes, total=s.total, error=s.error
    )


@router.post("/updates/download")
async def start_download(
    request: DownloadRequest, session: SessionDep
) -> DownloadStatusPublic:
    """
    Download and verify the offered release into the cache, in the background;
    poll `GET /updates/download` for progress. 409 when that version is not the
    one on offer (checks off, a dev build, or a newer check since).
    """
    release = _offered(await local_settings.get(session))
    if release is None or release.version != request.version:
        raise HTTPException(409, f"VISTA {request.version} is not the update on offer.")
    update_downloader.start(release)
    return _download_status()


@router.get("/updates/download")
async def get_download() -> DownloadStatusPublic:
    return _download_status()


# ---------------------------------------------------------------------------
# What a restart would interrupt
# ---------------------------------------------------------------------------


class ActiveChatRun(BaseModel):
    chat_session_id: uuid.UUID
    project_id: uuid.UUID
    needs_you: bool
    """ Waiting on the researcher's answer to a prompt. """
    title: str | None = None
    """ The conversation's title, so the confirm dialog can name it. """
    project_name: str | None = None


class WatchedJob(BaseModel):
    job_id: str
    cluster: str
    job_name: str | None = None
    state: str


class Activity(BaseModel):
    """Work a restart would stop, for the update's confirm dialog."""

    chat_runs: list[ActiveChatRun]
    debates: int
    """ Hypothesis Lab debates arguing in the background. """
    watched_jobs: list[WatchedJob]
    """
    HPC jobs VISTA is still watching. They keep running on the cluster; VISTA
    stops watching them until it is back.
    """


@router.get("/activity")
async def get_activity(session: SessionDep) -> Activity:
    from . import debate

    runs = [run for run in chat_run_registry.active() if not run.finished]
    titles: dict[uuid.UUID, str] = {}
    projects: dict[uuid.UUID, str] = {}
    if runs:
        titles = dict(
            (
                await session.exec(
                    select(ChatSessionTable.id, ChatSessionTable.title).where(
                        col(ChatSessionTable.id).in_([r.chat_session_id for r in runs])
                    )
                )
            ).all()
        )
        projects = dict(
            (
                await session.exec(
                    select(ProjectTable.id, ProjectTable.name).where(
                        col(ProjectTable.id).in_([r.project_id for r in runs])
                    )
                )
            ).all()
        )
    return Activity(
        chat_runs=[
            ActiveChatRun(
                chat_session_id=run.chat_session_id,
                project_id=run.project_id,
                needs_you=bool(run.pending),
                title=titles.get(run.chat_session_id),
                project_name=projects.get(run.project_id),
            )
            for run in runs
        ],
        debates=sum(1 for task in debate._BACKGROUND if not task.done()),
        watched_jobs=[
            WatchedJob(
                job_id=job.job_id,
                cluster=job.cluster,
                job_name=job.job_name,
                state=job.state,
            )
            for job in await campaign_service.list_open_jobs(session)
        ],
    )
