"""
Tests for the Hypothesis Lab's forum being a property of the project.

What these pin, mostly, is that one project's forum cannot become another's — or
the old deployment-wide one's. A forum is a room whose guest list is the push
access on a git repository, so a client pointed at the wrong repository is not a
cosmetic bug: it is a debate published to people who were never asked.

The setup path runs real git against a local bare repository standing in for
the forge, because what it has to prove — that a URL is reachable, that joining
an existing forum lists its threads — is about git, not about VISTA.
"""

import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from vista_backend.agents.forum import project_forum
from vista_backend.agents.forum.project_forum import (
    ForumSetupError,
    build_client_for,
    check_legacy_forum_env,
    ensure_forum,
    forum_config_for,
    forum_root,
    lab_enabled,
)
from vista_backend.config import ForumSettings
from vista_backend.config import settings as app_settings
from vista_backend.db.schemas import ProjectTable
from vista_backend.services.forum_git import REPO_DIR, ForumClient, ForumDisabled
from vista_backend.services.git_check import GitCheck

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(shutil.which("git") is None, reason="needs git"),
]


@pytest.fixture
def forge(tmp_path) -> Path:
    """A bare repository standing in for the forge."""
    path = tmp_path / "forge" / "forum.git"
    path.parent.mkdir()
    subprocess.run(["git", "init", "-q", "--bare", str(path)], check=True)
    return path


