"""
Projects on the collaboration server: publishing one with the skills it loads
(new, next version, unchanged, conflicts), the import list, and importing one
with the skill versions it names (new, renamed, updated in place, local
changes, skills already here), against a real server mounted in-process
(`collab_server` in conftest). Another installation is a second client with an
install id of its own.
"""

import shutil
import uuid

import pytest
from sqlmodel import select

from harness import api_client
from harness import seed_project as _seed_project
from harness.knowledge_bases import kb_row, make_kb, publish_kb_elsewhere, same_format
from vista_backend.agents.skills import read_skill
from vista_backend.config import ForumSettings, settings
from vista_backend.db.schemas import (
    KnowledgeBaseTable,
    LocalSettingsUpdate,
    ProjectTable,
    SkillTable,
)
from vista_backend.services import collaboration, local_settings
from vista_backend.services.collaboration import CollaborationClient
from vista_backend.services.project_collaboration import (
    KnowledgeBaseRef,
    ProjectManifest,
    SkillRef,
)
from vista_backend.services.skill_collaboration import SkillManifest, pack_skill

pytestmark = pytest.mark.anyio

URL = "http://collab.test:8002"


async def seed_project(session, **fields):
    """
    A project, committed: the test client's session commits nothing itself, and
    a publish starts a new transaction.
    """
    project = await _seed_project(session, **fields)
    await session.commit()
    return project


async def _put(client, session, name: str, body: dict):
    """`PUT /projects/{name}`, committed, as the app's request session would."""
    res = await client.put(f"/projects/{name}", json=body)
    await session.commit()
    return res


def _skill(name: str, body: str = "Mine.") -> dict:
    return {"name": name, "description": f"The {name} skill.", "body": body}


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "collaboration_server_url", None)
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    same_format(monkeypatch)


async def _use_server(session, url: str | None = URL):
    await local_settings.update(
        session,
        LocalSettingsUpdate.model_validate(
            {"collaboration_server_url": url, "display_name": "Ada"}
        ),
    )


def _other_install() -> CollaborationClient:
    """A second VISTA installation on the same server."""
    return CollaborationClient(URL, install_id=uuid.uuid4(), display_name="Bea")


def _body(project) -> dict:
    """A full `PUT /projects/{name}` body for a project's API representation."""
    return {
        key: project[key]
        for key in (
            "name",
            "description",
            "system_prompt",
            "skills",
            "knowledge_bases",
            "forum_repo_url",
            "tools",
            "usage_limits",
        )
    }


async def _publish_skill_elsewhere(tmp_path, name: str, body: str = "Theirs."):
    """Publish a skill from the other installation; returns its item id."""
    folder = tmp_path / "theirs" / uuid.uuid4().hex / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: The {name} skill.\n---\n\n{body}\n",
        encoding="utf-8",
    )
    archive = tmp_path / f"{uuid.uuid4().hex}.tar.gz"
    pack_skill(folder, archive)
    async with _other_install() as client:
        ref = await client.upload_blob(archive)
        item = await client.create_item(
            "skill",
            name,
            manifest=SkillManifest(
                name=name, description=f"The {name} skill."
            ).model_dump(),
            blob_ref=ref,
        )
    return item.id


async def _republish_skill_elsewhere(tmp_path, item_id, parent: int, name, body):
    folder = tmp_path / "theirs" / uuid.uuid4().hex / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Changed.\n---\n\n{body}\n",
        encoding="utf-8",
    )
    archive = tmp_path / f"{uuid.uuid4().hex}.tar.gz"
    pack_skill(folder, archive)
    async with _other_install() as client:
        ref = await client.upload_blob(archive)
        await client.publish_version(
            item_id,
            parent_version=parent,
            manifest=SkillManifest(name=name, description="Changed.").model_dump(),
            blob_ref=ref,
        )


def _manifest(name: str = "salt-lab", **fields) -> dict:
    return ProjectManifest(
        name=name,
        description=fields.pop("description", "Their project."),
        system_prompt=fields.pop("system_prompt", "Be careful."),
        tools=fields.pop("tools", ["*", "!agenthpc_*"]),
        usage_limits=fields.pop("usage_limits", {"request_limit": 40}),
        **fields,
    ).model_dump(mode="json")


async def _publish_project_elsewhere(name: str = "salt-lab", **fields) -> uuid.UUID:
    async with _other_install() as client:
        item = await client.create_item(
            "project", name, manifest=_manifest(name, **fields)
        )
    return item.id


async def _republish_project_elsewhere(item_id, parent: int, **fields) -> None:
    async with _other_install() as client:
        await client.publish_version(
            item_id, parent_version=parent, manifest=_manifest(**fields)
        )


async def _project(session, name: str) -> ProjectTable | None:
    session.expire_all()
    return (
        await session.exec(select(ProjectTable).where(ProjectTable.name == name))
    ).first()


