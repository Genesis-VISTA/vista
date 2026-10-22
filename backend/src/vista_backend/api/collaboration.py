from fastapi import APIRouter

from ..services import collaboration
from ..services.local_settings import LocalSettingsDep

router = APIRouter(prefix="/collaboration", tags=["collaboration"])


@router.get("/status")
async def get_status(local: LocalSettingsDep) -> collaboration.CollaborationStatus:
    """
    Whether publishing and importing can work: a server is set and answers. Checked
    on every call, in at most a few seconds; `reason` says why not when they cannot.
    """
    return await collaboration.status(local)