@pytest.fixture
def forum_home(tmp_path, monkeypatch, git_ok):
    """A deployment whose forums live under tmp_path."""
    monkeypatch.setattr(app_settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(
        app_settings, "forum", ForumSettings(enabled=True, timeout=30.0)
    )
    return tmp_path


@pytest.fixture
async def app_client(session, alice):
    """
    An HTTP client over the real app, sharing the test's session and identity.

    Only what the project routes need: the save path is the whole point here.
    """
    import httpx

    from vista_backend.api.api import app
    from vista_backend.db.db import _get_session
    from vista_backend.services.auth import get_user

    async def _session():
        yield session

    async def _user():
        return alice

    app.dependency_overrides[_get_session] = _session
    app.dependency_overrides[get_user] = _user
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


def _project(url: str | None = "https://example.invalid/forum.git", name="salt"):
    return ProjectTable(name=name, forum_repo_url=url)


def _body(name: str, url: str | None) -> dict:
    return {
        "name": name,
        "description": None,
        "system_prompt": None,
        "skills": [],
        "knowledge_bases": [],
        "forum_repo_url": url,
        "tools": [],
        "usage_limits": {},
    }


# --------------------------------------------------------------------------- #
# Which projects have a lab
# --------------------------------------------------------------------------- #


def test_a_project_with_no_url_has_no_lab(forum_home):
    """
    The honest default. A debate with nowhere to publish is a private argument
    with nobody to check it, and offering the feature would promise a peer
    review that cannot arrive.
    """
    assert forum_config_for(_project(None)) is None
    assert not lab_enabled(_project(None))
    with pytest.raises(ForumDisabled):
        build_client_for(_project(None))


def test_a_blank_url_counts_as_none(forum_home):
    """A text input returns "", and "   " is what a half-deleted URL leaves."""
    assert not lab_enabled(_project(""))
    assert not lab_enabled(_project("   "))


def test_no_lab_anywhere_when_the_feature_is_off(forum_home, monkeypatch):
    """The deployment switch still wins, whatever projects say."""
    monkeypatch.setattr(app_settings, "forum", ForumSettings(enabled=False))
    assert not lab_enabled(_project())


def test_no_lab_anywhere_without_git(forum_home, monkeypatch):
    """No git, no labs — and building a client says why, in the user's words."""
    missing = GitCheck(ok=False, reason="Git is not installed.")
    monkeypatch.setattr(project_forum, "git_status", lambda: missing)
    assert not lab_enabled(_project())
    with pytest.raises(ForumDisabled, match="Git is not installed."):
        build_client_for(_project())


def test_the_repository_comes_from_the_project_never_the_environment(
    forum_home, monkeypatch, tmp_path
):
    """
    The regression this whole change exists to make impossible.

    `VISTA_BACKEND_FORUM__REPO_ROOT` used to select one forum for the whole
    deployment. A stale line in a `.env` still parses, so a call site that forgot
    to scope itself to a project would keep working — against the old shared
    forum, silently, and only on the machine that still had that line.
    """
    stale = tmp_path / "old-shared-forum"
    monkeypatch.setattr(
        app_settings,
        "forum",
        ForumSettings(
            enabled=True,
            repo_root=stale,
            remote_url="https://example.invalid/the-old-one.git",
        ),
    )
    project = _project("https://example.invalid/ours.git")
    config = forum_config_for(project)

    assert config is not None
    assert config.repo_root != stale
    assert config.repo_root == forum_root(project.id)
    assert config.remote_url == "https://example.invalid/ours.git"


def test_two_projects_do_not_share_a_forum(forum_home):
    a, b = _project(name="salt"), _project(name="alloy")
    assert forum_config_for(a).repo_root != forum_config_for(b).repo_root  # type: ignore[union-attr]


def test_forums_live_under_forum_git_by_project_id(forum_home):
    project = _project(name="salt")
    assert forum_root(project.id) == app_settings.data_dir / "forum-git" / str(
        project.id
    )


def test_a_renamed_project_keeps_its_forum(forum_home):
    """Keyed by id, not name, so a rename does not read as "start an empty one"."""
    project = _project(name="salt")
    before = forum_root(project.id)
    project.name = "molten-salt"
    assert forum_root(project.id) == before


def test_the_legacy_env_is_disowned_out_loud(forum_home, monkeypatch, caplog):
    monkeypatch.setattr(
        app_settings,
        "forum",
        ForumSettings(
            enabled=True, repo_root=Path("/old/forum"), remote_url="https://old/x.git"
        ),
    )
    with caplog.at_level("WARNING"):
        check_legacy_forum_env()

    assert "no longer do anything" in caplog.text
    assert "https://old/x.git" in caplog.text, "say which URL to move"
    # Cleared, not just complained about: left in place they would keep
    # resolving, and a missed call site would work on one machine only.
    assert app_settings.forum.repo_root is None
    assert app_settings.forum.remote_url is None


# --------------------------------------------------------------------------- #
# Setting one up
# --------------------------------------------------------------------------- #


async def test_ensure_forum_creates_the_repo_and_points_it_at_the_url(
    forum_home, forge
):
    project = _project(str(forge))
    await ensure_forum(project)

    assert (forum_root(project.id) / REPO_DIR / "HEAD").is_file(), "a bare repository"
    client = ForumClient(forum_config_for(project))  # type: ignore[arg-type]
    assert await client.remote() == str(forge)


async def test_ensure_forum_is_idempotent(forum_home, forge):
    """Called on every save, and on first use. Twice must be the same as once."""
    project = _project(str(forge))
    await ensure_forum(project)
    config = (forum_root(project.id) / REPO_DIR / "config").read_text(encoding="utf-8")
    await ensure_forum(project)
    assert (forum_root(project.id) / REPO_DIR / "config").read_text(
        encoding="utf-8"
    ) == config


async def test_changing_the_url_moves_the_remote(forum_home, forge, tmp_path):
    other = tmp_path / "forge" / "other.git"
    subprocess.run(["git", "init", "-q", "--bare", str(other)], check=True)
    project = _project(str(forge))
    await ensure_forum(project)
    project.forum_repo_url = str(other)
    await ensure_forum(project)
    client = ForumClient(forum_config_for(project))  # type: ignore[arg-type]
    assert await client.remote() == str(other)


async def test_ensure_forum_does_nothing_for_a_project_with_no_lab(forum_home):
    project = _project(None)
    await ensure_forum(project)
    assert not forum_root(project.id).exists(), "no URL, no directory"


async def test_an_unreachable_url_is_raised_with_gits_reason(forum_home, tmp_path):
    """
    Git accepts any string as a remote, so a typo is not discovered until
    something tries to reach it. The sync is what turns that into an error in
    the dialog the person is still looking at, rather than a broken lab they
    find days later.
    """
    typo = str(tmp_path / "no-such-forge" / "typo.git")
    with pytest.raises(ForumSetupError) as caught:
        await ensure_forum(_project(typo))

    assert typo in str(caught.value)
    assert "does not appear to be a git repository" in str(caught.value), (
        "git's reason, not just that it failed"
    )


async def test_saving_without_git_says_the_lab_needs_it(forum_home, monkeypatch):
    missing = GitCheck(ok=False, reason="Git is not installed.")
    monkeypatch.setattr(project_forum, "git_status", lambda: missing)
    with pytest.raises(ForumSetupError, match="needs git. Git is not installed."):
        await ensure_forum(_project())


async def test_joining_an_existing_forum_lists_its_threads(forum_home, forge, tmp_path):
    """Pointing a new project at a repository that holds threads joins them."""
    from vista_backend.services.forum_git import MemoryOutbox

    elsewhere = ForumClient(
        ForumSettings(
            enabled=True, repo_root=tmp_path / "elsewhere" / "forum-git" / "p"
        ),
        outbox=MemoryOutbox(),
    )
    await elsewhere.set_remote(str(forge))
    thread = await elsewhere.create_thread("already being argued", body="go")

    project = _project(str(forge))
    await ensure_forum(project)
    client = build_client_for(project)
    assert [t.id for t in await client.list_threads()] == [thread]


# --------------------------------------------------------------------------- #
# Saving a project through the API
# --------------------------------------------------------------------------- #


async def test_saving_a_project_opens_its_lab(forum_home, forge, app_client):
    """The URL in the dialog is the whole setup; nothing else has to be run."""
    resp = await app_client.post("/projects", json=_body("with-a-lab", str(forge)))
    assert resp.status_code == 201, resp.text
    assert resp.json()["forum_repo_url"] == str(forge)

    project_id = uuid.UUID(resp.json()["id"])
    assert (forum_root(project_id) / REPO_DIR / "HEAD").is_file(), "set up on save"


async def test_a_url_that_cannot_be_reached_fails_the_save(
    forum_home, app_client, tmp_path
):
    """A typo is a thing to fix now, in the dialog, not a broken lab to find later."""
    typo = str(tmp_path / "no-such-forge" / "typo.git")
    resp = await app_client.post("/projects", json=_body("bad-url", typo))
    assert resp.status_code == 400
    assert "does not appear to be a git repository" in resp.json()["detail"], (
        "the git error, not a generic one"
    )


async def test_a_project_without_a_url_saves_with_no_lab(forum_home, app_client):
    resp = await app_client.post("/projects", json=_body("no-lab", None))
    assert resp.status_code == 201
    assert resp.json()["forum_repo_url"] is None
    assert not forum_root(uuid.UUID(resp.json()["id"])).exists()


async def test_adding_a_lab_to_an_existing_project_sets_it_up(
    forum_home, forge, app_client
):
    """
    Turning the lab on is an edit, not only a creation — and the common one: a
    project exists long before anyone decides its debates should be published.
    """
    body = _body("later", None)
    created = await app_client.post("/projects", json=body)
    project_id = uuid.UUID(created.json()["id"])
    assert not forum_root(project_id).exists()

    body["forum_repo_url"] = str(forge)
    resp = await app_client.put("/projects/later", json=body)

    assert resp.status_code == 200, resp.text
    assert (forum_root(project_id) / REPO_DIR / "HEAD").is_file(), (
        "the edit opened the lab"
    )
