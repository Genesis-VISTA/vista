"""
Tests for the Hypothesis Lab's forum being a property of the project.

What these pin, mostly, is that one project's forum cannot become another's — or
the old deployment-wide one's. A forum is a room whose guest list is the push
access on a git repository, so a client pointed at the wrong repository is not a
cosmetic bug: it is a debate published to people who were never asked.
"""

import uuid
from pathlib import Path

import pytest

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
from vista_backend.services.h5i_forum import (
    Enrollment,
    ForumClient,
    ForumDisabled,
    VotePolicy,
)


FAKE = Path(__file__).parent / "fixtures" / "fake_h5i.py"

pytestmark = pytest.mark.anyio


@pytest.fixture
def forum_home(tmp_path, monkeypatch):
    """A deployment whose forums live under tmp_path and speak to the fake h5i."""
    monkeypatch.setattr(app_settings, "data_dir", tmp_path)
    monkeypatch.setattr(
        app_settings,
        "forum",
        ForumSettings(enabled=True, binary=str(FAKE), timeout=30.0),
    )
    return tmp_path


@pytest.fixture
async def app_client(session, alice):
    """
    An HTTP client over the real app, sharing the test's session and identity.

    Only what the project routes need: the save path is the whole point here, so
    the stream and forum module globals the debate tests juggle are irrelevant.
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
    """The deployment switch still wins: no h5i, no labs, whatever projects say."""
    monkeypatch.setattr(app_settings, "forum", ForumSettings(enabled=False))
    assert not lab_enabled(_project())


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
            binary=str(FAKE),
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


def test_a_renamed_project_keeps_its_forum(forum_home):
    """
    Keyed by id, not name. The h5i state lives in that directory's `.git/.h5i/`,
    so keying by name would make a rename read as "this project has no lab,
    start an empty one" — with the threads still in the old directory.
    """
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


async def test_ensure_forum_creates_the_repo_and_points_it_at_the_url(forum_home):
    project = _project("https://example.invalid/forum.git")
    await ensure_forum(project)

    root = forum_root(project.id)
    assert (root / ".git").is_dir(), "h5i keeps the forum under a repository's .git"
    assert (
        "https://example.invalid/forum.git"
        in await ForumClient(
            forum_config_for(project)  # type: ignore[arg-type]
        ).remote()
    )


async def test_ensure_forum_is_idempotent(forum_home):
    """Called on every save, and on first use. Twice must be the same as once."""
    project = _project()
    await ensure_forum(project)
    head = (forum_root(project.id) / ".git" / "HEAD").read_bytes()
    await ensure_forum(project)
    assert (forum_root(project.id) / ".git" / "HEAD").read_bytes() == head


async def test_ensure_forum_does_nothing_for_a_project_with_no_lab(forum_home):
    project = _project(None)
    await ensure_forum(project)
    assert not forum_root(project.id).exists(), "no URL, no directory"


async def test_an_unreachable_url_is_raised_to_whoever_is_saving(
    forum_home, monkeypatch
):
    """
    `h5i forum remote` accepts any string, so a typo is not discovered until
    something tries to reach it. The sync is what turns that into an error in
    the dialog the person is still looking at, rather than a broken lab they
    find days later.
    """
    from vista_backend.services.h5i_forum import ForumCommandError

    async def unreachable(self):
        raise ForumCommandError(["h5i", "forum", "sync"], 1, "", "host is down")

    monkeypatch.setattr(ForumClient, "sync", unreachable)

    with pytest.raises(ForumSetupError) as caught:
        await ensure_forum(_project("https://example.invalid/typo.git"))

    assert "https://example.invalid/typo.git" in str(caught.value)
    assert "host is down" in str(caught.value), "the reason, not just that it failed"


# --------------------------------------------------------------------------- #
# Vote policy, applied when it would mean something
# --------------------------------------------------------------------------- #


async def test_principal_is_not_set_while_nobody_is_enrolled(forum_home, monkeypatch):
    """
    The trap this guard exists for: `principal` counts nothing from an unenrolled
    machine, so switching before anyone enrolls discards every vote on the forum
    — including the debate agents' own.
    """
    monkeypatch.setattr(
        app_settings,
        "forum",
        app_settings.forum.model_copy(update={"vote_policy": "principal"}),
    )
    project = _project()
    await ensure_forum(project)

    client = ForumClient(forum_config_for(project))  # type: ignore[arg-type]
    assert await client.enrollments() == []
    assert await client.vote_policy() == VotePolicy.ORIGIN, "left alone, deliberately"


async def test_principal_is_set_once_somebody_is_enrolled(forum_home, monkeypatch):
    monkeypatch.setattr(
        app_settings,
        "forum",
        app_settings.forum.model_copy(update={"vote_policy": "principal"}),
    )

    async def enrolled(self):
        return [Enrollment(principal="github.com/someone/1", origin="host-a")]

    monkeypatch.setattr(ForumClient, "enrollments", enrolled)
    project = _project()
    await ensure_forum(project)

    client = ForumClient(forum_config_for(project))  # type: ignore[arg-type]
    assert await client.vote_policy() == VotePolicy.PRINCIPAL


async def test_a_policy_that_cannot_be_set_does_not_fail_the_save(
    forum_home, monkeypatch
):
    """The forum is already usable; a policy is not a reason to refuse the project."""
    monkeypatch.setattr(
        app_settings,
        "forum",
        app_settings.forum.model_copy(update={"vote_policy": "principal"}),
    )

    async def boom(self):
        raise RuntimeError("policy is unreadable")

    monkeypatch.setattr(ForumClient, "vote_policy", boom)
    await ensure_forum(_project())  # must not raise


# --------------------------------------------------------------------------- #
# Saving a project through the API
# --------------------------------------------------------------------------- #


async def test_saving_a_project_opens_its_lab(forum_home, app_client):
    """The URL in the dialog is the whole setup; nothing else has to be run."""
    resp = await app_client.post(
        "/projects",
        json={
            "name": "with-a-lab",
            "description": None,
            "system_prompt": None,
            "skills": [],
            "knowledge_bases": [],
            "forum_repo_url": "https://example.invalid/forum.git",
            "tools": [],
            "usage_limits": {},
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["forum_repo_url"] == "https://example.invalid/forum.git"

    project_id = uuid.UUID(resp.json()["id"])
    assert (forum_root(project_id) / ".git").is_dir(), "the lab was set up on save"


async def test_a_url_that_cannot_be_reached_fails_the_save(
    forum_home, app_client, monkeypatch
):
    """
    A typo is a thing to fix now, in the dialog, not a broken lab to find later.

    `h5i forum remote` accepts any string, so nothing else in the system would
    have objected — the debate would simply have published nowhere.
    """
    from vista_backend.services.h5i_forum import ForumCommandError

    async def unreachable(self):
        raise ForumCommandError(["h5i", "forum", "sync"], 1, "", "no such host")

    monkeypatch.setattr(ForumClient, "sync", unreachable)

    resp = await app_client.post(
        "/projects",
        json={
            "name": "bad-url",
            "description": None,
            "system_prompt": None,
            "skills": [],
            "knowledge_bases": [],
            "forum_repo_url": "https://example.invalid/typo.git",
            "tools": [],
            "usage_limits": {},
        },
    )
    assert resp.status_code == 400
    assert "no such host" in resp.json()["detail"], "the git error, not a generic one"


async def test_a_project_without_a_url_saves_with_no_lab(forum_home, app_client):
    resp = await app_client.post(
        "/projects",
        json={
            "name": "no-lab",
            "description": None,
            "system_prompt": None,
            "skills": [],
            "knowledge_bases": [],
            "forum_repo_url": None,
            "tools": [],
            "usage_limits": {},
        },
    )
    assert resp.status_code == 201
    assert resp.json()["forum_repo_url"] is None
    assert not forum_root(uuid.UUID(resp.json()["id"])).exists()


async def test_adding_a_lab_to_an_existing_project_sets_it_up(forum_home, app_client):
    """
    Turning the lab on is an edit, not only a creation — and the common one: a
    project exists long before anyone decides its debates should be published.
    """
    body = {
        "name": "later",
        "description": None,
        "system_prompt": None,
        "skills": [],
        "knowledge_bases": [],
        "forum_repo_url": None,
        "tools": [],
        "usage_limits": {},
    }
    created = await app_client.post("/projects", json=body)
    project_id = uuid.UUID(created.json()["id"])
    assert not forum_root(project_id).exists()

    body["forum_repo_url"] = "https://example.invalid/forum.git"
    resp = await app_client.put("/projects/later", json=body)

    assert resp.status_code == 200, resp.text
    assert (forum_root(project_id) / ".git").is_dir(), "the edit opened the lab"
