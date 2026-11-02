"""
Teams, from this installation's side: Private and the selected team, what a new
project, skill or knowledge base joins, that a project uses only its own team's,
that a private one is copied to a team rather than published, and Copy to team
itself (`services/team_copy.py`). Against a real collaboration server mounted
in-process (`collab_server` in conftest).
"""

import uuid

import pytest
from sqlmodel import select

from harness import api_client
from harness.knowledge_bases import kb_row, make_kb
from vista_backend.config import settings
from vista_backend.db.schemas import (
    KnowledgeBaseTable,
    LocalSettingsUpdate,
    ProjectTable,
    SkillTable,
)
from vista_backend.services import local_settings
from vista_backend.services.collaboration import PUBLIC_TEAM_ID, CollaborationClient

pytestmark = pytest.mark.anyio

URL = "http://collab.test:8002"
BEA = uuid.UUID("00000000-0000-4000-8000-0000000000b2")

SKILL = {"description": "Estimate a molten salt's density.", "body": "# Density"}


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "collaboration_server_url", None)
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")


async def _use_server(session, url: str | None = URL):
    await local_settings.update(
        session,
        LocalSettingsUpdate.model_validate(
            {"collaboration_server_url": url, "display_name": "Ada"}
        ),
    )
    await session.commit()


def _bea() -> CollaborationClient:
    """A second VISTA installation on the same server."""
    return CollaborationClient(URL, install_id=BEA, display_name="Bea")


async def _ada_id(session) -> str:
    return str((await local_settings.get(session)).install_id)


async def _team(client, name: str = "salts") -> dict:
    res = await client.post("/collaboration/teams", json={"name": name})
    assert res.status_code == 201, res.text
    return res.json()


async def _select(client, team_id) -> None:
    res = await client.put(
        "/settings",
        json={"collaboration_team_id": None if team_id is None else str(team_id)},
    )
    assert res.status_code == 200, res.text


async def _skill(client, name: str, project: str | None = None):
    return await client.post(
        "/skills", json={"name": name, **SKILL, "project": project}
    )


async def _project(client, name: str, **fields):
    return await client.post("/projects", json={"name": name, **fields})


async def _row(session, table, **where):
    session.expire_all()
    query = select(table)
    for key, value in where.items():
        query = query.where(getattr(table, key) == value)
    return (await session.exec(query)).first()


async def _kb(session, slug: str, team_id=None) -> KnowledgeBaseTable:
    kb_dir = settings.knowledge_bases_dir / slug
    make_kb(kb_dir)
    row = kb_row(kb_dir, slug, team_id=team_id)
    session.add(row)
    await session.commit()
    return row


# ---------------------------------------------------------------------------
# Private, and the selected team
# ---------------------------------------------------------------------------


async def test_the_status_lists_the_teams_and_the_one_selected(collab_server, session):
    await _use_server(session)
    with api_client(session) as (client, _):
        private = (await client.get("/collaboration/status")).json()
        team = await _team(client)
        await _select(client, team["id"])
        selected = (await client.get("/collaboration/status")).json()
        await _select(client, PUBLIC_TEAM_ID)
        public = (await client.get("/collaboration/status")).json()
    assert private["team_id"] is None
    assert [t["name"] for t in selected["teams"]] == ["public", "salts"]
    assert selected["team_id"] == team["id"]
    assert public["team_id"] == str(PUBLIC_TEAM_ID)


async def test_leaving_the_selected_team_selects_private(collab_server, session):
    await _use_server(session)
    async with _bea() as bea:
        await bea.me()  # signed in once, so Ada can add her
    with api_client(session) as (client, _):
        team = await _team(client)
        await client.post(
            f"/collaboration/teams/{team['id']}/members", json={"sub": str(BEA)}
        )
        await _select(client, team["id"])
        left = await client.delete(f"/collaboration/teams/{team['id']}/members/me")
        now = (await client.get("/settings")).json()
    assert left.status_code == 204, left.text
    assert now["collaboration_team_id"] is None


