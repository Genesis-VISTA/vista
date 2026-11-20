"""
Storing and editing a project's linked folders (openspec change linked-folders, group 3):
the table, the name rules, the guards on what can be linked, the endpoints, and that links
never leave this computer.
"""

import asyncio
import uuid
from pathlib import Path

import pytest
from sqlmodel import select

from harness import api_client, seed_project
from vista_backend.config import settings
from vista_backend.db.schemas import ProjectLinkedFolderTable
from vista_backend.services import linked_folders
from vista_backend.services import project as project_service
from vista_backend.services import project_agent as project_agent_service
from vista_backend.services.linked_folders import (
    LinkRefused,
    check_linkable,
    check_name,
    default_name,
)

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    """VISTA's data in its own folder, and a home folder that does not hold it."""
    monkeypatch.setattr(settings, "data_dir", (tmp_path / "vista-data").resolve())
    settings.data_dir.mkdir()
    home = (tmp_path / "home").resolve()
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


def _folder(root: Path, *parts: str) -> Path:
    path = root.joinpath(*parts)
    path.mkdir(parents=True)
    return path.resolve()


class _Link:
    def __init__(self, name: str, host_path: Path):
        self.name, self.host_path = name, str(host_path)


# ---------------------------------------------------------------------------
# 3.2 Names
# ---------------------------------------------------------------------------


def test_default_name_is_made_safe():
    assert default_name("Salt Data (2026)", []) == "Salt-Data-2026"
    assert default_name("msr-runs", []) == "msr-runs"
    assert default_name(".hidden.", []) == "hidden"
    assert default_name("ü…", []) == "folder"
    assert default_name("", []) == "folder"


def test_default_name_on_collision():
    assert default_name("data", ["data"]) == "data-2"
    assert default_name("data", ["data", "data-2"]) == "data-3"
    assert default_name("data", ["Data"]) == "data-2", "one folder on macOS and Windows"


@pytest.mark.parametrize("name", ["../x", "a/b", ".", "..", "", "a b", "a\\b", "ü"])
def test_invalid_names_are_refused(name):
    with pytest.raises(LinkRefused) as e:
        check_name(name, [])
    assert (e.value.status, e.value.code) == (400, "invalid_name")


def test_a_name_another_link_has_is_refused():
    with pytest.raises(LinkRefused) as e:
        check_name("data", ["data"])
    assert (e.value.status, e.value.code) == (409, "name_taken")
    with pytest.raises(LinkRefused):
        check_name("DATA", ["data"])


def test_a_good_name_passes():
    check_name("msr-2025", ["data"])
    check_name(".hidden_1", [])


# ---------------------------------------------------------------------------
# 3.3 What can be linked
# ---------------------------------------------------------------------------


def _refused(path, existing=()) -> LinkRefused:
    with pytest.raises(LinkRefused) as e:
        check_linkable(str(path), list(existing))
    return e.value


def test_a_folder_is_linked_resolved(tmp_path):
    real = _folder(tmp_path, "real", "salt-data")
    (tmp_path / "via").symlink_to(tmp_path / "real")
    assert check_linkable(str(tmp_path / "via" / "salt-data"), []) == real


def test_not_a_folder(tmp_path):
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    for path in (tmp_path / "missing", tmp_path / "a.txt", "relative/path"):
        assert _refused(path).code == "not_a_folder"


def test_already_linked(tmp_path):
    real = _folder(tmp_path, "salt-data")
    (tmp_path / "alias").symlink_to(real)
    existing = [_Link("salt", real)]
    for path in (real, tmp_path / "alias"):
        e = _refused(path, existing)
        assert (e.status, e.code, e.extra) == (409, "already_linked", {"link": "salt"})


def test_vistas_data_folder_inside_it_or_holding_it(tmp_path):
    volume = _folder(settings.data_dir, "volumes", "p1")
    for path in (settings.data_dir, volume, tmp_path):
        e = _refused(path)
        assert (e.status, e.code) == (400, "vista_data"), path


def test_the_home_folder_is_refused(_data_dir):
    e = _refused(_data_dir)
    assert (e.status, e.code) == (400, "broad_folder")
    assert "home folder" in e.detail and "folder inside it" in e.detail


def test_a_home_folder_holding_vistas_data_says_it_is_home(monkeypatch):
    """Packaged installs keep VISTA's data in `~/.vista`: the reason is still "home"."""
    home = Path.home()
    monkeypatch.setattr(settings, "data_dir", _folder(home, ".vista"))
    assert _refused(home).code == "broad_folder"


def test_a_filesystem_root_is_refused():
    e = _refused(Path(Path.cwd().anchor))
    assert (e.code, "a whole drive" in e.detail) == ("broad_folder", True)


def test_a_folder_inside_home_or_a_mounted_volume_is_not_broad(_data_dir, tmp_path):
    assert check_linkable(str(_folder(_data_dir, "Research")), []).name == "Research"
    assert not linked_folders.is_root(Path("/Volumes/External"))


# ---------------------------------------------------------------------------
# 3.1 The table
# ---------------------------------------------------------------------------


async def test_deleting_a_project_deletes_its_links(session, tmp_path):
    project = await seed_project(session, name="salt")
    await linked_folders.add_link(session, project.id, str(_folder(tmp_path, "d")))
    await session.commit()

    await project_service.delete_project(session, "salt")
    await session.commit()

    rows = (await session.exec(select(ProjectLinkedFolderTable))).all()
    assert rows == []