async def _skill_row(session, name: str) -> SkillTable | None:
    session.expire_all()
    return (
        await session.exec(select(SkillTable).where(SkillTable.name == name))
    ).first()


async def _bundle(session, name: str) -> None:
    row = await _skill_row(session, name)
    assert row is not None
    row.bundled = True
    row.modified = False
    session.add(row)
    await session.commit()


async def _seed_kb(session, slug: str) -> None:
    """A knowledge base here, indexed, never published; committed."""
    kb_dir = settings.knowledge_bases_dir / slug
    make_kb(kb_dir)
    session.add(kb_row(kb_dir, slug))
    await session.commit()


async def _kb_row(session, slug: str) -> KnowledgeBaseTable | None:
    session.expire_all()
    return (
        await session.exec(
            select(KnowledgeBaseTable).where(KnowledgeBaseTable.slug == slug)
        )
    ).first()


# ---------------------------------------------------------------------------
# What a project records
# ---------------------------------------------------------------------------


async def test_a_new_project_is_modified_and_only_a_real_change_marks_it(
    collab_server, session
):
    await _use_server(session)
    await seed_project(session, name="salt")
    with api_client(session) as (client, _):
        created = (await client.get("/projects/salt")).json()
        assert (created["modified"], created["remote_id"]) == (True, None)
        await client.post("/projects/salt/publish", json={})
        published = (await client.get("/projects/salt")).json()
        assert published["modified"] is False

        same = await _put(client, session, "salt", _body(published))
        assert same.json()["modified"] is False
        changed = await _put(
            client, session, "salt", {**_body(published), "system_prompt": "New."}
        )
    assert changed.json()["modified"] is True


async def test_loading_a_skill_into_a_project_marks_it_modified(collab_server, session):
    await _use_server(session)
    await seed_project(session, name="salt")
    with api_client(session) as (client, _):
        await client.post("/projects/salt/publish", json={})
        await client.post("/skills", json={**_skill("salt-density"), "project": "salt"})
        project = (await client.get("/projects/salt")).json()
    assert project["skills"] == ["salt-density"]
    assert project["modified"] is True


# ---------------------------------------------------------------------------
# Publishing
# ---------------------------------------------------------------------------


async def test_without_a_server_publishing_says_why(session):
    await seed_project(session, name="salt")
    with api_client(session) as (client, _):
        res = await client.post("/projects/salt/publish", json={})
        listed = await client.get("/collaboration/projects")
    assert res.status_code == 503
    assert res.json() == {
        "detail": collaboration.NOT_CONFIGURED,
        "code": "collaboration_unavailable",
    }
    assert listed.status_code == 503


async def test_publishing_a_project_publishes_its_skills_first(collab_server, session):
    await _use_server(session)
    await _seed_kb(session, "salt-papers")
    project = await seed_project(
        session,
        name="salt",
        system_prompt="Use SI units.",
        knowledge_bases=["salt-papers"],
        usage_limits={"request_limit": 30},
    )
    with api_client(session) as (client, _):
        await client.post("/skills", json=_skill("salt-density"))
        await client.post("/skills", json=_skill("salt-analysis"))
        await _bundle(session, "salt-analysis")
        body = {
            **_body((await client.get("/projects/salt")).json()),
            "skills": ["salt-density", "salt-analysis"],
        }
        await _put(client, session, "salt", body)
        res = await client.post("/projects/salt/publish", json={})
    assert res.status_code == 200, res.text
    result = res.json()
    assert (
        result["status"],
        result["skills_published"],
        result["knowledge_bases_published"],
    ) == ("published", ["salt-density"], ["salt-papers"])
    published = result["project"]
    assert published["id"] == str(project.id)
    assert (
        published["origin_server"],
        published["remote_version"],
        published["modified"],
    ) == (URL, 1, False)

    skill = await _skill_row(session, "salt-density")
    assert (skill.remote_version, skill.modified) == (1, False)
    skill_id = skill.remote_id
    kb = await _kb_row(session, "salt-papers")
    assert (kb.remote_version, kb.modified) == (1, False)
    kb_id = kb.remote_id
    async with _other_install() as other:
        item = await other.get_item(uuid.UUID(published["remote_id"]))
        manifest = (await other.get_version(item.id, 1)).manifest
    assert (item.kind, item.name, item.created_by_name) == ("project", "salt", "Ada")
    assert manifest == {
        "format": 2,
        "name": "salt",
        "description": None,
        "system_prompt": "Use SI units.",
        "tools": ["*"],
        "usage_limits": published["usage_limits"],
        "forum_repo_url": None,
        "skills": [
            {
                "name": "salt-density",
                "bundled": False,
                "remote_id": str(skill_id),
                "version": 1,
            },
            {
                "name": "salt-analysis",
                "bundled": True,
                "remote_id": None,
                "version": None,
            },
        ],
        "knowledge_bases": [
            {"slug": "salt-papers", "remote_id": str(kb_id), "version": 1}
        ],
    }
    assert str(project.id) not in str(manifest)