async def test_users_are_found_to_add(collab_server, session):
    await _use_server(session)
    async with _bea() as bea:
        await bea.me()
    with api_client(session) as (client, _):
        found = (await client.get("/collaboration/users", params={"q": "be"})).json()
    assert [(u["sub"], u["name"]) for u in found] == [(str(BEA), "Bea")]


async def test_another_server_makes_everything_here_private_until_back(
    collab_server, session
):
    from vista_backend.services.teams import current_url, team_of

    await _use_server(session)
    with api_client(session) as (client, _):
        team = await _team(client)
        await _select(client, team["id"])
        await _skill(client, "teamed")
        await session.commit()
    await _use_server(session, "http://elsewhere.test:8002")
    assert (await local_settings.get(session)).collaboration_team_id is None
    row = await _row(session, SkillTable, name="teamed")
    assert team_of(row, await current_url(session)) is None
    # Nothing was erased: back on its server, it is in its team again.
    await _use_server(session)
    row = await _row(session, SkillTable, name="teamed")
    assert str(team_of(row, await current_url(session))) == team["id"]


async def test_clearing_the_address_back_to_the_same_default_keeps_the_team(
    collab_server, session, monkeypatch
):
    monkeypatch.setattr(settings, "collaboration_server_url", URL)
    await _use_server(session)
    with api_client(session) as (client, _):
        team = await _team(client)
        await _select(client, team["id"])
    await _use_server(session, None)
    assert str((await local_settings.get(session)).collaboration_team_id) == team["id"]


async def test_a_selected_team_one_is_no_longer_in_is_cleared(collab_server, session):
    await _use_server(session)
    with api_client(session) as (client, _):
        await _select(client, uuid.uuid4())
        status = (await client.get("/collaboration/status")).json()
        now = (await client.get("/settings")).json()
    assert status["team_id"] is None
    assert now["collaboration_team_id"] is None


# ---------------------------------------------------------------------------
# What a new resource joins, and what a project may use
# ---------------------------------------------------------------------------


async def test_new_resources_are_private_until_a_team_is_selected(
    collab_server, session
):
    await _use_server(session)
    with api_client(session) as (client, _):
        private = await _skill(client, "private-skill")
        team = await _team(client)
        await _select(client, team["id"])
        skill = await _skill(client, "team-skill")
        project = await _project(client, "team-project")
        kb = await client.post(
            "/knowledge-bases", json={"slug": "team-papers", "name": "Papers"}
        )
    assert private.json()["team_id"] is None
    assert skill.json()["team_id"] == team["id"]
    assert project.json()["team_id"] == team["id"]
    assert kb.status_code == 201, kb.text
    assert kb.json()["team_id"] == team["id"]


async def test_a_project_uses_only_its_own_teams(collab_server, session):
    await _use_server(session)
    with api_client(session) as (client, _):
        await _skill(client, "private-skill")
        await _project(client, "private-lab", skills=["private-skill"])
        team = await _team(client)
        await _select(client, team["id"])
        await _skill(client, "team-skill")
        mixed = await _project(client, "mixed", skills=["private-skill"])
        await _project(client, "team-lab")
        update = await client.put(
            "/projects/team-lab", json={"name": "team-lab", "skills": ["private-skill"]}
        )
        await _select(client, None)
        private_mixed = await _project(client, "private-mixed", skills=["team-skill"])
        # Made for a project, a skill joins its team, whatever is selected.
        made_here = await _skill(client, "for-team", project="team-lab")
    for refused in (mixed, update, private_mixed):
        assert refused.status_code == 400, refused.text
        assert "another team" in refused.json()["detail"]
    assert made_here.status_code == 201, made_here.text
    assert made_here.json()["team_id"] == team["id"]


