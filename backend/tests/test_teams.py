"""
Teams, from this installation's side: that a new project, skill or knowledge
base is private unless made for a team's project, that a project uses only its
own team's, that a private one joins a team only when first published into it
(`services/team_publish.py`), and stays there. Against a real collaboration
server mounted in-process (`collab_server` in conftest).
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
from vista_backend.services.collaboration import CollaborationClient

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


async def _publish_to(client, kind: str, name: str, team_id) -> dict:
    """First publish of private `name` (a `kind` route: skills, projects, knowledge-bases) into `team_id`."""
    res = await client.post(
        f"/{kind}/{name}/publish-to-team", json={"team_id": str(team_id)}
    )
    assert res.status_code == 200, res.text
    return res.json()


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
# The status, and leaving a team
# ---------------------------------------------------------------------------


async def test_the_status_lists_the_teams(collab_server, session):
    await _use_server(session)
    with api_client(session) as (client, _):
        await _team(client)
        status = (await client.get("/collaboration/status")).json()
    assert [t["name"] for t in status["teams"]] == ["public", "salts"]
    assert "team_id" not in status


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
        await _skill(client, "teamed")
        await session.commit()
        await _publish_to(client, "skills", "teamed", team["id"])
    await _use_server(session, "http://elsewhere.test:8002")
    row = await _row(session, SkillTable, name="teamed")
    assert team_of(row, await current_url(session)) is None
    # Nothing was erased: back on its server, it is in its team again.
    await _use_server(session)
    row = await _row(session, SkillTable, name="teamed")
    assert str(team_of(row, await current_url(session))) == team["id"]


# ---------------------------------------------------------------------------
# What a new resource joins, and what a project may use
# ---------------------------------------------------------------------------


async def test_new_resources_are_private_unless_made_for_a_teams_project(
    collab_server, session
):
    await _use_server(session)
    with api_client(session) as (client, _):
        team = await _team(client)
        skill = await _skill(client, "new-skill")
        project = await _project(client, "new-project")
        kb = await client.post(
            "/knowledge-bases", json={"slug": "new-papers", "name": "Papers"}
        )
        await session.commit()
        await _publish_to(client, "projects", "new-project", team["id"])
        for_team = await _skill(client, "for-team", project="new-project")
    assert skill.json()["team_id"] is None
    assert project.json()["team_id"] is None
    assert kb.status_code == 201, kb.text
    assert kb.json()["team_id"] is None
    assert for_team.status_code == 201, for_team.text
    assert for_team.json()["team_id"] == team["id"]


async def test_a_project_uses_only_its_own_teams(collab_server, session):
    await _use_server(session)
    with api_client(session) as (client, _):
        team = await _team(client)
        await _skill(client, "private-skill")
        await _project(client, "team-lab")
        await session.commit()
        await _publish_to(client, "projects", "team-lab", team["id"])
        await _skill(client, "team-skill", project="team-lab")
        update = await client.put(
            "/projects/team-lab", json={"name": "team-lab", "skills": ["private-skill"]}
        )
        private_mixed = await _project(client, "private-mixed", skills=["team-skill"])
    for refused in (update, private_mixed):
        assert refused.status_code == 400, refused.text
        assert "another team" in refused.json()["detail"]


async def test_a_private_resource_needs_a_team_to_publish(collab_server, session):
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
        assert "choose a team" in res.json()["detail"]
    async with _bea() as bea:
        assert await bea.list_items() == []


async def test_the_copy_routes_are_gone(collab_server, session):
    await _use_server(session)
    with api_client(session) as (client, _):
        team = await _team(client)
        await _skill(client, "density")
        res = await client.post("/skills/density/copy", json={"team_id": team["id"]})
    assert res.status_code in (404, 405)


# ---------------------------------------------------------------------------
# Publishing and importing by team
# ---------------------------------------------------------------------------


async def test_new_versions_publish_to_its_own_team(collab_server, session):
    await _use_server(session)
    with api_client(session) as (client, _):
        team = await _team(client)
        await _skill(client, "team-skill")
        await session.commit()
        await _publish_to(client, "skills", "team-skill", team["id"])
        await client.patch("/skills/team-skill", json={"description": "Changed."})
        await session.commit()
        res = await client.post("/skills/team-skill/publish", json={})
        listed = (await client.get("/collaboration/skills")).json()
    assert res.status_code == 200, res.text
    assert [(s["name"], s["team_id"], s["head_version"]) for s in listed] == [
        ("team-skill", team["id"], 2)
    ]
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
# Publishing a private one into a team
# ---------------------------------------------------------------------------


async def test_a_private_skill_is_published_into_the_team(collab_server, session):
    await _use_server(session)
    with api_client(session) as (client, _):
        await _skill(client, "density")
        team = await _team(client)
        await session.commit()
        result = await _publish_to(client, "skills", "density", team["id"])
    assert result["published"] == [
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


async def test_a_skill_a_project_uses_is_imported_beside_it(collab_server, session):
    await _use_server(session)
    with api_client(session) as (client, _):
        await _skill(client, "density")
        await _project(client, "lab", skills=["density"])
        team = await _team(client)
        await session.commit()
        result = await _publish_to(client, "skills", "density", team["id"])
    assert result["published"] == [
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


async def test_a_project_is_published_with_everything_it_uses(collab_server, session):
    await _use_server(session)
    await _kb(session, "papers")
    with api_client(session) as (client, _):
        await _skill(client, "density")
        await _project(client, "lab", skills=["density"], knowledge_bases=["papers"])
        team = await _team(client)
        await session.commit()
        result = await _publish_to(client, "projects", "lab", team["id"])
    assert [(c["kind"], c["name"], c["how"]) for c in result["published"]] == [
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


async def test_a_shared_dependency_stays_private_for_its_other_project(
    collab_server, session
):
    await _use_server(session)
    with api_client(session) as (client, _):
        await _skill(client, "density")
        await _project(client, "lab", skills=["density"])
        await _project(client, "other-lab", skills=["density"])
        team = await _team(client)
        await session.commit()
        result = await _publish_to(client, "projects", "lab", team["id"])
    assert result["published"][0]["local_name"] == "density-2"
    assert (await _row(session, ProjectTable, name="lab")).skills == ["density-2"]
    other = await _row(session, ProjectTable, name="other-lab")
    assert (other.skills, other.team_id) == (["density"], None)
    assert (await _row(session, SkillTable, name="density")).team_id is None


async def test_it_needs_a_team_one_is_in_and_stays_in_its_first(collab_server, session):
    await _use_server(session)
    async with _bea() as bea:
        theirs = await bea.create_team("theirs")
    with api_client(session) as (client, _):
        await _skill(client, "density")
        mine = await _team(client)
        other = await _team(client, "other")
        await session.commit()
        not_member = await client.post(
            "/skills/density/publish-to-team", json={"team_id": str(theirs.id)}
        )
        await _publish_to(client, "skills", "density", mine["id"])
        again = await client.post(
            "/skills/density/publish-to-team", json={"team_id": other["id"]}
        )
    assert not_member.status_code == 400, not_member.text
    assert not_member.json()["code"] == "not_in_team"
    assert again.status_code == 400
    assert "in a team already" in again.json()["detail"]


async def test_a_project_using_what_is_not_here_is_not_published(
    collab_server, session
):
    await _use_server(session)
    with api_client(session) as (client, _):
        team = await _team(client)
        await _project(client, "lab")
    project = await _row(session, ProjectTable, name="lab")
    project.skills = ["absent"]
    session.add(project)
    await session.commit()
    with api_client(session) as (client, _):
        res = await client.post(
            "/projects/lab/publish-to-team", json={"team_id": team["id"]}
        )
    assert res.status_code == 400
    assert "absent" in res.json()["detail"]
    async with _bea() as bea:
        assert await bea.list_items() == []


async def test_a_resource_of_a_team_one_has_left_is_published_from_here(
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
        await _skill(client, "density")
        await session.commit()
        await _publish_to(client, "skills", "density", old["id"])
        left = await client.delete(f"/collaboration/teams/{old['id']}/members/me")
        await client.patch("/skills/density", json={"description": "Changed."})
        await session.commit()
        # Its new versions cannot go to the team it has left...
        plain = await client.post("/skills/density/publish", json={})
        # ...so it is published into another, as a private one is.
        result = await _publish_to(client, "skills", "density", new["id"])
    assert left.status_code == 204, left.text
    assert plain.status_code == 400, plain.text
    assert plain.json()["code"] == "not_in_team"
    assert "no longer in" in plain.json()["detail"]
    # The old item is out of reach, so it is published from here.
    assert result["published"][0]["how"] == "published"
    assert str((await _row(session, SkillTable, name="density")).team_id) == new["id"]


async def test_a_bundled_skill_stays_private_when_its_project_is_published(
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
        result = await _publish_to(client, "projects", "lab", team["id"])
    assert result["published"][0] == {
        "kind": "skill",
        "name": "salt-analysis",
        "local_name": "salt-analysis-2",
        "how": "imported",
    }
    assert (await _row(session, SkillTable, name="salt-analysis")).team_id is None
    assert (await _row(session, ProjectTable, name="lab")).skills == ["salt-analysis-2"]


async def test_a_project_whose_own_publish_fails_is_left_in_the_team(
    collab_server, session, monkeypatch
):
    from vista_backend.services import project_collaboration
    from vista_backend.services.collaboration import CollaborationUnavailable

    await _use_server(session)
    with api_client(session) as (client, _):
        team = await _team(client)
        await _project(client, "lab")
        await session.commit()
        publish = project_collaboration.publish_project

        async def down(*args, **kwargs):
            raise CollaborationUnavailable("The server went away.")

        monkeypatch.setattr(project_collaboration, "publish_project", down)
        failed = await client.post(
            "/projects/lab/publish-to-team", json={"team_id": team["id"]}
        )
        monkeypatch.setattr(project_collaboration, "publish_project", publish)
        row = await _row(session, ProjectTable, name="lab")
        assert (str(row.team_id), row.remote_id, row.modified) == (
            team["id"],
            None,
            True,
        )
        # In the team now: publishing it into that team again finishes it,
        # as a page not yet showing it there would ask.
        again = await client.post(
            "/projects/lab/publish-to-team", json={"team_id": team["id"]}
        )
        listed = (await client.get("/collaboration/projects")).json()
    assert failed.status_code == 503
    assert again.status_code == 200, again.text
    assert [(p["team_id"], p["head_version"]) for p in listed] == [(team["id"], 1)]


async def test_a_project_left_in_the_team_is_finished_by_plain_publish_too(
    collab_server, session, monkeypatch
):
    from vista_backend.services import project_collaboration
    from vista_backend.services.collaboration import CollaborationUnavailable

    await _use_server(session)
    with api_client(session) as (client, _):
        team = await _team(client)
        other = await _team(client, "other")
        await _project(client, "lab")
        await session.commit()
        publish = project_collaboration.publish_project

        async def down(*args, **kwargs):
            raise CollaborationUnavailable("The server went away.")

        monkeypatch.setattr(project_collaboration, "publish_project", down)
        await client.post("/projects/lab/publish-to-team", json={"team_id": team["id"]})
        monkeypatch.setattr(project_collaboration, "publish_project", publish)
        elsewhere = await client.post(
            "/projects/lab/publish-to-team", json={"team_id": other["id"]}
        )
        again = await client.post("/projects/lab/publish", json={})
    # It is in its team: only that team finishes it.
    assert elsewhere.status_code == 400, elsewhere.text
    assert again.status_code == 200, again.text


async def test_a_project_publish_that_fails_part_way_puts_back_what_moved(
    collab_server, session, monkeypatch
):
    from vista_backend.services import skill_collaboration
    from vista_backend.services.collaboration import CollaborationUnavailable

    await _use_server(session)
    with api_client(session) as (client, _):
        await _skill(client, "first")
        await _skill(client, "second")
        await _project(client, "lab", skills=["first", "second"])
        team = await _team(client)
        await session.commit()
        publish = skill_collaboration.publish_skill

        async def second_fails(session, local, name, **kwargs):
            if name == "second":
                raise CollaborationUnavailable("The server went away.")
            return await publish(session, local, name, **kwargs)

        monkeypatch.setattr(skill_collaboration, "publish_skill", second_fails)
        failed = await client.post(
            "/projects/lab/publish-to-team", json={"team_id": team["id"]}
        )
        first = await _row(session, SkillTable, name="first")
        assert (first.team_id, first.remote_id) == (None, None)
        project = await _row(session, ProjectTable, name="lab")
        assert (project.team_id, project.skills) == (None, ["first", "second"])
        monkeypatch.setattr(skill_collaboration, "publish_skill", publish)
        again = await client.post(
            "/projects/lab/publish-to-team", json={"team_id": team["id"]}
        )
    assert failed.status_code == 503
    assert again.status_code == 200, again.text
    assert str((await _row(session, ProjectTable, name="lab")).team_id) == team["id"]


async def test_a_failed_project_publish_removes_what_it_imported_beside(
    collab_server, session, monkeypatch
):
    from vista_backend.services import skill_collaboration
    from vista_backend.services.collaboration import CollaborationUnavailable

    await _use_server(session)
    with api_client(session) as (client, _):
        await _skill(client, "shared")
        await _skill(client, "second")
        await _project(client, "lab", skills=["shared", "second"])
        await _project(client, "other-lab", skills=["shared"])
        team = await _team(client)
        await session.commit()
        publish = skill_collaboration.publish_skill

        async def second_fails(session, local, name, **kwargs):
            if name == "second":
                raise CollaborationUnavailable("The server went away.")
            return await publish(session, local, name, **kwargs)

        monkeypatch.setattr(skill_collaboration, "publish_skill", second_fails)
        failed = await client.post(
            "/projects/lab/publish-to-team", json={"team_id": team["id"]}
        )
        monkeypatch.setattr(skill_collaboration, "publish_skill", publish)
        after_failure = sorted(s["name"] for s in (await client.get("/skills")).json())
        again = await client.post(
            "/projects/lab/publish-to-team", json={"team_id": team["id"]}
        )
        after_retry = sorted(s["name"] for s in (await client.get("/skills")).json())
    assert failed.status_code == 503
    # `shared` stayed private for other-lab; its team copy, shared-2, went again.
    assert "shared-2" not in after_failure
    assert again.status_code == 200, again.text
    assert [n for n in after_retry if n.startswith("shared")] == ["shared", "shared-2"]


async def test_a_project_publish_asks_for_the_teams_once(
    collab_server, session, monkeypatch
):
    await _use_server(session)
    await _kb(session, "papers")
    with api_client(session) as (client, _):
        await _skill(client, "first")
        await _skill(client, "second")
        await _project(
            client, "lab", skills=["first", "second"], knowledge_bases=["papers"]
        )
        team = await _team(client)
        await session.commit()
        me = CollaborationClient.me
        asked = []

        async def counted(self):
            asked.append(1)
            return await me(self)

        monkeypatch.setattr(CollaborationClient, "me", counted)
        await _publish_to(client, "projects", "lab", team["id"])
    assert len(asked) == 1


async def test_a_knowledge_base_made_for_a_project_joins_its_team(
    collab_server, session
):
    await _use_server(session)
    with api_client(session) as (client, _):
        team = await _team(client)
        await _project(client, "team-lab")
        await session.commit()
        await _publish_to(client, "projects", "team-lab", team["id"])
        res = await client.post(
            "/knowledge-bases",
            json={"slug": "lab-papers", "name": "Papers", "project": "team-lab"},
        )
        attach = await client.put(
            "/projects/team-lab",
            json={"name": "team-lab", "knowledge_bases": ["lab-papers"]},
        )
    assert res.status_code == 201, res.text
    assert res.json()["team_id"] == team["id"]
    assert attach.status_code == 200, attach.text


async def test_a_team_on_another_server_is_sent_as_private(collab_server, session):
    await _use_server(session)
    with api_client(session) as (client, _):
        team = await _team(client)
        await _skill(client, "teamed")
        await session.commit()
        await _publish_to(client, "skills", "teamed", team["id"])
    await _use_server(session, "http://elsewhere.test:8002")
    with api_client(session) as (client, _):
        elsewhere = (await client.get("/skills")).json()
    await _use_server(session)
    with api_client(session) as (client, _):
        back = (await client.get("/skills")).json()
    assert [s["team_id"] for s in elsewhere if s["name"] == "teamed"] == [None]
    assert [s["team_id"] for s in back if s["name"] == "teamed"] == [team["id"]]


async def test_a_hosted_forum_joins_the_team_its_project_is_published_into(
    loopback_server, git_ok, session, monkeypatch
):
    """
    A new project's hosted forum is its creator's alone; publishing the project
    into a team links it to the item, so that team, and only it, can read it.
    """
    from vista_backend.config import ForumSettings

    monkeypatch.setattr(settings, "forum", ForumSettings(enabled=True, timeout=60.0))
    await _use_server(session, loopback_server.url)
    outsider = uuid.uuid4()
    with loopback_server.api(BEA, "Bea") as bea:
        assert bea.get("/teams").status_code == 200  # Bea signs in, to be added
    with api_client(session) as (client, _):
        created = await _project(client, "salt-lab")
        assert created.status_code == 201, created.text
        url = created.json()["forum_repo_url"]
        assert url and url.startswith(loopback_server.url)
        forum = url.rsplit("/", 1)[-1].removesuffix(".git")
        await session.commit()
        team = await _team(client)
        added = await client.post(
            f"/collaboration/teams/{team['id']}/members", json={"sub": str(BEA)}
        )
        assert added.status_code == 201, added.text
        assert not loopback_server.forum_exists(BEA, forum)

        result = await _publish_to(client, "projects", "salt-lab", team["id"])
    assert result["forum_error"] is None
    assert loopback_server.forum_exists(BEA, forum)
    assert not loopback_server.forum_exists(outsider, forum)