async def test_the_pooled_agent_is_built_with_the_projects_links(
    session, engine, tmp_path, monkeypatch
):
    project = await seed_project(session, name="salt")
    real = _folder(tmp_path, "salt-data")
    await linked_folders.add_link(session, project.id, str(real))
    await linked_folders.add_link(session, project.id, str(_folder(tmp_path, "gone")))
    await session.commit()
    (tmp_path / "gone").rmdir()

    monkeypatch.setattr(project_agent_service, "get_engine", lambda: engine)
    built = []

    class Agent:
        def __init__(self, *args, linked_folders=()):
            self.links = [(link.name, link.host_path) for link in linked_folders]
            built.append(self)

        async def __aenter__(self):
            return self

    monkeypatch.setattr(project_agent_service, "ProjectAgent", Agent)
    await project_agent_service._build_project_agent(None, project.id)  # noqa: SLF001
    assert built[0].links == [
        ("salt-data", str(real)),
        ("gone", str((tmp_path / "gone").resolve())),
    ]


# ---------------------------------------------------------------------------
# 3.4 Endpoints
# ---------------------------------------------------------------------------


async def test_link_list_rename_unlink(session, tmp_path):
    await seed_project(session, name="salt")
    folder = _folder(tmp_path, "Salt Data (2026)")
    gone = _folder(tmp_path, "msr-runs")
    url = "/projects/salt/linked-folders"
    with api_client(session) as (client, _):
        made = await client.post(url, json={"path": str(folder)})
        assert made.status_code == 201, made.text
        assert made.json() == {
            "name": "Salt-Data-2026",
            "host_path": str(folder),
            "sandbox_path": "/mnt/linked/Salt-Data-2026",
            "available": True,
            "reason": None,
        }
        named = await client.post(url, json={"path": str(gone), "name": "runs"})
        assert named.json()["name"] == "runs"
        gone.rmdir()

        listed = (await client.get(url)).json()
        assert [(r["name"], r["available"], r["reason"]) for r in listed] == [
            ("Salt-Data-2026", True, None),
            ("runs", False, "missing"),
        ]

        renamed = await client.patch(f"{url}/runs", json={"name": "msr-2025"})
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["sandbox_path"] == "/mnt/linked/msr-2025"
        refused = await client.patch(f"{url}/msr-2025", json={"name": "../x"})
        assert (refused.status_code, refused.json()["code"]) == (400, "invalid_name")
        taken = await client.patch(f"{url}/msr-2025", json={"name": "Salt-Data-2026"})
        assert (taken.status_code, taken.json()["code"]) == (409, "name_taken")

        gone_link = await client.delete(f"{url}/msr-2025")
        assert gone_link.status_code == 204
        missing = await client.delete(f"{url}/msr-2025")
        assert missing.status_code == 404
        listed = (await client.get(url)).json()
    assert [r["name"] for r in listed] == ["Salt-Data-2026"]


async def test_refusals_name_the_reason(session, tmp_path, _data_dir):
    await seed_project(session, name="salt")
    folder = _folder(tmp_path, "salt-data")
    url = "/projects/salt/linked-folders"
    with api_client(session) as (client, _):
        await client.post(url, json={"path": str(folder)})
        again = await client.post(url, json={"path": str(folder)})
        assert (again.status_code, again.json()["code"], again.json()["link"]) == (
            409,
            "already_linked",
            "salt-data",
        )
        nope = await client.post(url, json={"path": str(tmp_path / "nope")})
        assert (nope.status_code, nope.json()["code"]) == (400, "not_a_folder")
        data = await client.post(url, json={"path": str(settings.data_dir)})
        assert (data.status_code, data.json()["code"]) == (400, "vista_data")

        home = await client.post(url, json={"path": str(_data_dir)})
        assert (home.status_code, home.json()["code"]) == (400, "broad_folder")
        ignored = await client.post(
            url, json={"path": str(_data_dir), "confirm_broad": True}
        )
        assert ignored.status_code == 400, "there is no way past it"
        unknown = await client.get("/projects/nope/linked-folders")
    assert unknown.status_code == 404


async def test_a_change_evicts_the_projects_agents_but_not_a_running_one(
    session, tmp_path, monkeypatch
):
    """Linking applies from each conversation's next turn; a running turn keeps its agent."""
    project = await seed_project(session, name="salt")
    other = await seed_project(session, name="other")
    running_key = (uuid.uuid4(), project.id)
    idle_key = (uuid.uuid4(), project.id)
    other_key = (uuid.uuid4(), other.id)

    cleaned: list[object] = []

    async def factory(key):
        return ("agent", key)

    async def cleanup(agent):
        cleaned.append(agent)

    pool = project_agent_service.project_agent_pool
    monkeypatch.setattr(pool, "_factory", factory)
    monkeypatch.setattr(pool, "_cleanup", cleanup)
    url = "/projects/salt/linked-folders"
    try:
        for key in (idle_key, other_key):
            async with pool.get(key):
                pass
        with api_client(session) as (client, _):
            async with pool.get(running_key) as running:
                res = await client.post(url, json={"path": str(_folder(tmp_path, "d"))})
                assert res.status_code == 201, res.text
                await session.commit()
                await asyncio.sleep(0)
                assert running_key not in pool.keys()
                assert idle_key not in pool.keys()
                assert other_key in pool.keys()
                assert running not in cleaned, "the running turn's agent was torn down"
            await pool.flush()
            assert running in cleaned and ("agent", idle_key) in cleaned

            for key in (idle_key,):
                async with pool.get(key):
                    pass
            await client.patch(f"{url}/d", json={"name": "e"})
            await session.commit()
            await asyncio.sleep(0)
            assert idle_key not in pool.keys()
            async with pool.get(idle_key):
                pass
            await client.delete(f"{url}/e")
            await session.commit()
            await asyncio.sleep(0)
            assert idle_key not in pool.keys()
    finally:
        for key in (running_key, idle_key, other_key):
            pool.delete(key)
        await pool.flush()
