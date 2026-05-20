from typing import Annotated as A

from fastapi import Depends, HTTPException, Request
from sqlmodel import select

from ..db.db import SessionDep
from ..db.schemas import UserTable


async def get_user(session: SessionDep, request: Request) -> UserTable:
    """SSO placeholder — hard-coded to the dummy regular user."""
    email = "vista-test-user@americansciencecloud.org"
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
