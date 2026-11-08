"""
Service-layer project tests. With one researcher per installation there is no
membership to check: every project is visible, and only an unknown one fails.
"""

import pytest
from fastapi import HTTPException

from vista_backend.db.schemas import ProjectCreate
from vista_backend.services import project as project_service


@pytest.mark.anyio
async def test_every_project_is_listed(session):
    await project_service.create_project(session, ProjectCreate(name="p1"))
    await project_service.create_project(session, ProjectCreate(name="p2"))
    names = {p.name for p in await project_service.list_projects(session)}
    assert names == {"p1", "p2"}


@pytest.mark.anyio
async def test_get_project_by_name(session):
    await project_service.create_project(session, ProjectCreate(name="p1"))
    assert (await project_service.get_project_by_name(session, "p1")).name == "p1"


@pytest.mark.anyio
async def test_get_missing_project_is_404(session):
    with pytest.raises(HTTPException) as exc:
        await project_service.get_project_by_name(session, "nope")
    assert exc.value.status_code == 404


@pytest.mark.anyio
async def test_update_and_delete(session):
    await project_service.create_project(session, ProjectCreate(name="p1"))
    updated = await project_service.update_project(
        session, "p1", ProjectCreate(name="p1", description="edited")
    )
    assert updated.description == "edited"
    await project_service.delete_project(session, "p1")
    await session.flush()
    assert await project_service.get_project_by_name_optional(session, "p1") is None