async def test_an_unchanged_project_is_not_published_again(collab_server, session):
    await _use_server(session)
    await seed_project(session, name="salt")
    with api_client(session) as (client, _):
        first = (await client.post("/projects/salt/publish", json={})).json()
        again = await client.post("/projects/salt/publish", json={})
    assert again.status_code == 200, again.text
    assert (again.json()["status"], again.json()["project"]["remote_version"]) == (
        "unchanged",
        1,
    )
    async with _other_install() as other:
        item = await other.get_item(uuid.UUID(first["project"]["remote_id"]))
    assert item.head_version == 1


async def test_a_changed_skill_publishes_a_new_project_version(collab_server, session):
    """The project's own fields are unchanged, but the version it names is not."""
    await _use_server(session)
    await seed_project(session, name="salt")
    with api_client(session) as (client, _):
        await client.post("/skills", json={**_skill("salt-density"), "project": "salt"})
        await client.post("/projects/salt/publish", json={})
        await client.patch("/skills/salt-density", json={"body": "Use the new fit."})
        res = await client.post("/projects/salt/publish", json={})
    assert res.status_code == 200, res.text
    assert res.json()["skills_published"] == ["salt-density"]
    assert res.json()["project"]["remote_version"] == 2
    async with _other_install() as other:
        manifest = (
            await other.get_version(uuid.UUID(res.json()["project"]["remote_id"]), 2)
        ).manifest
    assert manifest["skills"][0]["version"] == 2


async def test_a_stale_publish_is_refused_before_any_skill_is_published(
    collab_server, session
):
    await _use_server(session)
    await seed_project(session, name="salt")
    with api_client(session) as (client, _):
        first = (await client.post("/projects/salt/publish", json={})).json()
        item_id = uuid.UUID(first["project"]["remote_id"])
        await _republish_project_elsewhere(item_id, 1, name="salt")
        await client.post("/skills", json={**_skill("salt-density"), "project": "salt"})

        stale = await client.post("/projects/salt/publish", json={})
        assert stale.status_code == 409, stale.text
        assert stale.json()["code"] == "version_conflict"
        assert (stale.json()["head_version"], stale.json()["remote_version"]) == (2, 1)
        skill = await _skill_row(session, "salt-density")
        assert skill.remote_id is None, "nothing was published"

        on_top = await client.post("/projects/salt/publish", json={"on_top_of": 2})
        assert on_top.status_code == 200, on_top.text
        assert on_top.json()["project"]["remote_version"] == 3

        as_new = await client.post("/projects/salt/publish", json={"as_new": True})
    assert as_new.status_code == 200, as_new.text
    assert as_new.json()["project"]["remote_id"] != first["project"]["remote_id"]
    assert as_new.json()["project"]["remote_version"] == 1


async def test_a_project_gone_from_the_server_can_be_published_as_new(
    collab_server, session
):
    await _use_server(session)
    await seed_project(session, name="salt")
    with api_client(session) as (client, _):
        await client.post("/projects/salt/publish", json={})
        row = await _project(session, "salt")
        row.remote_id = uuid.uuid4()
        session.add(row)
        await session.commit()
        res = await client.post("/projects/salt/publish", json={})
    assert res.status_code == 409
    assert res.json()["code"] == "remote_missing"


async def test_a_skill_that_needs_a_choice_stops_the_publish(
    collab_server, session, tmp_path
):
    await _use_server(session)
    await seed_project(session, name="salt")
    with api_client(session) as (client, _):
        await client.post("/skills", json={**_skill("salt-density"), "project": "salt"})
        skill = (await client.post("/skills/salt-density/publish", json={})).json()
        await _republish_skill_elsewhere(
            tmp_path, uuid.UUID(skill["remote_id"]), 1, "salt-density", "Theirs."
        )
        await client.patch("/skills/salt-density", json={"body": "Mine."})
        res = await client.post("/projects/salt/publish", json={})
    assert res.status_code == 409, res.text
    assert res.json()["code"] == "skill_conflict"
    assert (res.json()["skill"], res.json()["skill_code"]) == (
        "salt-density",
        "version_conflict",
    )
    row = await _project(session, "salt")
    assert row.remote_id is None


async def test_a_project_loading_a_skill_not_here_is_not_published(
    collab_server, session
):
    await _use_server(session)
    await seed_project(session, name="salt", skills=["deleted-skill"])
    with api_client(session) as (client, _):
        res = await client.post("/projects/salt/publish", json={})
    assert res.status_code == 400
    assert "deleted-skill" in res.json()["detail"]


# ---------------------------------------------------------------------------
# The import list
# ---------------------------------------------------------------------------


