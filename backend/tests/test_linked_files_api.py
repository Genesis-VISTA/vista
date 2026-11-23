"""
The `linked` file kind (openspec change linked-folders, group 5): serving a linked folder's
files, confined to it by their real location, listing it one directory at a time, and its
displays' records.
"""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness import FakeAgentPool, api_client, seed_project
from vista_backend.config import settings
from vista_backend.db.schemas import ChatSessionCreate, DisplayedFileTable
from vista_backend.services import chat_session as chat_session_service
from vista_backend.services import displayed_files as displayed_files_service
from vista_backend.services import files as files_service
from vista_backend.services import linked_folders
from vista_backend.services.displayed_files import sandbox_path_to_file

pytestmark = pytest.mark.anyio


@pytest.fixture
def tree(tmp_path, monkeypatch) -> Path:
    """
    salt-data/
      README.md, figs/phase.png, runs/a.csv, runs/2026-11/b.csv
      current -> runs/2026-11      (inward, relative)
      latest  -> <outside>/run42   (outward)
      escape.txt -> <outside>/secret.txt
    """
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    root = tmp_path / "Research" / "salt-data"
    (root / "figs").mkdir(parents=True)
    (root / "runs" / "2026-11").mkdir(parents=True)
    (root / "README.md").write_text("# Salt data\n", encoding="utf-8")
    (root / "figs" / "phase.png").write_bytes(b"\x89PNG fake")
    (root / "runs" / "a.csv").write_text("a\n", encoding="utf-8")
    (root / "runs" / "2026-11" / "b.csv").write_text("b\n", encoding="utf-8")
    outside = tmp_path / "Archive"
    (outside / "run42").mkdir(parents=True)
    (outside / "run42" / "out.csv").write_text("out\n", encoding="utf-8")
    (outside / "secret.txt").write_text("secret\n", encoding="utf-8")
    (root / "current").symlink_to(Path("runs") / "2026-11")
    (root / "latest").symlink_to(outside / "run42")
    (root / "escape.txt").symlink_to(outside / "secret.txt")
    # Newest first is by modification time: README newest, then runs, then figs.
    for i, name in enumerate(["figs", "runs", "README.md"]):
        os.utime(root / name, (1_700_000_000 + i, 1_700_000_000 + i))
    return root.resolve()


@pytest.fixture
async def project(session, tree):
    project = await seed_project(session, name="salts")
    await linked_folders.add_link(session, project.id, str(tree))
    await session.commit()
    return project


def _url(path: str) -> str:
    return f"/projects/salts/linked/{path}"


async def _get(session, path: str, **params):
    with api_client(session) as (client, _):
        return await client.get(_url(path), params=params)


def _safe(response) -> bool:
    return (
        response.headers["x-content-type-options"] == "nosniff"
        and response.headers["content-security-policy"] == "sandbox; default-src 'none'"
    )


# ---------------------------------------------------------------------------
# 5.1 Serving
# ---------------------------------------------------------------------------


async def test_a_preview_is_inline(session, project):
    res = await _get(session, "salt-data/figs/phase.png", preview=1)
    assert res.status_code == 200, res.text
    assert res.content == b"\x89PNG fake"
    assert res.headers["content-disposition"].startswith("inline")
    assert _safe(res)


async def test_a_download_is_an_attachment(session, project):
    res = await _get(session, "salt-data/README.md")
    assert res.status_code == 200
    assert res.text == "# Salt data\n"
    assert res.headers["content-disposition"].startswith("attachment")
    assert _safe(res)


async def test_traversal_is_refused(session, project):
    for path in ("salt-data/..%2F..%2FArchive%2Fsecret.txt", "salt-data/%2E%2E/x"):
        assert (await _get(session, path)).status_code == 404, path
    with pytest.raises(Exception) as e:
        await files_service.get_file_path(
            session, "salts", "linked", "salt-data/../../Archive/secret.txt"
        )
    assert getattr(e.value, "status_code", None) == 404


async def test_a_symlink_out_is_refused(session, project):
    assert (await _get(session, "salt-data/latest/out.csv")).status_code == 404
    assert (await _get(session, "salt-data/escape.txt")).status_code == 404


async def test_a_symlink_inside_is_followed(session, project):
    res = await _get(session, "salt-data/current/b.csv")
    assert (res.status_code, res.text) == (200, "b\n")


async def test_an_unknown_or_unavailable_link_is_not_found(session, project, tree):
    assert (await _get(session, "nope/README.md")).status_code == 404
    assert (await _get(session, "salt-data")).status_code == 404
    tree.rename(tree.parent / "moved")
    res = await _get(session, "salt-data/README.md")
    assert (res.status_code, res.json()["code"]) == (404, "unavailable")


