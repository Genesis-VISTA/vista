"""
Skills on the collaboration server: the archive a version holds, publishing
(new, next version, conflicts and the researcher's choices), the Skill Hub's
listing, and importing (new, renamed, updated in place, local changes), against
a real server mounted in-process (`collab_server` in conftest). Another
installation is a second client with an install id of its own.
"""

import io
import tarfile
import uuid
from pathlib import Path

import pytest
from sqlmodel import select

from harness import api_client, seed_project
from vista_backend.agents.skill_import import SkillImportError
from vista_backend.agents.skills import read_skill
from vista_backend.config import settings
from vista_backend.db.schemas import LocalSettingsUpdate, ProjectTable, SkillTable
from vista_backend.services import collaboration, local_settings
from vista_backend.services.collaboration import CollaborationClient
from vista_backend.services.skill_collaboration import (
    SkillManifest,
    _stage,
    pack_skill,
    unpack_skill,
)

pytestmark = pytest.mark.anyio

URL = "http://collab.test:8002"

SKILL = {
    "name": "salt-density",
    "description": "Estimate a molten salt's density.",
    "body": "# Salt density\n\nUse the Redlich-Kister fit.",
}


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


def _other_install() -> CollaborationClient:
    """A second VISTA installation on the same server."""
    return CollaborationClient(URL, install_id=uuid.uuid4(), display_name="Bea")


def _skill_folder(root: Path, name: str, body: str = "Body.", **extra: str) -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: The {name} skill.\n---\n\n{body}\n",
        encoding="utf-8",
    )
    for rel, text in extra.items():
        (folder / rel).parent.mkdir(parents=True, exist_ok=True)
        (folder / rel).write_text(text, encoding="utf-8")
    return folder


async def _publish_elsewhere(tmp_path, name: str, body: str = "Theirs.") -> uuid.UUID:
    """Publish a skill from the other installation; returns its item id."""
    folder = _skill_folder(tmp_path / "theirs" / uuid.uuid4().hex, name, body)
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


async def _republish_elsewhere(tmp_path, item_id, parent: int, name: str, body: str):
    folder = _skill_folder(tmp_path / "theirs" / uuid.uuid4().hex, name, body)
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


async def _row(session, name: str) -> SkillTable | None:
    session.expire_all()
    return (
        await session.exec(select(SkillTable).where(SkillTable.name == name))
    ).first()


def _body(row: SkillTable) -> str:
    return read_skill(settings.data_dir / row.path).body


# ---------------------------------------------------------------------------
# The archive
# ---------------------------------------------------------------------------


def test_the_archive_round_trips_a_skill_folder(tmp_path):
    folder = _skill_folder(
        tmp_path,
        "demo",
        **{"scripts/run.py": "print(1)\n", "hpc_jobs/hello/README.md": "# hello\n"},
    )
    (folder / "__pycache__").mkdir()
    (folder / "__pycache__" / "x.pyc").write_bytes(b"junk")
    archive = tmp_path / "demo.tar.gz"
    assert pack_skill(folder, archive) == (
        3,
        sum(
            p.stat().st_size
            for p in folder.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts
        ),
    )

    skill = unpack_skill(archive, tmp_path / "out")
    assert skill.name == "demo"
    assert sorted(
        p.relative_to(tmp_path / "out").as_posix()
        for p in (tmp_path / "out").rglob("*")
        if p.is_file()
    ) == ["SKILL.md", "hpc_jobs/hello/README.md", "scripts/run.py"]
    with tarfile.open(archive) as tar:
        assert {m.uname for m in tar} == {""} and {m.mtime for m in tar} == {0}


def test_an_executable_script_stays_executable(tmp_path):
    folder = _skill_folder(tmp_path, "demo", **{"scripts/run.sh": "echo hi\n"})
    (folder / "scripts" / "run.sh").chmod(0o755)
    archive = tmp_path / "demo.tar.gz"
    pack_skill(folder, archive)
    unpack_skill(archive, tmp_path / "out")
    assert (tmp_path / "out" / "scripts" / "run.sh").stat().st_mode & 0o777 == 0o755
    assert (tmp_path / "out" / "SKILL.md").stat().st_mode & 0o111 == 0