async def test_the_listing_says_what_each_project_is_and_what_is_here(
    collab_server, session
):
    await _use_server(session)
    theirs = await _publish_project_elsewhere("salt-lab")
    clash = await _publish_project_elsewhere("salt")
    await seed_project(session, name="salt")
    with api_client(session) as (client, _):
        mine = (await client.post("/projects/salt/publish", json={})).json()
        listed = await client.get("/collaboration/projects")
    assert listed.status_code == 200, listed.text
    by_id = {p["id"]: p for p in listed.json()}
    assert by_id[str(theirs)]["manifest"]["system_prompt"] == "Be careful."
    assert by_id[str(theirs)]["published_by_name"] == "Bea"
    assert (by_id[str(theirs)]["local"], by_id[str(theirs)]["name_taken"]) == (
        None,
        False,
    )
    assert by_id[mine["project"]["remote_id"]]["local"] == {
        "name": "salt",
        "remote_version": 1,
        "modified": False,
    }
    assert by_id[str(clash)]["name_taken"] is True


# ---------------------------------------------------------------------------
# Importing
# ---------------------------------------------------------------------------


async def test_importing_a_project_imports_its_skills(collab_server, session, tmp_path):
    await _use_server(session)
    # A different knowledge base here has the slug of theirs.
    await _seed_kb(session, "salt-papers")
    await seed_project(session, name="local")
    with api_client(session) as (client, _):
        await client.post("/skills", json=_skill("salt-analysis"))
        await _bundle(session, "salt-analysis")
    skill_id = await _publish_skill_elsewhere(tmp_path, "vllm-lux")
    async with _other_install() as other:
        kb_id = await publish_kb_elsewhere(other, tmp_path, "salt-papers")
    item_id = await _publish_project_elsewhere(
        skills=[
            SkillRef(name="vllm-lux", remote_id=skill_id, version=1),
            SkillRef(name="salt-analysis", bundled=True),
            SkillRef(name="their-own-bundled", bundled=True),
        ],
        knowledge_bases=[
            KnowledgeBaseRef(slug="salt-papers", remote_id=kb_id, version=1)
        ],
    )
    with api_client(session) as (client, _):
        res = await client.post(f"/collaboration/projects/{item_id}/import", json={})
        again = await client.post(f"/collaboration/projects/{item_id}/import", json={})
    assert res.status_code == 200, res.text
    result = res.json()
    assert result["status"] == "imported"
    assert [(s["name"], s["status"]) for s in result["skills"]] == [
        ("vllm-lux", "imported"),
        ("salt-analysis", "unchanged"),
        ("their-own-bundled", "missing"),
    ]
    assert [(k["slug"], k["status"]) for k in result["knowledge_bases"]] == [
        ("salt-papers-2", "imported"),
    ]
    assert result["forum_error"] is None
    project = result["project"]
    assert (
        project["name"],
        project["system_prompt"],
        project["skills"],
        project["knowledge_bases"],
        project["tools"],
        project["usage_limits"]["request_limit"],
    ) == (
        "salt-lab",
        "Be careful.",
        # A bundled skill this VISTA lacks is still named: the next version
        # must not lose it.
        ["vllm-lux", "salt-analysis", "their-own-bundled"],
        ["salt-papers-2"],
        ["*", "!agenthpc_*"],
        40,
    )
    assert (
        project["origin_server"],
        project["remote_id"],
        project["remote_version"],
        project["modified"],
    ) == (URL, str(item_id), 1, False)
    skill = await _skill_row(session, "vllm-lux")
    assert (skill.remote_id, skill.remote_version, skill.modified) == (
        skill_id,
        1,
        False,
    )
    kb = await _kb_row(session, "salt-papers-2")
    assert (kb.remote_id, kb.remote_version, kb.modified) == (kb_id, 1, False)
    assert (await _kb_row(session, "salt-papers")).remote_id is None
    assert again.json()["status"] == "unchanged"


async def test_an_import_never_takes_an_id_from_elsewhere(collab_server, session):
    """The seeded projects share fixed ids across installations."""
    await _use_server(session)
    here = await seed_project(session, name="salt")
    manifest = {**_manifest("salt-lab"), "id": str(here.id)}
    async with _other_install() as other:
        item = await other.create_item("project", "salt-lab", manifest=manifest)
    with api_client(session) as (client, _):
        res = await client.post(f"/collaboration/projects/{item.id}/import", json={})
    assert res.status_code == 200, res.text
    assert res.json()["project"]["id"] != str(here.id)
    assert (await _project(session, "salt")).id == here.id


