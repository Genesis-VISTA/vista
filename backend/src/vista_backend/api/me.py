"""`GET /me`: who the request is from, for the UI to say so (Settings → Research profile)."""

from fastapi import APIRouter
from pydantic import BaseModel

from ..services.current_user import CurrentUser

router = APIRouter()


class Me(BaseModel):
    """The signed-in user as their sign-in names them; desktop's `local` has neither."""

    display_name: str | None
    email: str | None


@router.get("/me")
async def get_me(user: CurrentUser) -> Me:
    return Me(display_name=user.display_name, email=user.email)