async def test_linked_files_cannot_be_deleted_or_walked(session, project, tree):
    with api_client(session) as (client, _):
        deleted = await client.delete(_url("salt-data/README.md"))
        walked = await client.get("/projects/salts/linked")
        uploaded = await client.post(
            "/projects/salts/linked", files={"files": ("x.txt", b"x")}
        )
    assert deleted.status_code == 405
    assert (tree / "README.md").exists()
    assert walked.status_code == 404
    assert uploaded.status_code in (404, 405)


# ---------------------------------------------------------------------------
# 5.2 One directory at a time
# ---------------------------------------------------------------------------


async def _entries(session, directory: str = ""):
    with api_client(session) as (client, _):
        res = await client.get(
            "/projects/salts/linked-folders/salt-data/entries",
            params={"dir": directory},
        )
    return res


async def test_the_listing_is_one_level_newest_first_without_symlinks_out(
    session, project
):
    res = await _entries(session)
    assert res.status_code == 200, res.text
    entries = res.json()
    names = [e["name"] for e in entries]
    assert set(names) == {"README.md", "runs", "figs", "current"}
    assert names.index("README.md") < names.index("runs") < names.index("figs")
    by_name = {e["name"]: e for e in entries}
    assert by_name["runs"]["type"] == "folder" and by_name["runs"]["size"] is None
    assert by_name["README.md"] == {
        **by_name["README.md"],
        "type": "file",
        "path": "README.md",
        "size": 12,
    }
    assert by_name["current"]["type"] == "folder"

    runs = {e["name"]: e for e in (await _entries(session, "runs")).json()}
    assert set(runs) == {"a.csv", "2026-11"}
    assert runs["a.csv"]["path"] == "runs/a.csv"
    current = (await _entries(session, "current")).json()
    assert [e["path"] for e in current] == ["current/b.csv"]


async def test_the_listing_stays_inside(session, project):
    for directory in ("..", "../..", "latest", "README.md", "missing"):
        assert (await _entries(session, directory)).status_code == 404, directory


# ---------------------------------------------------------------------------
# 5.3 Displays
# ---------------------------------------------------------------------------


def test_a_linked_sandbox_path_maps_to_a_linked_file():
    assert sandbox_path_to_file("/mnt/linked/salt-data/figs/phase.png") == (
        "linked",
        "salt-data/figs/phase.png",
    )
    assert sandbox_path_to_file("file:///mnt/linked/salt-data/a%20b.csv") == (
        "linked",
        "salt-data/a b.csv",
    )
    for uri in ("/mnt/linked/salt-data", "/mnt/linked/salt-data/", "/mnt/linked/"):
        assert sandbox_path_to_file(uri) is None, uri


async def _record(session, project, path: str, file: Path) -> str:
    chat = await chat_session_service.create_chat_session(
        session, project_id=project.id, payload=ChatSessionCreate(title="Phases")
    )
    chat_id = chat.id
    stat = file.stat()
    session.add(
        DisplayedFileTable(
            project_id=project.id,
            chat_session_id=chat_id,
            run_id="run-1",
            tool_call_id="call-1",
            kind="linked",
            path=path,
            shown_at="2026-11-23T10:00:00",
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
        )
    )
    await session.commit()
    return str(chat_id)


async def test_shown_in_finds_a_displayed_linked_file(session, project, tree):
    chat_id = await _record(
        session, project, "salt-data/figs/phase.png", tree / "figs" / "phase.png"
    )
    [entry] = (await _entries(session, "figs")).json()
    assert entry["shown_in"]["chat_session_id"] == chat_id

    (tree / "figs" / "phase.png").write_bytes(b"changed, and longer")
    [entry] = (await _entries(session, "figs")).json()
    assert entry["shown_in"] is None


async def test_a_chats_linked_display_has_a_status(session, project, tree, monkeypatch):
    agent = SimpleNamespace(uploads_dir=tree / "u", output_dir=tree / "o")
    monkeypatch.setattr(
        displayed_files_service, "project_agent_pool", FakeAgentPool(agent=agent)
    )
    chat_id = await _record(
        session, project, "salt-data/figs/phase.png", tree / "figs" / "phase.png"
    )

    async def status():
        with api_client(session) as (client, _):
            res = await client.get(
                f"/projects/salts/chat-sessions/{chat_id}/displayed-files"
            )
        [row] = res.json()
        return (row["kind"], row["path"], row["status"])

    assert await status() == ("linked", "salt-data/figs/phase.png", "current")
    await linked_folders.rename_link(session, project.id, "salt-data", "renamed")
    await session.commit()
    assert (await status())[2] == "missing", "a renamed link finds no file"