async def test_a_taken_name_asks_for_a_new_one_before_importing_skills(
    collab_server, session, tmp_path
):
    await _use_server(session)
    await seed_project(session, name="salt-lab")
    skill_id = await _publish_skill_elsewhere(tmp_path, "vllm-lux")
    item_id = await _publish_project_elsewhere(
        skills=[SkillRef(name="vllm-lux", remote_id=skill_id, version=1)]
    )
    url = f"/collaboration/projects/{item_id}/import"
    with api_client(session) as (client, _):
        taken = await client.post(url, json={})
        assert await _skill_row(session, "vllm-lux") is None
        renamed = await client.post(url, json={"rename_to": "salt-lab-bea"})
    assert taken.status_code == 409
    assert (taken.json()["code"], taken.json()["name"]) == ("name_taken", "salt-lab")
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["project"]["name"] == "salt-lab-bea"


async def test_a_skill_whose_name_is_taken_is_imported_under_a_free_one(
    collab_server, session, tmp_path
):
    await _use_server(session)
    with api_client(session) as (client, _):
        await client.post("/skills", json=_skill("vllm-lux"))
        await client.post("/skills", json=_skill("vllm-lux-2"))
    skill_id = await _publish_skill_elsewhere(tmp_path, "vllm-lux")
    item_id = await _publish_project_elsewhere(
        skills=[SkillRef(name="vllm-lux", remote_id=skill_id, version=1)]
    )
    with api_client(session) as (client, _):
        res = await client.post(f"/collaboration/projects/{item_id}/import", json={})
    assert res.status_code == 200, res.text
    assert res.json()["project"]["skills"] == ["vllm-lux-3"]
    row = await _skill_row(session, "vllm-lux-3")
    assert read_skill(settings.data_dir / row.path).name == "vllm-lux-3"
    assert row.remote_id == skill_id


async def test_skills_already_here_are_updated_or_kept(
    collab_server, session, tmp_path
):
    await _use_server(session)
    older = await _publish_skill_elsewhere(tmp_path, "older", "One.")
    changed = await _publish_skill_elsewhere(tmp_path, "changed", "One.")
    newer = await _publish_skill_elsewhere(tmp_path, "newer", "One.")
    with api_client(session) as (client, _):
        for item in (older, changed, newer):
            await client.post(f"/collaboration/skills/{item}/import", json={})
        await client.patch("/skills/changed", json={"body": "Mine."})
    for item, name in ((older, "older"), (changed, "changed"), (newer, "newer")):
        await _republish_skill_elsewhere(tmp_path, item, 1, name, "Two.")
    with api_client(session) as (client, _):
        await client.post(f"/collaboration/skills/{newer}/import", json={})
    item_id = await _publish_project_elsewhere(
        skills=[
            SkillRef(name="older", remote_id=older, version=2),
            SkillRef(name="changed", remote_id=changed, version=2),
            SkillRef(name="newer", remote_id=newer, version=1),
        ]
    )
    with api_client(session) as (client, _):
        res = await client.post(f"/collaboration/projects/{item_id}/import", json={})
    assert res.status_code == 200, res.text
    assert [(s["name"], s["status"]) for s in res.json()["skills"]] == [
        ("older", "updated"),
        ("changed", "kept"),
        ("newer", "kept"),
    ]
    assert (await _skill_row(session, "older")).remote_version == 2
    kept = await _skill_row(session, "changed")
    assert (kept.remote_version, kept.modified) == (1, True)
    assert res.json()["project"]["skills"] == ["older", "changed", "newer"]
    # The kept copies are the skills' own state: the project's fields are the
    # version's, and a publish from here would publish those copies first.
    assert res.json()["project"]["modified"] is False


async def test_a_newer_version_updates_the_copy_in_place(collab_server, session):
    await _use_server(session)
    item_id = await _publish_project_elsewhere(system_prompt="One.")
    url = f"/collaboration/projects/{item_id}/import"
    with api_client(session) as (client, pool):
        first = (await client.post(url, json={})).json()["project"]
        await _republish_project_elsewhere(item_id, 1, system_prompt="Two.")
        updated = await client.post(url, json={})
        assert updated.status_code == 200, updated.text
        assert updated.json()["status"] == "updated"
        project = updated.json()["project"]
        assert (project["id"], project["name"], project["system_prompt"]) == (
            first["id"],
            "salt-lab",
            "Two.",
        )

        await _put(
            client, session, "salt-lab", {**_body(project), "system_prompt": "Mine."}
        )
        await _republish_project_elsewhere(item_id, 2, system_prompt="Three.")
        warned = await client.post(url, json={})
        assert warned.status_code == 409
        assert warned.json()["code"] == "modified"
        assert (await _project(session, "salt-lab")).system_prompt == "Mine."
        replaced = await client.post(url, json={"replace_modified": True})
    assert replaced.status_code == 200, replaced.text
    row = await _project(session, "salt-lab")
    assert (row.system_prompt, row.remote_version, row.modified) == (
        "Three.",
        3,
        False,
    )


