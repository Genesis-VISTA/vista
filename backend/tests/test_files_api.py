"""
HTTP contract for serving project files safely (project-files).

A file is shown inline only for allowlisted raster image types and PDF, by
extension; everything else is an attachment. Every response forbids sniffing and
carries a sandboxing CSP. Runs in-process against the real app with the agent
pool faked onto temporary directories.
"""

import os
from types import SimpleNamespace

import pytest
from sqlmodel import select
from harness import FakeAgentPool, api_client, seed_project

from vista_backend.db.schemas import ChatSessionCreate, DisplayedFileTable
from vista_backend.services import chat_session as chat_session_service
from vista_backend.services import files as files_service

pytestmark = [pytest.mark.anyio, pytest.mark.unit]

URL = "/projects/{name}/{kind}/{file}"


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    uploads_dir = tmp_path / "uploads"
    output_dir = tmp_path / "output"
    uploads_dir.mkdir()
    output_dir.mkdir()
    agent = SimpleNamespace(uploads_dir=uploads_dir, output_dir=output_dir)
    monkeypatch.setattr(files_service, "project_agent_pool", FakeAgentPool(agent=agent))
    return uploads_dir, output_dir


@pytest.fixture
async def project(session):
    return await seed_project(session, name="salts")


async def _get(session, project, kind, file, **params):
    with api_client(session) as (client, _):
        return await client.get(
            URL.format(name=project.name, kind=kind, file=file), params=params
        )


def _assert_safe(response):
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-security-policy"] == "sandbox; default-src 'none'"


@pytest.mark.parametrize(
    "file", ["plot.png", "plot.JPG", "a.jpeg", "a.gif", "a.webp", "doc.pdf"]
)
async def test_allowlisted_preview_is_inline(session, dirs, project, file):
    (dirs[1] / file).write_bytes(b"data")

    response = await _get(session, project, "outputs", file, preview=1)

    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith("inline")
    _assert_safe(response)


async def test_allowlisted_type_without_preview_is_attachment(session, dirs, project):
    (dirs[1] / "plot.png").write_bytes(b"data")

    response = await _get(session, project, "outputs", "plot.png")

    assert response.headers["content-disposition"].startswith("attachment")
    _assert_safe(response)


@pytest.mark.parametrize(
    "file", ["page.html", "page.HTML", "logo.svg", "notes.txt", "noext"]
)
@pytest.mark.parametrize("params", [{}, {"preview": 1}])
async def test_other_types_are_attachments(session, dirs, project, file, params):
    (dirs[0] / file).write_text("<script>alert(1)</script>", encoding="utf-8")

    response = await _get(session, project, "uploads", file, **params)

    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith("attachment")
    _assert_safe(response)


async def test_path_traversal_is_still_not_found(session, dirs, project, tmp_path):
    (tmp_path / "secret.png").write_bytes(b"secret")

    response = await _get(session, project, "uploads", "..%2Fsecret.png", preview=1)

    assert response.status_code == 404


async def _display(session, project, file, *, title, shown_at, kind="outputs"):
    """Record `file` as displayed in a new chat, as the run driver will, stat taken now."""
    chat = await chat_session_service.create_chat_session(
        session, project_id=project.id, payload=ChatSessionCreate(title=title)
    )
    chat_id = chat.id
    stat = file.stat()
    session.add(
        DisplayedFileTable(
            project_id=project.id,
            chat_session_id=chat_id,
            run_id=f"run-{title}",
            tool_call_id=f"call-{title}",
            kind=kind,
            path=file.name,
            shown_at=shown_at,
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
        )
    )
    await session.commit()
    return chat_id


async def _listing(session, project, kind="outputs"):
    with api_client(session) as (client, _):
        response = await client.get(f"/projects/{project.name}/{kind}")
    assert response.status_code == 200
    return {item["name"]: item for item in response.json()}


async def test_a_displayed_unchanged_file_says_where_it_was_shown(
    session, dirs, project
):
    plot = dirs[1] / "plot.png"
    plot.write_bytes(b"data")
    chat_id = await _display(
        session, project, plot, title="Phase diagrams", shown_at="2026-01-01T10:00:00"
    )

    listing = await _listing(session, project)

    assert listing["plot.png"]["shown_in"] == {
        "chat_session_id": str(chat_id),
        "chat_title": "Phase diagrams",
        "run_id": "run-Phase diagrams",
        "tool_call_id": "call-Phase diagrams",
    }


async def test_an_overwritten_file_is_not_shown_in_anywhere(session, dirs, project):
    plot = dirs[1] / "plot.png"
    plot.write_bytes(b"data")
    await _display(session, project, plot, title="Old", shown_at="2026-01-01T10:00:00")
    plot.write_bytes(b"different length")

    assert (await _listing(session, project))["plot.png"]["shown_in"] is None


async def test_a_same_size_rewrite_at_another_time_is_not_shown_in(
    session, dirs, project
):
    plot = dirs[1] / "plot.png"
    plot.write_bytes(b"data")
    await _display(session, project, plot, title="Old", shown_at="2026-01-01T10:00:00")
    mtime_ns = plot.stat().st_mtime_ns
    os.utime(plot, ns=(mtime_ns + 1_000_000, mtime_ns + 1_000_000))

    assert (await _listing(session, project))["plot.png"]["shown_in"] is None


async def test_a_file_displayed_in_two_chats_names_the_newer(session, dirs, project):
    plot = dirs[1] / "plot.png"
    plot.write_bytes(b"data")
    await _display(
        session, project, plot, title="Older", shown_at="2026-01-01T10:00:00"
    )
    newer = await _display(
        session, project, plot, title="Newer", shown_at="2026-01-02T10:00:00"
    )

    shown_in = (await _listing(session, project))["plot.png"]["shown_in"]

    assert shown_in["chat_session_id"] == str(newer)
    assert shown_in["chat_title"] == "Newer"


async def test_only_the_newest_record_counts_when_it_is_stale(session, dirs, project):
    """An older record that still matches the file must not stand in for a changed newer one."""
    plot = dirs[1] / "plot.png"
    plot.write_bytes(b"data")
    await _display(
        session, project, plot, title="Older", shown_at="2026-01-01T10:00:00"
    )
    newer = await _display(
        session, project, plot, title="Newer", shown_at="2026-01-02T10:00:00"
    )
    # The newer record saw other content, so the file now matches only the older one.
    result = await session.exec(
        select(DisplayedFileTable).where(DisplayedFileTable.chat_session_id == newer)
    )
    row = result.one()
    row.size += 1
    session.add(row)
    await session.commit()

    assert (await _listing(session, project))["plot.png"]["shown_in"] is None


async def test_a_never_displayed_file_has_no_shown_in(session, dirs, project):
    (dirs[1] / "plot.png").write_bytes(b"data")
    (dirs[0] / "plot.png").write_bytes(b"data")

    assert (await _listing(session, project))["plot.png"]["shown_in"] is None
    assert (await _listing(session, project, "uploads"))["plot.png"]["shown_in"] is None


async def test_shown_in_is_per_kind(session, dirs, project):
    output = dirs[1] / "data.csv"
    output.write_bytes(b"data")
    (dirs[0] / "data.csv").write_bytes(b"data")
    await _display(
        session, project, output, title="Chat", shown_at="2026-01-01T10:00:00"
    )

    assert (await _listing(session, project))["data.csv"]["shown_in"] is not None
    assert (await _listing(session, project, "uploads"))["data.csv"]["shown_in"] is None
