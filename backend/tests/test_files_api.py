"""
HTTP contract for serving project files safely (project-files).

A file is shown inline only for allowlisted raster image types and PDF, by
extension; everything else is an attachment. Every response forbids sniffing and
carries a sandboxing CSP. Runs in-process against the real app with the agent
pool faked onto temporary directories.
"""

from types import SimpleNamespace

import pytest
from harness import FakeAgentPool, api_client, seed_project

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
