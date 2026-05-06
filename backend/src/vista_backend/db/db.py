"""
Database engine, session factory, and FastAPI session dependency.
"""
from typing import Annotated as A, Iterator
from fastapi import Depends
import functools
import sqlalchemy
from sqlmodel import Session, SQLModel, create_engine, select
from . import schemas
from ..config import settings


@functools.cache
def get_engine() -> sqlalchemy.Engine:
    engine = create_engine(settings.database_url)
    SQLModel.metadata.create_all(engine)  # only creates tables if needed
    _seed_defaults(engine)
    return engine


def _seed_defaults(engine: sqlalchemy.Engine) -> None:
    from .defaults import DEFAULT_PROJECTS

    with Session(engine) as session:
        for project in DEFAULT_PROJECTS:
            existing = session.exec(
                select(schemas.ProjectTable).where(schemas.ProjectTable.name == project.name)
            ).first()
            fields = project.model_dump(exclude={"id"})
            if existing is None:
                session.add(schemas.ProjectTable(**fields))
            else:
                for key, value in fields.items():
                    setattr(existing, key, value)
                session.add(existing)
        session.commit()


EngineDep = A[sqlalchemy.Engine, Depends(get_engine)]


def _get_session(engine: A[sqlalchemy.Engine, Depends(get_engine)]) -> Iterator[Session]:
    with Session(engine) as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise

SessionDep = A[Session, Depends(_get_session)]
"""
FastAPI Dependency for a database session. Automatically commits on success, rolls back on error
"""