async def test_a_private_resource_is_copied_not_published(collab_server, session):
    await _use_server(session)
    await _kb(session, "papers")
    with api_client(session) as (client, _):
        await _skill(client, "private-skill")
        await _project(client, "private-lab")
        refusals = [
            await client.post("/skills/private-skill/publish", json={}),
            await client.post("/knowledge-bases/papers/publish", json={}),
            await client.post("/projects/private-lab/publish", json={}),
        ]
    for res in refusals:
        assert res.status_code == 400, res.text
        assert "private" in res.json()["detail"]
    async with _bea() as bea:
        assert await bea.list_items() == []


# ---------------------------------------------------------------------------
# Publishing and importing by team
# ---------------------------------------------------------------------------


async def test_a_resource_publishes_to_its_own_team(collab_server, session):
    await _use_server(session)
    with api_client(session) as (client, _):
        team = await _team(client)
        await _select(client, team["id"])
        await _skill(client, "team-skill")
        await session.commit()
        res = await client.post("/skills/team-skill/publish", json={})
        listed = (await client.get("/collaboration/skills")).json()
    assert res.status_code == 200, res.text
    assert [(s["name"], s["team_id"]) for s in listed] == [("team-skill", team["id"])]
    async with _bea() as bea:
        assert await bea.list_items() == []  # Bea is not in the team


async def test_an_import_joins_its_items_team(collab_server, session):
    await _use_server(session)
    with api_client(session) as (client, _):
        await client.get("/collaboration/status")  # Ada signs in, so Bea can add her
    async with _bea() as bea:
        team = await bea.create_team("bea-team")
        await bea.add_member(team.id, await _ada_id(session))
        item = await bea.create_item(
            "project",
            "bea-lab",
            team_id=team.id,
            manifest={"format": 3, "name": "bea-lab"},
        )
    with api_client(session) as (client, _):
        res = await client.post(f"/collaboration/projects/{item.id}/import", json={})
    assert res.status_code == 200, res.text
    assert res.json()["project"]["team_id"] == str(team.id)


# ---------------------------------------------------------------------------
# Copy to team
# ---------------------------------------------------------------------------


async def _copy(client, kind: str, name: str, team_id) -> dict:
    res = await client.post(f"/{kind}/{name}/copy", json={"team_id": str(team_id)})
    assert res.status_code == 200, res.text
    return res.json()


async def test_a_private_skill_is_published_into_the_team(collab_server, session):
    await _use_server(session)
    with api_client(session) as (client, _):
        await _skill(client, "density")
        team = await _team(client)
        await session.commit()
        result = await _copy(client, "skills", "density", team["id"])
    assert result["copied"] == [
        {
            "kind": "skill",
            "name": "density",
            "local_name": "density",
            "how": "published",
        }
    ]
    row = await _row(session, SkillTable, name="density")
    assert (str(row.team_id), row.remote_version, row.modified) == (
        team["id"],
        1,
        False,
    )
    async with _bea() as bea:
        assert await bea.list_items() == []


async def test_an_unchanged_skill_is_copied_by_the_server(
    collab_server, session, monkeypatch
):
    await _use_server(session)
    with api_client(session) as (client, _):
        first = await _team(client, "first")
        second = await _team(client, "second")
        await _select(client, first["id"])
        await _skill(client, "density")
        await session.commit()
        await client.post("/skills/density/publish", json={})
        original = (await _row(session, SkillTable, name="density")).remote_id

        async def no_upload(*args, **kwargs):
            raise AssertionError("an unchanged copy uploads nothing")

        monkeypatch.setattr(CollaborationClient, "upload_blob", no_upload)
        result = await _copy(client, "skills", "density", second["id"])
        listed = (await client.get("/collaboration/skills")).json()
    assert result["copied"][0]["how"] == "copied"
    row = await _row(session, SkillTable, name="density")
    assert str(row.team_id) == second["id"] and row.remote_id != original
    by_team = {s["team_id"]: s["id"] for s in listed}
    # The original stays, for the first team.
    assert by_team == {first["id"]: str(original), second["id"]: str(row.remote_id)}


