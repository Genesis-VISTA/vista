from typing import Annotated as A

from fastapi import Depends, HTTPException, Request
from sqlmodel import select

from ..config import settings
from ..db.db import SessionDep
from ..db.schemas import UserTable


async def get_user(session: SessionDep, request: Request) -> UserTable:
    """SSO placeholder — uses a hard-coded dev user in dev mode; raises 501 in prod."""
    if settings.env == 'prod':
        raise HTTPException(status_code=501, detail="SSO authentication is not yet implemented")
    email = "vista-test-admin@americansciencecloud.org"
    user = (await session.exec(select(UserTable).where(UserTable.email == email))).first()
    if user is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return user


async def get_admin(user: A[UserTable, Depends(get_user)]) -> UserTable:
    """ Gets current user, require them to be admin """
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


UserDep = A[UserTable, Depends(get_user)]
AdminDep = A[UserTable, Depends(get_admin)]
