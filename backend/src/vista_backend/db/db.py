"""
Database engine, session factory, and FastAPI session dependency.
"""
import functools
from typing import Annotated as A, AsyncIterator
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession
from . import schemas
from ..config import settings


@functools.cache
def get_engine() -> AsyncEngine:
    return create_async_engine(settings.database_url)


async def init_db() -> None:
    """Create tables and seed defaults. Call once at app startup."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    from .defaults import DEFAULT_PROJECTS

    async with AsyncSession(engine) as session:
        for project in DEFAULT_PROJECTS:
            existing = (await session.exec(
                select(schemas.ProjectTable).where(schemas.ProjectTable.name == project.name)
            )).first()
            fields = project.model_dump(exclude={"id"})
            if existing is None:
                session.add(schemas.ProjectTable(**fields))
            else:
                for key, value in fields.items():
                    setattr(existing, key, value)
                session.add(existing)
        await session.commit()


EngineDep = A[AsyncEngine, Depends(get_engine)]

async def _get_session(engine: A[AsyncEngine, Depends(get_engine)]) -> AsyncIterator[AsyncSession]:
    async with AsyncSession(engine) as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise

SessionDep = A[AsyncSession, Depends(_get_session)]
"""
FastAPI Dependency for an async database session. Automatically commits on success, rolls back on error
"""