async def test_an_item_that_is_not_a_project_is_not_imported(
    collab_server, session, tmp_path
):
    await _use_server(session)
    skill_id = await _publish_skill_elsewhere(tmp_path, "vllm-lux")
    async with _other_install() as other:
        unreadable = await other.create_item("project", "odd", manifest={"format": 9})
    with api_client(session) as (client, _):
        res = await client.post(f"/collaboration/projects/{skill_id}/import", json={})
        odd = await client.post(
            f"/collaboration/projects/{unreadable.id}/import", json={}
        )
    assert res.status_code == 400
    assert "not a project" in res.json()["detail"]
    assert odd.status_code == 502
    assert "not a project this VISTA can import" in odd.json()["detail"]


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
async def test_a_forum_that_cannot_be_reached_does_not_fail_the_import(
    collab_server, session, tmp_path, monkeypatch, git_ok
):
    """Saving such a project is refused; importing it leaves the lab to fix."""
    monkeypatch.setattr(settings, "forum", ForumSettings(enabled=True, timeout=30.0))
    await _use_server(session)
    unreachable = str(tmp_path / "no-such-forge" / "forum.git")
    item_id = await _publish_project_elsewhere(forum_repo_url=unreachable)
    with api_client(session) as (client, _):
        res = await client.post(f"/collaboration/projects/{item_id}/import", json={})
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "imported"
    assert res.json()["project"]["forum_repo_url"] == unreachable
    assert "could not reach" in res.json()["forum_error"]
    assert await _project(session, "salt-lab") is not None


async def test_republishing_an_untouched_import_publishes_nothing(
    collab_server, session, tmp_path
):
    """
    Renamed here, with a renamed skill and a renamed knowledge base, it is
    still the version it was imported as.
    """
    await _use_server(session)
    await seed_project(session, name="salt-lab")
    await _seed_kb(session, "salt-papers")
    with api_client(session) as (client, _):
        await client.post("/skills", json=_skill("vllm-lux"))
    skill_id = await _publish_skill_elsewhere(tmp_path, "vllm-lux")
    async with _other_install() as other:
        kb_id = await publish_kb_elsewhere(other, tmp_path, "salt-papers")
    item_id = await _publish_project_elsewhere(
        description="",
        skills=[SkillRef(name="vllm-lux", remote_id=skill_id, version=1)],
        knowledge_bases=[
            KnowledgeBaseRef(slug="salt-papers", remote_id=kb_id, version=1)
        ],
    )
    with api_client(session) as (client, _):
        imported = await client.post(
            f"/collaboration/projects/{item_id}/import",
            json={"rename_to": "salt-lab-bea"},
        )
        project = imported.json()["project"]
        assert (
            project["skills"],
            project["knowledge_bases"],
            project["description"],
            project["modified"],
        ) == (["vllm-lux-2"], ["salt-papers-2"], None, False)
        again = await client.post("/projects/salt-lab-bea/publish", json={})
    assert again.status_code == 200, again.text
    assert (
        again.json()["status"],
        again.json()["skills_published"],
        again.json()["knowledge_bases_published"],
    ) == ("unchanged", [], [])
    async with _other_install() as other:
        assert (await other.get_item(item_id)).head_version == 1


async def test_a_skill_that_needs_a_choice_stops_the_import(
    collab_server, session, tmp_path, monkeypatch
):
    """Not under the project's own codes, which would offer the wrong choice."""
    from vista_backend.services import project_collaboration
    from vista_backend.services.skill_collaboration import SkillConflict

    async def changed_meanwhile(*args, **kwargs):
        raise SkillConflict("vllm-lux changed here meanwhile.", "modified")

    await _use_server(session)
    skill_id = await _publish_skill_elsewhere(tmp_path, "vllm-lux")
    item_id = await _publish_project_elsewhere(
        skills=[SkillRef(name="vllm-lux", remote_id=skill_id, version=1)]
    )
    monkeypatch.setattr(project_collaboration, "import_skill", changed_meanwhile)
    with api_client(session) as (client, _):
        res = await client.post(f"/collaboration/projects/{item_id}/import", json={})
    assert res.status_code == 409, res.text
    assert (res.json()["code"], res.json()["skill"], res.json()["skill_code"]) == (
        "skill_conflict",
        "vllm-lux",
        "modified",
    )
    assert await _project(session, "salt-lab") is None


async def test_a_project_that_cannot_be_imported_imports_no_skill(
    collab_server, session, tmp_path
):
    await _use_server(session)
    skill_id = await _publish_skill_elsewhere(tmp_path, "vllm-lux")
    item_id = await _publish_project_elsewhere(
        skills=[SkillRef(name="vllm-lux", remote_id=skill_id, version=1)],
        usage_limits={"request_limit": "lots"},
    )
    with api_client(session) as (client, _):
        res = await client.post(f"/collaboration/projects/{item_id}/import", json={})
    assert res.status_code == 502, res.text
    assert "not a project this VISTA can import" in res.json()["detail"]
    assert await _skill_row(session, "vllm-lux") is None


