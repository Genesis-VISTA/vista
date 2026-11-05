"""Teams, who is in them, and what each user can see and change because of them."""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from vista_collaboration_server.db.schemas import PUBLIC_TEAM_ID
from vista_collaboration_server.services import forums

from conftest import INSTALL_ID, OTHER_INSTALL_ID, PUBLIC, headers

pytestmark = pytest.mark.anyio

THIRD_INSTALL_ID = uuid.UUID("00000000-0000-4000-8000-000000000003")


@pytest.fixture
async def other(app):
    """A second user, "Grace"."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://collab",
        headers=headers(OTHER_INSTALL_ID, "Grace"),
    ) as client:
        await client.get("/me")  # signed in once, so she can be found and added
        yield client


async def _team(client, name="salts") -> dict:
    res = await client.post("/teams", json={"name": name})
    assert res.status_code == 201, res.text
    return res.json()


async def _item(client, team_id=PUBLIC, kind="skill", name="salt-analysis") -> dict:
    res = await client.post(
        "/items",
        json={"kind": kind, "name": name, "team_id": team_id, "manifest": {"v": 1}},
    )
    assert res.status_code == 201, res.text
    return res.json()


async def _publish(client, item_id, parent=1):
    return await client.post(
        f"/items/{item_id}/versions",
        json={"parent_version": parent, "manifest": {"v": parent + 1}},
    )


# ---------------------------------------------------------------------------
# Teams and members
# ---------------------------------------------------------------------------


async def test_everyone_starts_in_the_public_team_alone(client):
    teams = (await client.get("/teams")).json()
    assert [(t["id"], t["name"], t["public"]) for t in teams] == [
        (PUBLIC, "public", True)
    ]
    assert (await client.get("/me")).json()["teams"] == teams


async def test_a_new_team_has_its_creator_as_its_member(client):
    team = await _team(client)
    assert team["public"] is False
    assert team["created_by"] == str(INSTALL_ID)
    assert [t["id"] for t in (await client.get("/teams")).json()] == [
        PUBLIC,
        team["id"],
    ]
    members = (await client.get(f"/teams/{team['id']}/members")).json()
    assert [(m["sub"], m["name"]) for m in members] == [(str(INSTALL_ID), "Ada")]


async def test_teams_are_listed_public_first_then_by_name(client):
    await _team(client, "zinc")
    await _team(client, "Alloys")
    names = [t["name"] for t in (await client.get("/teams")).json()]
    assert names == ["public", "Alloys", "zinc"]


@pytest.mark.parametrize("name", ["salts", "SALTS", "  Salts  ", "public", "Public"])
async def test_team_names_are_unique_in_any_case(client, name):
    await _team(client, "salts")
    res = await client.post("/teams", json={"name": name})
    assert res.status_code == 409, res.text


async def test_a_team_name_cannot_be_blank(client):
    assert (await client.post("/teams", json={"name": "   "})).status_code == 422


async def test_a_member_adds_someone_who_has_signed_in(client, other):
    team = await _team(client)
    res = await client.post(
        f"/teams/{team['id']}/members", json={"sub": str(OTHER_INSTALL_ID)}
    )
    assert res.status_code == 201, res.text
    assert res.json()["name"] == "Grace"
    assert res.json()["added_by"] == str(INSTALL_ID)
    assert team["id"] in [t["id"] for t in (await other.get("/teams")).json()]
    # Now a member, Grace can add others too, and adding again changes nothing.
    again = await other.post(
        f"/teams/{team['id']}/members", json={"sub": str(OTHER_INSTALL_ID)}
    )
    assert again.status_code == 201
    assert again.json()["added_by"] == str(INSTALL_ID)
    members = (await other.get(f"/teams/{team['id']}/members")).json()
    assert {m["name"] for m in members} == {"Ada", "Grace"}


async def test_someone_who_never_signed_in_cannot_be_added(client):
    team = await _team(client)
    res = await client.post(
        f"/teams/{team['id']}/members", json={"sub": str(THIRD_INSTALL_ID)}
    )
    assert res.status_code == 404
    assert "sign in" in res.json()["detail"]


async def test_a_team_one_is_not_in_does_not_exist_to_them(client, other):
    team = await _team(client)
    assert (await other.get(f"/teams/{team['id']}")).status_code == 404
    assert (await other.get(f"/teams/{team['id']}/members")).status_code == 404
    res = await other.post(
        f"/teams/{team['id']}/members", json={"sub": str(OTHER_INSTALL_ID)}
    )
    assert res.status_code == 404
    unknown = await other.get(f"/teams/{uuid.uuid4()}")
    assert unknown.json() == (await other.get(f"/teams/{team['id']}")).json()


async def test_the_public_team_has_no_members_to_manage(client, other):
    assert (await client.get(f"/teams/{PUBLIC}/members")).status_code == 400
    add = await client.post(
        f"/teams/{PUBLIC}/members", json={"sub": str(OTHER_INSTALL_ID)}
    )
    assert add.status_code == 400
    assert (await client.delete(f"/teams/{PUBLIC}/members/me")).status_code == 400


async def test_a_member_can_leave_but_not_the_last(client, other):
    team = await _team(client)
    last = await client.delete(f"/teams/{team['id']}/members/me")
    assert last.status_code == 409
    await client.post(
        f"/teams/{team['id']}/members", json={"sub": str(OTHER_INSTALL_ID)}
    )
    assert (await client.delete(f"/teams/{team['id']}/members/me")).status_code == 204
    assert (await client.get(f"/teams/{team['id']}")).status_code == 404
    assert (await other.delete(f"/teams/{team['id']}/members/me")).status_code == 409


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


async def test_users_are_found_by_name(client, other):
    everyone = (await client.get("/users")).json()
    assert [u["name"] for u in everyone] == ["Ada", "Grace"]
    found = (await client.get("/users", params={"q": "gra"})).json()
    assert [u["sub"] for u in found] == [str(OTHER_INSTALL_ID)]
    assert (await client.get("/users", params={"q": "%"})).json() == []


async def test_a_users_name_is_what_they_last_signed_in_as(app, client):
    assert [u["name"] for u in (await client.get("/users")).json()] == ["Ada"]
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://collab",
        headers=headers(INSTALL_ID, "Ada L."),
    ) as renamed:
        assert [u["name"] for u in (await renamed.get("/users")).json()] == ["Ada L."]


async def test_a_new_users_first_requests_at_once_all_succeed(app):
    import asyncio

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://collab",
        headers=headers(THIRD_INSTALL_ID, "Lin"),
    ) as new:
        results = await asyncio.gather(*(new.get("/me") for _ in range(5)))
    assert [r.status_code for r in results] == [200] * 5


# ---------------------------------------------------------------------------
# Items in teams
# ---------------------------------------------------------------------------


async def test_a_teams_items_are_seen_only_by_its_members(client, other):
    team = await _team(client)
    private = await _item(client, team["id"], name="private")
    public = await _item(client, PUBLIC, name="public")
    assert private["team_id"] == team["id"]
    assert [i["id"] for i in (await client.get("/items")).json()] == [
        private["id"],
        public["id"],
    ]
    assert [i["id"] for i in (await other.get("/items")).json()] == [public["id"]]
    for path in ("", "/versions", "/versions/1", "/versions/1/blob"):
        res = await other.get(f"/items/{private['id']}{path}")
        assert res.status_code == 404, path
    assert (await _publish(other, private["id"])).status_code == 404

    await client.post(
        f"/teams/{team['id']}/members", json={"sub": str(OTHER_INSTALL_ID)}
    )
    assert (await other.get(f"/items/{private['id']}")).status_code == 200
    assert (await _publish(other, private["id"])).status_code == 201


async def test_an_unseen_item_answers_as_an_unknown_one_does(client, other):
    team = await _team(client)
    private = await _item(client, team["id"])
    hidden = await other.get(f"/items/{private['id']}")
    unknown = await other.get(f"/items/{uuid.uuid4()}")
    assert (hidden.status_code, hidden.json()) == (unknown.status_code, unknown.json())


async def test_items_are_listed_by_team(client):
    team = await _team(client)
    private = await _item(client, team["id"])
    await _item(client, PUBLIC)
    only = (await client.get("/items", params={"team_id": team["id"]})).json()
    assert [i["id"] for i in only] == [private["id"]]


async def test_an_item_cannot_be_published_to_a_team_one_is_not_in(client, other):
    team = await _team(client)
    res = await other.post(
        "/items",
        json={"kind": "skill", "name": "x", "team_id": team["id"], "manifest": {}},
    )
    assert res.status_code == 404
    assert (await client.get("/items")).json() == []


async def test_leaving_a_team_loses_its_items(client, other):
    team = await _team(client)
    item = await _item(client, team["id"])
    await client.post(
        f"/teams/{team['id']}/members", json={"sub": str(OTHER_INSTALL_ID)}
    )
    await client.delete(f"/teams/{team['id']}/members/me")
    assert (await client.get(f"/items/{item['id']}")).status_code == 404
    assert (await other.get(f"/items/{item['id']}")).status_code == 200


async def test_anyone_can_publish_over_a_public_item(client, other):
    item = await _item(client)
    assert (await _publish(other, item["id"])).status_code == 201


# ---------------------------------------------------------------------------
# Copying into another team
# ---------------------------------------------------------------------------


async def _copy(client, item_id, team_id, **body):
    return await client.post(
        f"/items/{item_id}/copy", json={"team_id": team_id, **body}
    )


async def test_a_copy_is_a_new_item_in_the_team_from_the_head(client, settings):
    team = await _team(client)
    blob = await client.post("/blobs", files={"file": ("a", b"the files")})
    ref = blob.json()["blob_ref"]
    original = await _item(client, PUBLIC)
    await client.post(
        f"/items/{original['id']}/versions",
        json={"parent_version": 1, "manifest": {"v": 2}, "blob_ref": ref},
    )
    res = await _copy(client, original["id"], team["id"])
    assert res.status_code == 201, res.text
    copy = res.json()
    assert copy["id"] != original["id"]
    assert (copy["team_id"], copy["head_version"], copy["name"]) == (
        team["id"],
        1,
        original["name"],
    )
    assert (copy["copied_from"], copy["copied_from_version"]) == (original["id"], 2)
    version = (await client.get(f"/items/{copy['id']}/versions/1")).json()
    assert (version["manifest"], version["blob_ref"]) == ({"v": 2}, ref)
    assert (
        await client.get(f"/items/{copy['id']}/versions/1/blob")
    ).content == b"the files"
    # The original is as it was.
    assert (await client.get(f"/items/{original['id']}")).json()["head_version"] == 2


async def test_a_copy_can_be_renamed(client):
    team = await _team(client)
    original = await _item(client, PUBLIC)
    res = await _copy(client, original["id"], team["id"], name="  renamed  ")
    assert res.json()["name"] == "renamed"


async def test_a_copy_needs_both_teams(client, other):
    mine = await _team(client, "mine")
    theirs = await _team(other, "theirs")
    private = await _item(client, mine["id"])
    public = await _item(client, PUBLIC)
    # Grace cannot see Ada's item, and Ada is not in Grace's team.
    assert (await _copy(other, private["id"], theirs["id"])).status_code == 404
    assert (await _copy(client, public["id"], theirs["id"])).status_code == 404
    assert (await _copy(other, public["id"], theirs["id"])).status_code == 201


async def test_a_copy_into_the_same_team_is_a_duplicate(client):
    original = await _item(client, PUBLIC)
    res = await _copy(client, original["id"], PUBLIC)
    assert res.status_code == 201
    assert len((await client.get("/items")).json()) == 2


async def test_a_blob_is_named_only_by_its_uploader_or_who_sees_it(client, other):
    team = await _team(client)
    ref = (await client.post("/blobs", files={"file": ("a", b"secret papers")})).json()[
        "blob_ref"
    ]
    body = {"kind": "skill", "name": "x", "manifest": {}, "blob_ref": ref}
    private = await client.post("/items", json={**body, "team_id": team["id"]})
    assert private.status_code == 201, private.text
    # Grace knows the reference but neither uploaded it nor sees a version with it.
    stolen = await other.post("/items", json={**body, "team_id": PUBLIC})
    unknown = await other.post(
        "/items", json={**body, "team_id": PUBLIC, "blob_ref": "0" * 64}
    )
    assert stolen.status_code == 422
    assert stolen.json()["detail"].replace(ref, "") == unknown.json()["detail"].replace(
        "0" * 64, ""
    )
    mine = await _item(other, PUBLIC)
    over = await other.post(
        f"/items/{mine['id']}/versions",
        json={"parent_version": 1, "manifest": {}, "blob_ref": ref},
    )
    assert over.status_code == 422
    # Added to the team, she sees a version naming it, and may name it too.
    await client.post(
        f"/teams/{team['id']}/members", json={"sub": str(OTHER_INSTALL_ID)}
    )
    allowed = await other.post("/items", json={**body, "team_id": team["id"]})
    assert allowed.status_code == 201, allowed.text
    # Uploading the same bytes herself is proof enough too.
    again = await other.post("/blobs", files={"file": ("a", b"secret papers")})
    assert again.json()["blob_ref"] == ref


def test_the_public_teams_id_is_fixed():
    assert str(PUBLIC_TEAM_ID) == PUBLIC


# ---------------------------------------------------------------------------
# A copy's forum
# ---------------------------------------------------------------------------

needs_git = pytest.mark.skipif(
    not forums.git_usable(), reason="needs git 2.34 or later"
)


@needs_git
async def test_a_copy_of_an_item_with_a_forum_gets_a_new_empty_one(client, other):
    mine = await _team(client, "mine")
    theirs = await _team(client, "theirs")
    await client.post(
        f"/teams/{theirs['id']}/members", json={"sub": str(OTHER_INSTALL_ID)}
    )
    original = await _item(client, mine["id"], kind="project", name="proj")
    forum = (await client.post("/forums")).json()
    linked = await client.put(
        f"/forums/{forum['id']}/item", json={"item_id": original["id"]}
    )
    assert linked.status_code == 200, linked.text

    copy = (await _copy(client, original["id"], theirs["id"])).json()
    res = await client.get(f"/items/{copy['id']}/forum")
    assert res.status_code == 200, res.text
    new = res.json()
    assert new["id"] != forum["id"] and new["item_id"] == copy["id"]
    assert (await client.get(f"/forums/{new['id']}/pushes")).json() == []
    # The repository was made with the copy, and is empty.
    refs = f"/git/{new['id']}.git/info/refs?service=git-upload-pack"
    assert (await client.get(refs)).status_code == 200
    # The original's forum is still the original's; the other team cannot read it.
    assert (await client.get(f"/items/{original['id']}/forum")).json()["id"] == forum[
        "id"
    ]
    assert (await other.get(f"/forums/{forum['id']}/pushes")).status_code == 404
    gone = f"/git/{forum['id']}.git/info/refs?service=git-upload-pack"
    assert (await other.get(gone)).status_code == 404
    assert (await other.get(f"/items/{copy['id']}/forum")).status_code == 200


@needs_git
async def test_a_copy_of_an_item_without_a_forum_has_none(client):
    team = await _team(client)
    original = await _item(client, PUBLIC, kind="project")
    copy = (await _copy(client, original["id"], team["id"])).json()
    assert (await client.get(f"/items/{copy['id']}/forum")).status_code == 404