async def test_a_changed_skill_is_copied_with_its_change(collab_server, session):
    await _use_server(session)
    with api_client(session) as (client, _):
        first = await _team(client, "first")
        second = await _team(client, "second")
        await _select(client, first["id"])
        await _skill(client, "density")
        await session.commit()
        await client.post("/skills/density/publish", json={})
        await client.patch("/skills/density", json={"description": "Changed here."})
        await session.commit()
        result = await _copy(client, "skills", "density", second["id"])
    assert result["copied"][0]["how"] == "published"
    row = await _row(session, SkillTable, name="density")
    remote_id, modified = row.remote_id, row.modified
    async with CollaborationClient(
        URL, install_id=uuid.UUID(await _ada_id(session)), display_name="Ada"
    ) as ada:
        version = await ada.get_version(remote_id, 1)
    assert version.manifest["description"] == "Changed here."
    assert modified is False


async def test_a_skill_a_project_uses_is_copied_beside_it(collab_server, session):
    await _use_server(session)
    with api_client(session) as (client, _):
        await _skill(client, "density")
        await _project(client, "lab", skills=["density"])
        team = await _team(client)
        await session.commit()
        result = await _copy(client, "skills", "density", team["id"])
    assert result["copied"] == [
        {
            "kind": "skill",
            "name": "density",
            "local_name": "density-2",
            "how": "imported",
        }
    ]
    original = await _row(session, SkillTable, name="density")
    assert (original.team_id, original.remote_id) == (None, None)
    copy = await _row(session, SkillTable, name="density-2")
    assert str(copy.team_id) == team["id"]
    assert (await _row(session, ProjectTable, name="lab")).skills == ["density"]


async def test_a_project_is_copied_with_everything_it_uses(collab_server, session):
    await _use_server(session)
    await _kb(session, "papers")
    with api_client(session) as (client, _):
        await _skill(client, "density")
        await _project(client, "lab", skills=["density"], knowledge_bases=["papers"])
        team = await _team(client)
        await session.commit()
        result = await _copy(client, "projects", "lab", team["id"])
    assert [(c["kind"], c["name"], c["how"]) for c in result["copied"]] == [
        ("skill", "density", "published"),
        ("knowledge-base", "papers", "published"),
        ("project", "lab", "published"),
    ]
    ids = {}
    for table, where in (
        (ProjectTable, {"name": "lab"}),
        (SkillTable, {"name": "density"}),
        (KnowledgeBaseTable, {"slug": "papers"}),
    ):
        row = await _row(session, table, **where)
        assert str(row.team_id) == team["id"]
        assert row.remote_id is not None and row.modified is False
        ids[table] = row.remote_id
    async with CollaborationClient(
        URL, install_id=uuid.UUID(await _ada_id(session)), display_name="Ada"
    ) as ada:
        manifest = (await ada.get_version(ids[ProjectTable], 1)).manifest
    assert [s["remote_id"] for s in manifest["skills"]] == [str(ids[SkillTable])]
    assert [k["remote_id"] for k in manifest["knowledge_bases"]] == [
        str(ids[KnowledgeBaseTable])
    ]


async def test_a_shared_dependency_is_copied_beside_the_original(
    collab_server, session
):
    await _use_server(session)
    with api_client(session) as (client, _):
        await _skill(client, "density")
        await _project(client, "lab", skills=["density"])
        await _project(client, "other-lab", skills=["density"])
        team = await _team(client)
        await session.commit()
        result = await _copy(client, "projects", "lab", team["id"])
    assert result["copied"][0]["local_name"] == "density-2"
    assert (await _row(session, ProjectTable, name="lab")).skills == ["density-2"]
    other = await _row(session, ProjectTable, name="other-lab")
    assert (other.skills, other.team_id) == (["density"], None)
    assert (await _row(session, SkillTable, name="density")).team_id is None


async def test_a_copy_needs_a_team_one_is_in_and_another_team(collab_server, session):
    await _use_server(session)
    async with _bea() as bea:
        theirs = await bea.create_team("theirs")
    with api_client(session) as (client, _):
        await _skill(client, "density")
        mine = await _team(client)
        await session.commit()
        not_member = await client.post(
            "/skills/density/copy", json={"team_id": str(theirs.id)}
        )
        await _copy(client, "skills", "density", mine["id"])
        again = await client.post("/skills/density/copy", json={"team_id": mine["id"]})
    assert not_member.status_code == 404, not_member.text
    assert again.status_code == 400
    assert "already" in again.json()["detail"]