def test_a_symlink_is_never_published(tmp_path):
    folder = _skill_folder(tmp_path, "demo")
    (folder / "secret").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(SkillImportError, match="symlink"):
        pack_skill(folder, tmp_path / "demo.tar.gz")


def _archive(tmp_path, members: list[tuple[tarfile.TarInfo, bytes | None]]) -> Path:
    path = tmp_path / "crafted.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        for info, data in members:
            if data is not None:
                info.size = len(data)
            tar.addfile(info, io.BytesIO(data) if data is not None else None)
    return path


SKILL_MD = b"---\nname: demo\ndescription: d\n---\n\nBody.\n"


def test_an_archive_with_a_link_is_refused(tmp_path):
    link = tarfile.TarInfo("escape")
    link.type = tarfile.SYMTYPE
    link.linkname = "/etc/passwd"
    archive = _archive(
        tmp_path, [(tarfile.TarInfo("SKILL.md"), SKILL_MD), (link, None)]
    )
    with pytest.raises(SkillImportError, match="link or a special file"):
        unpack_skill(archive, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_an_archive_path_outside_the_skill_is_refused(tmp_path):
    archive = _archive(
        tmp_path,
        [(tarfile.TarInfo("SKILL.md"), SKILL_MD), (tarfile.TarInfo("../evil"), b"x")],
    )
    with pytest.raises(SkillImportError, match="Invalid file path"):
        unpack_skill(archive, tmp_path / "out")
    assert not (tmp_path / "evil").exists()


def test_a_damaged_archive_is_refused(tmp_path):
    archive = tmp_path / "bad.tar.gz"
    archive.write_bytes(b"not a tar at all")
    with pytest.raises(SkillImportError, match="cannot be read|damaged"):
        unpack_skill(archive, tmp_path / "out")


def test_an_archive_that_is_not_the_version_published_is_refused(tmp_path):
    archive = tmp_path / "demo.tar.gz"
    pack_skill(_skill_folder(tmp_path, "demo"), archive)
    with pytest.raises(collaboration.CollaborationError, match="does not match"):
        _stage(archive, "0" * 64, tmp_path / "staged", "demo")


# ---------------------------------------------------------------------------
# Publishing
# ---------------------------------------------------------------------------


async def test_without_a_server_publishing_says_why(session):
    with api_client(session) as (client, _):
        assert (await client.post("/skills", json=SKILL)).status_code == 201
        res = await client.post("/skills/salt-density/publish", json={})
        listed = await client.get("/collaboration/skills")
    assert res.status_code == 503
    assert res.json() == {
        "detail": collaboration.NOT_CONFIGURED,
        "code": "collaboration_unavailable",
    }
    assert listed.status_code == 503


async def test_a_new_skill_is_published_as_version_1(collab_server, session):
    await _use_server(session)
    with api_client(session) as (client, _):
        created = await client.post("/skills", json=SKILL)
        assert created.json()["modified"] is True
        assert created.json()["remote_id"] is None
        res = await client.post("/skills/salt-density/publish", json={})
    assert res.status_code == 200, res.text
    body = res.json()
    assert (body["origin_server"], body["remote_version"], body["modified"]) == (
        URL,
        1,
        False,
    )
    async with _other_install() as other:
        item = await other.get_item(uuid.UUID(body["remote_id"]))
        version = await other.get_version(item.id, 1)
    assert (item.kind, item.name, item.created_by_name) == (
        "skill",
        "salt-density",
        "Ada",
    )
    assert version.manifest["description"] == SKILL["description"]
    assert version.manifest["format"] == 1


async def test_an_edit_marks_the_skill_modified_and_publishes_as_the_next_version(
    collab_server, session
):
    await _use_server(session)
    with api_client(session) as (client, _):
        await client.post("/skills", json=SKILL)
        await client.post("/skills/salt-density/publish", json={})
        patched = await client.patch(
            "/skills/salt-density", json={"body": "Use the new fit."}
        )
        assert patched.json()["modified"] is True
        res = await client.post("/skills/salt-density/publish", json={})
    assert res.status_code == 200, res.text
    assert (res.json()["remote_version"], res.json()["modified"]) == (2, False)


async def test_a_stale_publish_offers_the_three_choices(
    collab_server, session, tmp_path
):
    await _use_server(session)
    with api_client(session) as (client, _):
        await client.post("/skills", json=SKILL)
        first = (await client.post("/skills/salt-density/publish", json={})).json()
        item_id = uuid.UUID(first["remote_id"])
        await _republish_elsewhere(tmp_path, item_id, 1, "salt-density", "Theirs.")
        await client.patch("/skills/salt-density", json={"body": "Mine."})

        stale = await client.post("/skills/salt-density/publish", json={})
        assert stale.status_code == 409, stale.text
        assert stale.json()["code"] == "version_conflict"
        assert (stale.json()["head_version"], stale.json()["remote_version"]) == (2, 1)
        row = await _row(session, "salt-density")
        assert (row.remote_version, row.modified) == (1, True)

        # Publish on top of the head the researcher was shown.
        on_top = await client.post(
            "/skills/salt-density/publish", json={"on_top_of": 2}
        )
        assert on_top.status_code == 200, on_top.text
        assert on_top.json()["remote_version"] == 3

        # Or as a skill of its own.
        await client.patch("/skills/salt-density", json={"body": "Mine again."})
        as_new = await client.post(
            "/skills/salt-density/publish", json={"as_new": True}
        )
    assert as_new.status_code == 200, as_new.text
    assert as_new.json()["remote_id"] != first["remote_id"]
    assert as_new.json()["remote_version"] == 1


async def test_a_stale_publish_can_take_the_servers_version(
    collab_server, session, tmp_path
):
    await _use_server(session)
    with api_client(session) as (client, _):
        await client.post("/skills", json=SKILL)
        first = (await client.post("/skills/salt-density/publish", json={})).json()
        await _republish_elsewhere(
            tmp_path, uuid.UUID(first["remote_id"]), 1, "salt-density", "Theirs."
        )
        await client.patch("/skills/salt-density", json={"body": "Mine."})
        assert (
            await client.post("/skills/salt-density/publish", json={})
        ).status_code == 409

        url = f"/collaboration/skills/{first['remote_id']}/import"
        warned = await client.post(url, json={})
        assert warned.status_code == 409
        assert warned.json()["code"] == "modified"
        taken = await client.post(url, json={"replace_modified": True})
    assert taken.status_code == 200, taken.text
    assert taken.json()["status"] == "updated"
    row = await _row(session, "salt-density")
    assert (row.remote_version, row.modified) == (2, False)
    assert _body(row) == "Theirs."


async def test_a_skill_gone_from_the_server_can_be_published_as_new(
    collab_server, session
):
    await _use_server(session)
    with api_client(session) as (client, _):
        await client.post("/skills", json=SKILL)
        await client.post("/skills/salt-density/publish", json={})
        row = await _row(session, "salt-density")
        row.remote_id = uuid.uuid4()
        row.modified = True
        session.add(row)
        await session.commit()
        res = await client.post("/skills/salt-density/publish", json={})
    assert res.status_code == 409
    assert res.json()["code"] == "remote_missing"


async def test_publishing_to_another_server_starts_a_new_item(collab_server, session):
    """`remote_id` belongs to `origin_server`; elsewhere it names nothing."""
    await _use_server(session)
    with api_client(session) as (client, _):
        await client.post("/skills", json=SKILL)
        await client.post("/skills/salt-density/publish", json={})
        row = await _row(session, "salt-density")
        row.origin_server = "http://another.test:8002"
        session.add(row)
        await session.commit()
        res = await client.post("/skills/salt-density/publish", json={})
    assert res.status_code == 200, res.text
    assert res.json()["origin_server"] == URL
    assert res.json()["remote_version"] == 1


async def test_an_unchanged_skill_is_not_published_again(collab_server, session):
    await _use_server(session)
    with api_client(session) as (client, _):
        await client.post("/skills", json=SKILL)
        first = (await client.post("/skills/salt-density/publish", json={})).json()
        again = await client.post("/skills/salt-density/publish", json={})
    assert again.status_code == 200, again.text
    assert again.json()["remote_version"] == 1
    async with _other_install() as other:
        assert (await other.get_item(uuid.UUID(first["remote_id"]))).head_version == 1


async def test_an_edit_made_while_publishing_stays_modified(
    collab_server, session, engine, monkeypatch
):
    """The edit is not in what was uploaded, so it must not read as published."""
    from sqlmodel.ext.asyncio.session import AsyncSession

    await _use_server(session)
    upload = CollaborationClient.upload_blob

    async def upload_then_edit(self, path):
        async with AsyncSession(engine) as other:
            row = (
                await other.exec(
                    select(SkillTable).where(SkillTable.name == "salt-density")
                )
            ).one()
            row.modified = True
            row.updated_at = "edited meanwhile"
            other.add(row)
            await other.commit()
        return await upload(self, path)

    with api_client(session) as (client, _):
        await client.post("/skills", json=SKILL)
        monkeypatch.setattr(CollaborationClient, "upload_blob", upload_then_edit)
        res = await client.post("/skills/salt-density/publish", json={})
    assert res.status_code == 200, res.text
    assert (res.json()["remote_version"], res.json()["modified"]) == (1, True)


async def test_a_bundled_skill_is_neither_published_nor_deleted(collab_server, session):
    """Some bundled skills' data is fetched only by the first seeding, so a
    deleted one would never come back."""
    await _use_server(session)
    with api_client(session) as (client, _):
        await client.post("/skills", json=SKILL)
        row = await _row(session, "salt-density")
        row.bundled = True
        session.add(row)
        await session.commit()
        res = await client.post("/skills/salt-density/publish", json={})
        deleted = await client.delete("/skills/salt-density")
    assert res.status_code == 409
    assert "bundled" in res.json()["detail"]
    assert deleted.status_code == 409
    assert await _row(session, "salt-density") is not None


async def test_only_a_taken_name_is_a_name_clash(engine):
    """Any other failed insert (an old database's NOT NULL column) is not."""
    from sqlalchemy.exc import IntegrityError
    from sqlmodel.ext.asyncio.session import AsyncSession

    from vista_backend.services.skills import is_name_clash

    async with AsyncSession(engine) as s:
        s.add(SkillTable(name="a", description="d", path="p"))
        await s.commit()
        s.add(SkillTable(name="a", description="d", path="q"))
        with pytest.raises(IntegrityError) as clash:
            await s.commit()
        await s.rollback()
        s.add(SkillTable(name="b", description=None, path="p"))  # type: ignore[arg-type]
        with pytest.raises(IntegrityError) as other:
            await s.commit()
    assert is_name_clash(clash.value)
    assert not is_name_clash(other.value)


# ---------------------------------------------------------------------------
# The Skill Hub's listing
# ---------------------------------------------------------------------------


async def test_the_listing_says_what_each_skill_is_and_what_is_here(
    collab_server, session, tmp_path
):
    await _use_server(session)
    theirs = await _publish_elsewhere(tmp_path, "vllm-lux")
    clash = await _publish_elsewhere(tmp_path, "salt-density")
    with api_client(session) as (client, _):
        await client.post("/skills", json=SKILL)
        mine = (await client.post("/skills/salt-density/publish", json={})).json()
        listed = await client.get("/collaboration/skills")
    assert listed.status_code == 200, listed.text
    by_id = {s["id"]: s for s in listed.json()}
    assert by_id[str(theirs)]["manifest"]["description"] == "The vllm-lux skill."
    assert by_id[str(theirs)]["published_by_name"] == "Bea"
    assert (by_id[str(theirs)]["local"], by_id[str(theirs)]["name_taken"]) == (
        None,
        False,
    )
    assert by_id[mine["remote_id"]]["local"] == {
        "name": "salt-density",
        "remote_version": 1,
        "modified": False,
    }
    # Same name, another skill: importing it needs a new name.
    assert by_id[str(clash)]["name_taken"] is True


# ---------------------------------------------------------------------------
# Importing
# ---------------------------------------------------------------------------


async def test_importing_a_skill_loads_it_into_the_project(
    collab_server, session, tmp_path
):
    await _use_server(session)
    project = await seed_project(session)
    item_id = await _publish_elsewhere(tmp_path, "vllm-lux")
    with api_client(session) as (client, _):
        res = await client.post(
            f"/collaboration/skills/{item_id}/import", json={"project": project.name}
        )
        again = await client.post(f"/collaboration/skills/{item_id}/import", json={})
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "imported"
    assert again.json()["status"] == "unchanged"
    row = await _row(session, "vllm-lux")
    assert (row.origin_server, row.remote_id, row.remote_version, row.modified) == (
        URL,
        item_id,
        1,
        False,
    )
    assert _body(row) == "Theirs."
    loaded = await session.get(ProjectTable, project.id)
    assert loaded.skills == ["vllm-lux"]


async def test_a_new_import_is_named_after_the_item(collab_server, session, tmp_path):
    """
    A copy imported under another name publishes its versions under that name;
    the hub shows the item's name, and the next importer gets that name too.
    """
    await _use_server(session)
    item_id = await _publish_elsewhere(tmp_path, "salt-density")
    with api_client(session) as (client, _):
        await client.post("/skills", json=SKILL)
        url = f"/collaboration/skills/{item_id}/import"
        await client.post(url, json={"rename_to": "salt-density-bea"})
        await client.patch("/skills/salt-density-bea", json={"body": "Changed."})
        published = await client.post("/skills/salt-density-bea/publish", json={})
        assert published.json()["remote_version"] == 2
        # Another installation, with neither skill: the hub and the import agree.
        await client.delete("/skills/salt-density-bea")
        await client.delete("/skills/salt-density")
        [listed] = (await client.get("/collaboration/skills")).json()
        imported = await client.post(url, json={})
    assert (listed["name"], listed["name_taken"]) == ("salt-density", False)
    assert listed["manifest"]["name"] == "salt-density-bea"
    assert imported.json()["skill"]["name"] == "salt-density"
    assert _body(await _row(session, "salt-density")) == "Changed."


async def test_a_download_larger_than_any_skill_is_refused(
    collab_server, session, tmp_path, monkeypatch
):
    from vista_backend.services import skill_collaboration

    await _use_server(session)
    item_id = await _publish_elsewhere(tmp_path, "vllm-lux", "x" * 50_000)
    monkeypatch.setattr(skill_collaboration, "MAX_ARCHIVE_BYTES", 100)
    with api_client(session) as (client, _):
        res = await client.post(f"/collaboration/skills/{item_id}/import", json={})
    assert res.status_code == 502
    assert "more than" in res.json()["detail"]
    assert await _row(session, "vllm-lux") is None


async def test_the_listing_without_manifests_is_one_request(
    collab_server, session, tmp_path, monkeypatch
):
    await _use_server(session)
    item_id = await _publish_elsewhere(tmp_path, "vllm-lux")
    asked = []
    get_version = CollaborationClient.get_version

    async def counting(self, *args):
        asked.append(args)
        return await get_version(self, *args)

    monkeypatch.setattr(CollaborationClient, "get_version", counting)
    with api_client(session) as (client, _):
        [listed] = (await client.get("/collaboration/skills?manifests=false")).json()
    assert (listed["id"], listed["head_version"], listed["manifest"]) == (
        str(item_id),
        1,
        None,
    )
    assert asked == []


async def test_a_head_that_cannot_be_read_leaves_the_rest_listed(
    collab_server, session, tmp_path, monkeypatch
):
    await _use_server(session)
    broken = await _publish_elsewhere(tmp_path, "broken")
    fine = await _publish_elsewhere(tmp_path, "fine")
    get_version = CollaborationClient.get_version

    async def failing(self, item_id, version):
        if item_id == broken:
            raise collaboration.CollaborationRefused("refused", 500)
        return await get_version(self, item_id, version)

    monkeypatch.setattr(CollaborationClient, "get_version", failing)
    with api_client(session) as (client, _):
        res = await client.get("/collaboration/skills")
    assert res.status_code == 200, res.text
    by_id = {s["id"]: s for s in res.json()}
    assert by_id[str(broken)]["manifest"] is None
    assert by_id[str(fine)]["manifest"]["name"] == "fine"


async def test_a_manifest_link_must_be_http(tmp_path):
    assert (
        SkillManifest(
            name="x", description="d", repo_url="javascript:alert(1)"
        ).repo_url
        is None
    )
    assert SkillManifest(
        name="x", description="d", repo_url="https://github.com/a/b"
    ).repo_url


async def test_a_taken_name_asks_for_a_new_one(collab_server, session, tmp_path):
    await _use_server(session)
    item_id = await _publish_elsewhere(tmp_path, "salt-density")
    with api_client(session) as (client, _):
        await client.post("/skills", json=SKILL)
        url = f"/collaboration/skills/{item_id}/import"
        taken = await client.post(url, json={})
        renamed = await client.post(url, json={"rename_to": "salt-density-bea"})
    assert taken.status_code == 409
    assert (taken.json()["code"], taken.json()["name"]) == (
        "name_taken",
        "salt-density",
    )
    assert renamed.status_code == 200, renamed.text
    row = await _row(session, "salt-density-bea")
    assert read_skill(settings.data_dir / row.path).name == "salt-density-bea"
    assert row.remote_id == item_id
    mine = await _row(session, "salt-density")
    assert mine.remote_id is None and _body(mine) == SKILL["body"]


async def test_a_newer_version_updates_the_copy_in_place(
    collab_server, session, tmp_path
):
    await _use_server(session)
    project = await seed_project(session, skills=[])
    item_id = await _publish_elsewhere(tmp_path, "vllm-lux", "Version one.")
    with api_client(session) as (client, _):
        url = f"/collaboration/skills/{item_id}/import"
        await client.post(url, json={"project": project.name})
        before = await _row(session, "vllm-lux")
        old_folder = settings.data_dir / before.path
        await _republish_elsewhere(tmp_path, item_id, 1, "vllm-lux", "Version two.")

        listed = {
            s["id"]: s for s in (await client.get("/collaboration/skills")).json()
        }
        entry = listed[str(item_id)]
        assert (entry["head_version"], entry["local"]["remote_version"]) == (2, 1)

        res = await client.post(url, json={})
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "updated"
    after = await _row(session, "vllm-lux")
    assert after.id == before.id
    assert (after.remote_version, after.description) == (2, "The vllm-lux skill.")
    assert _body(after) == "Version two."
    assert not old_folder.exists()


async def test_an_older_version_can_be_imported(collab_server, session, tmp_path):
    await _use_server(session)
    item_id = await _publish_elsewhere(tmp_path, "vllm-lux", "Version one.")
    await _republish_elsewhere(tmp_path, item_id, 1, "vllm-lux", "Version two.")
    with api_client(session) as (client, _):
        res = await client.post(
            f"/collaboration/skills/{item_id}/import", json={"version": 1}
        )
    assert res.status_code == 200, res.text
    row = await _row(session, "vllm-lux")
    assert (row.remote_version, _body(row)) == (1, "Version one.")


async def test_an_item_that_is_not_a_skill_is_not_imported(collab_server, session):
    await _use_server(session)
    async with _other_install() as other:
        item = await other.create_item("project", "p", manifest={})
    with api_client(session) as (client, _):
        res = await client.post(f"/collaboration/skills/{item.id}/import", json={})
    assert res.status_code == 400
    assert "not a skill" in res.json()["detail"]
