"""`GET /version`: which VISTA this is, for Settings > About."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from ..config import settings
from ..services.local_settings import LocalSettingsDep
from ..services.update_check import is_dev_build

router = APIRouter(tags=["version"])


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


@router.get("/version")
async def get_version(local: LocalSettingsDep) -> VersionInfo:
    if is_dev_build(settings.version):
        checks = "dev_build"
    else:
        checks = "on" if local.update_checks else "off"
    return VersionInfo(
        version=settings.version, commit=settings.commit or None, checks=checks
    )