async def test_a_project_using_what_is_not_here_is_not_copied(collab_server, session):
    await _use_server(session)
    with api_client(session) as (client, _):
        team = await _team(client)
        await _project(client, "lab")
    project = await _row(session, ProjectTable, name="lab")
    project.skills = ["absent"]
    session.add(project)
    await session.commit()
    with api_client(session) as (client, _):
        res = await client.post("/projects/lab/copy", json={"team_id": team["id"]})
    assert res.status_code == 400
    assert "absent" in res.json()["detail"]
    async with _bea() as bea:
        assert await bea.list_items() == []


async def test_a_resource_of_a_team_one_has_left_is_copied_from_here(
    collab_server, session
):
    await _use_server(session)
    async with _bea() as bea:
        await bea.me()
    with api_client(session) as (client, _):
        old = await _team(client, "old")
        new = await _team(client, "new")
        await client.post(
            f"/collaboration/teams/{old['id']}/members", json={"sub": str(BEA)}
        )
        await _select(client, old["id"])
        await _skill(client, "density")
        await session.commit()
        await client.post("/skills/density/publish", json={})
        await client.delete(f"/collaboration/teams/{old['id']}/members/me")
        result = await _copy(client, "skills", "density", new["id"])
    # The old item is out of reach, so the copy is published from here.
    assert result["copied"][0]["how"] == "published"
    assert str((await _row(session, SkillTable, name="density")).team_id) == new["id"]


async def test_a_bundled_skill_stays_private_when_its_project_is_copied(
    collab_server, session
):
    await _use_server(session)
    with api_client(session) as (client, _):
        await _skill(client, "salt-analysis")
        row = await _row(session, SkillTable, name="salt-analysis")
        row.bundled = True
        session.add(row)
        await session.commit()
        await _project(client, "lab", skills=["salt-analysis"])
        team = await _team(client)
        await session.commit()
        result = await _copy(client, "projects", "lab", team["id"])
    assert result["copied"][0] == {
        "kind": "skill",
        "name": "salt-analysis",
        "local_name": "salt-analysis-2",
        "how": "imported",
    }
    assert (await _row(session, SkillTable, name="salt-analysis")).team_id is None
    assert (await _row(session, ProjectTable, name="lab")).skills == ["salt-analysis-2"]


async def test_a_failed_project_copy_is_never_published_to_its_old_team(
    collab_server, session, monkeypatch
):
    from vista_backend.services import project_collaboration
    from vista_backend.services.collaboration import CollaborationUnavailable

    await _use_server(session)
    with api_client(session) as (client, _):
        old = await _team(client, "old")
        new = await _team(client, "new")
        await _select(client, old["id"])
        await _project(client, "lab")
        await session.commit()
        await client.post("/projects/lab/publish", json={})
        publish = project_collaboration.publish_project

        async def down(*args, **kwargs):
            raise CollaborationUnavailable("The server went away.")

        monkeypatch.setattr(project_collaboration, "publish_project", down)
        failed = await client.post("/projects/lab/copy", json={"team_id": new["id"]})
        monkeypatch.setattr(project_collaboration, "publish_project", publish)
        row = await _row(session, ProjectTable, name="lab")
        assert (str(row.team_id), row.remote_id, row.modified) == (
            new["id"],
            None,
            True,
        )
        again = await client.post("/projects/lab/publish", json={})
        listed = (await client.get("/collaboration/projects")).json()
    assert failed.status_code == 503
    assert again.status_code == 200, again.text
    # The retry is a new item in the new team; the old team's is as it was.
    by_team = {p["team_id"]: p["head_version"] for p in listed}
    assert by_team == {old["id"]: 1, new["id"]: 1}