async def test_a_bundled_skill_this_vista_lacks_is_published_as_bundled_again(
    collab_server, session
):
    """The import kept its name; a publish from here keeps the reference."""
    await _use_server(session)
    item_id = await _publish_project_elsewhere(
        skills=[SkillRef(name="their-own-bundled", bundled=True)]
    )
    with api_client(session) as (client, _):
        project = (
            await client.post(f"/collaboration/projects/{item_id}/import", json={})
        ).json()["project"]
        await _put(
            client, session, "salt-lab", {**_body(project), "system_prompt": "Mine."}
        )
        res = await client.post("/projects/salt-lab/publish", json={})
    assert res.status_code == 200, res.text
    async with _other_install() as other:
        manifest = (await other.get_version(item_id, 2)).manifest
    assert manifest["skills"] == [
        {
            "name": "their-own-bundled",
            "bundled": True,
            "remote_id": None,
            "version": None,
        }
    ]
    assert manifest["system_prompt"] == "Mine."


async def test_a_bundled_skill_shadowed_here_leaves_the_project_modified(
    collab_server, session
):
    """The one reference an import loses, so a publish from here would drop it."""
    await _use_server(session)
    with api_client(session) as (client, _):
        await client.post("/skills", json=_skill("salt-analysis"))  # not bundled
    item_id = await _publish_project_elsewhere(
        skills=[SkillRef(name="salt-analysis", bundled=True)]
    )
    with api_client(session) as (client, _):
        res = await client.post(f"/collaboration/projects/{item_id}/import", json={})
    assert res.status_code == 200, res.text
    assert [(s["name"], s["status"]) for s in res.json()["skills"]] == [
        ("salt-analysis", "missing")
    ]
    assert (res.json()["project"]["skills"], res.json()["project"]["modified"]) == (
        [],
        True,
    )


async def test_a_skill_reference_must_be_a_skill_name(collab_server, session):
    """A bundled name is kept on the project and names a folder: never a path."""
    await _use_server(session)
    manifest = _manifest()
    manifest["skills"] = [
        {"name": "../../outside", "bundled": True, "remote_id": None, "version": None}
    ]
    async with _other_install() as other:
        item = await other.create_item("project", "salt-lab", manifest=manifest)
    with api_client(session) as (client, _):
        listed = (await client.get("/collaboration/projects")).json()
        res = await client.post(f"/collaboration/projects/{item.id}/import", json={})
    assert listed[0]["manifest"] is None
    assert res.status_code == 502
    assert "not a project this VISTA can import" in res.json()["detail"]
    assert await _project(session, "salt-lab") is None


async def test_a_name_no_route_could_reach_is_refused(collab_server, session):
    await _use_server(session)
    item_id = await _publish_project_elsewhere("salts/v2")
    url = f"/collaboration/projects/{item_id}/import"
    with api_client(session) as (client, _):
        theirs = await client.post(url, json={})
        typed = await client.post(url, json={"rename_to": "mine/v2"})
        fine = await client.post(url, json={"rename_to": "salts v2"})
    assert theirs.status_code == 409
    assert (theirs.json()["code"], theirs.json()["name"]) == (
        "name_invalid",
        "salts/v2",
    )
    assert typed.status_code == 400
    assert "2 to 80 letters" in typed.json()["detail"]
    assert fine.status_code == 200, fine.text
    assert fine.json()["project"]["name"] == "salts v2"


# ---------------------------------------------------------------------------
# Knowledge bases
# ---------------------------------------------------------------------------


async def test_a_knowledge_base_not_here_stops_the_publish_before_anything_is_sent(
    collab_server, session
):
    await _use_server(session)
    await seed_project(session, name="salt", knowledge_bases=["gone-papers"])
    with api_client(session) as (client, _):
        await client.post("/skills", json={**_skill("salt-density"), "project": "salt"})
        res = await client.post("/projects/salt/publish", json={})
    assert res.status_code == 400
    assert "gone-papers" in res.json()["detail"]
    assert (await _skill_row(session, "salt-density")).remote_id is None


async def test_a_knowledge_base_deleted_here_is_carried_from_the_version(
    collab_server, session, tmp_path
):
    """Like a bundled skill this VISTA lacks: the next version must not lose it."""
    await _use_server(session)
    async with _other_install() as other:
        kb_id = await publish_kb_elsewhere(other, tmp_path, "salt-papers")
    item_id = await _publish_project_elsewhere(
        knowledge_bases=[
            KnowledgeBaseRef(slug="salt-papers", remote_id=kb_id, version=1)
        ]
    )
    with api_client(session) as (client, _):
        await client.post(f"/collaboration/projects/{item_id}/import", json={})
        await client.delete("/knowledge-bases/salt-papers")
        await session.commit()
        project = (await client.get("/projects/salt-lab")).json()
        await _put(
            client, session, "salt-lab", {**_body(project), "system_prompt": "New."}
        )
        res = await client.post("/projects/salt-lab/publish", json={})
    assert res.status_code == 200, res.text
    async with _other_install() as other:
        manifest = (await other.get_version(item_id, 2)).manifest
    assert manifest["knowledge_bases"] == [
        {"slug": "salt-papers", "remote_id": str(kb_id), "version": 1}
    ]


