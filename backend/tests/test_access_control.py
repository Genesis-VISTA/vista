"""Service-layer access control tests for projects and users."""
import uuid

import pytest
from fastapi import HTTPException

from sqlmodel import select

from vista_backend.db.schemas import ProjectCreate, ProjectMemberTable, UserCreate
from vista_backend.services import project as project_service
from vista_backend.services import user as user_service


async def _member_count(session) -> int:
    return len((await session.exec(select(ProjectMemberTable))).all())


# --------------------------------------------------------------------------- #
# Projects
# --------------------------------------------------------------------------- #

@pytest.mark.anyio
async def test_create_project_makes_creator_a_member(session, alice):
    await project_service.create_project(session, ProjectCreate(name="p1"), alice)
    names = [p.name for p in await project_service.list_projects(session, alice)]
    assert names == ["p1"]


@pytest.mark.anyio
async def test_list_projects_filters_to_membership(session, alice, bob):
    await project_service.create_project(session, ProjectCreate(name="p1"), alice)
    await project_service.create_project(session, ProjectCreate(name="p2"), bob)
    assert [p.name for p in await project_service.list_projects(session, alice)] == ["p1"]
    assert [p.name for p in await project_service.list_projects(session, bob)] == ["p2"]


@pytest.mark.anyio
async def test_admin_and_system_see_all_projects(session, alice, bob, admin):
    await project_service.create_project(session, ProjectCreate(name="p1"), alice)
    await project_service.create_project(session, ProjectCreate(name="p2"), bob)
    assert len(await project_service.list_projects(session, admin)) == 2
    assert len(await project_service.list_projects(session, "system")) == 2


@pytest.mark.anyio
async def test_create_project_as_system_adds_no_member(session):
    await project_service.create_project(session, ProjectCreate(name="p1"), "system")
    assert await _member_count(session) == 0


@pytest.mark.anyio
async def test_get_project_denied_for_non_member(session, alice, bob):
    await project_service.create_project(session, ProjectCreate(name="p1"), alice)
    with pytest.raises(HTTPException) as exc:
        await project_service.get_project_by_name(session, "p1", bob)
    assert exc.value.status_code == 403


@pytest.mark.anyio
async def test_get_project_allowed_for_member_admin_and_system(session, alice, admin):
    await project_service.create_project(session, ProjectCreate(name="p1"), alice)
    assert (await project_service.get_project_by_name(session, "p1", alice)).name == "p1"
    assert (await project_service.get_project_by_name(session, "p1", admin)).name == "p1"
    assert (await project_service.get_project_by_name(session, "p1", "system")).name == "p1"


@pytest.mark.anyio
async def test_get_missing_project_is_404(session, admin):
    with pytest.raises(HTTPException) as exc:
        await project_service.get_project_by_name(session, "nope", admin)
    assert exc.value.status_code == 404


@pytest.mark.anyio
async def test_update_and_delete_denied_for_non_member(session, alice, bob):
    await project_service.create_project(session, ProjectCreate(name="p1"), alice)
    with pytest.raises(HTTPException) as exc:
        await project_service.update_project(session, "p1", ProjectCreate(name="p1"), bob)
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException) as exc:
        await project_service.delete_project(session, "p1", bob)
    assert exc.value.status_code == 403


@pytest.mark.anyio
async def test_delete_project_cascades_membership(session, alice, bob):
    await project_service.create_project(session, ProjectCreate(name="p1"), alice)
    await project_service.add_project_member(session, "p1", bob.email, alice)
    assert await _member_count(session) == 2
    await project_service.delete_project(session, "p1", alice)
    await session.flush()
    assert await _member_count(session) == 0


@pytest.mark.anyio
async def test_delete_user_cascades_membership(session, alice, admin):
    await project_service.create_project(session, ProjectCreate(name="p1"), alice)
    assert await _member_count(session) == 1
    await user_service.delete_user(session, alice.id, admin)
    await session.flush()
    assert await _member_count(session) == 0


# --------------------------------------------------------------------------- #
# Membership management
# --------------------------------------------------------------------------- #

@pytest.mark.anyio
async def test_member_can_add_member(session, alice, bob):
    await project_service.create_project(session, ProjectCreate(name="p1"), alice)
    await project_service.add_project_member(session, "p1", bob.email, alice)
    # bob now sees the project
    assert [p.name for p in await project_service.list_projects(session, bob)] == ["p1"]
    members = await project_service.list_project_members(session, "p1", alice)
    assert {m.id for m in members} == {alice.id, bob.id}


@pytest.mark.anyio
async def test_non_member_cannot_add_member(session, alice, bob):
    await project_service.create_project(session, ProjectCreate(name="p1"), alice)
    with pytest.raises(HTTPException) as exc:
        await project_service.add_project_member(session, "p1", bob.email, bob)
    assert exc.value.status_code == 403


@pytest.mark.anyio
async def test_add_unknown_user_is_404(session, alice):
    await project_service.create_project(session, ProjectCreate(name="p1"), alice)
    with pytest.raises(HTTPException) as exc:
        await project_service.add_project_member(session, "p1", "nobody@example.com", alice)
    assert exc.value.status_code == 404


@pytest.mark.anyio
async def test_add_duplicate_member_is_409(session, alice, bob):
    await project_service.create_project(session, ProjectCreate(name="p1"), alice)
    await project_service.add_project_member(session, "p1", bob.email, alice)
    with pytest.raises(HTTPException) as exc:
        await project_service.add_project_member(session, "p1", bob.email, alice)
    assert exc.value.status_code == 409


# --------------------------------------------------------------------------- #
# Users
# --------------------------------------------------------------------------- #

@pytest.mark.anyio
async def test_admin_only_user_ops_denied_for_non_admin(session, alice):
    with pytest.raises(HTTPException) as exc:
        await user_service.list_users(session, alice)
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException) as exc:
        await user_service.create_user(session, UserCreate(email="x@example.com"), alice)
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException) as exc:
        await user_service.delete_user(session, uuid.uuid4(), alice)
    assert exc.value.status_code == 403


@pytest.mark.anyio
async def test_get_user_self_or_admin(session, alice, bob, admin):
    # self is allowed
    assert (await user_service.get_user_by_id(session, alice.id, alice)).id == alice.id
    # other user is forbidden
    with pytest.raises(HTTPException) as exc:
        await user_service.get_user_by_id(session, bob.id, alice)
    assert exc.value.status_code == 403
    # admin and system are allowed
    assert (await user_service.get_user_by_id(session, alice.id, admin)).id == alice.id
    assert (await user_service.get_user_by_id(session, alice.id, "system")).id == alice.id


@pytest.mark.anyio
async def test_admin_user_ops_allowed(session, admin):
    assert len(await user_service.list_users(session, admin)) >= 1
    created = await user_service.create_user(session, UserCreate(email="new@example.com"), admin)
    assert created.email == "new@example.com"