async def test_a_changed_knowledge_base_is_published_again_with_its_project(
    collab_server, session
):
    await _use_server(session)
    await _seed_kb(session, "salt-papers")
    await seed_project(session, name="salt", knowledge_bases=["salt-papers"])
    with api_client(session) as (client, _):
        await client.post("/projects/salt/publish", json={})
        await client.put("/knowledge-bases/salt-papers", json={"name": "Renamed"})
        await session.commit()
        res = await client.post("/projects/salt/publish", json={})
    assert res.status_code == 200, res.text
    assert (res.json()["status"], res.json()["knowledge_bases_published"]) == (
        "published",
        ["salt-papers"],
    )
    assert res.json()["project"]["remote_version"] == 2
    assert (await _kb_row(session, "salt-papers")).remote_version == 2


async def test_a_knowledge_base_this_vista_cannot_search_stops_the_import_first(
    collab_server, session, tmp_path
):
    await _use_server(session)
    skill_id = await _publish_skill_elsewhere(tmp_path, "vllm-lux")
    async with _other_install() as other:
        kb_id = await publish_kb_elsewhere(
            other, tmp_path, "salt-papers", embedding_revision="9" * 40
        )
    item_id = await _publish_project_elsewhere(
        skills=[SkillRef(name="vllm-lux", remote_id=skill_id, version=1)],
        knowledge_bases=[
            KnowledgeBaseRef(slug="salt-papers", remote_id=kb_id, version=1)
        ],
    )
    with api_client(session) as (client, _):
        res = await client.post(f"/collaboration/projects/{item_id}/import", json={})
    assert res.status_code == 409
    assert res.json()["detail"].startswith("salt-lab was not imported:")
    assert await _skill_row(session, "vllm-lux") is None
    assert await _kb_row(session, "salt-papers") is None
    assert await _project(session, "salt-lab") is None


async def test_a_newer_copy_of_a_knowledge_base_is_kept(
    collab_server, session, tmp_path
):
    await _use_server(session)
    async with _other_install() as other:
        kb_id = await publish_kb_elsewhere(other, tmp_path, "salt-papers")
        await publish_kb_elsewhere(
            other, tmp_path, "salt-papers", item_id=kb_id, parent=1
        )
    item_id = await _publish_project_elsewhere(
        knowledge_bases=[
            KnowledgeBaseRef(slug="salt-papers", remote_id=kb_id, version=1)
        ]
    )
    with api_client(session) as (client, _):
        await client.post(f"/collaboration/knowledge-bases/{kb_id}/import", json={})
        res = await client.post(f"/collaboration/projects/{item_id}/import", json={})
    assert res.status_code == 200, res.text
    [outcome] = res.json()["knowledge_bases"]
    assert (outcome["slug"], outcome["status"]) == ("salt-papers", "kept")
    assert "version 2 here" in outcome["detail"]
    assert res.json()["project"]["knowledge_bases"] == ["salt-papers"]
    assert res.json()["project"]["modified"] is False
    assert (await _kb_row(session, "salt-papers")).remote_version == 2


async def test_a_knowledge_base_that_needs_a_choice_stops_the_import(
    collab_server, session, tmp_path, monkeypatch
):
    from vista_backend.services import project_collaboration
    from vista_backend.services.knowledge_base_collaboration import (
        KnowledgeBaseConflict,
    )

    async def taken_meanwhile(*args, **kwargs):
        raise KnowledgeBaseConflict("salt-papers was taken meanwhile.", "name_taken")

    await _use_server(session)
    async with _other_install() as other:
        kb_id = await publish_kb_elsewhere(other, tmp_path, "salt-papers")
    item_id = await _publish_project_elsewhere(
        knowledge_bases=[
            KnowledgeBaseRef(slug="salt-papers", remote_id=kb_id, version=1)
        ]
    )
    monkeypatch.setattr(project_collaboration, "import_knowledge_base", taken_meanwhile)
    with api_client(session) as (client, _):
        res = await client.post(f"/collaboration/projects/{item_id}/import", json={})
    assert res.status_code == 409, res.text
    body = res.json()
    assert (body["code"], body["knowledge_base"], body["knowledge_base_code"]) == (
        "knowledge_base_conflict",
        "salt-papers",
        "name_taken",
    )
    assert await _project(session, "salt-lab") is None
