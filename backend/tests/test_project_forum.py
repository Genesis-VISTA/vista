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

import asyncio
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
from vista_backend.db.schemas import LocalSettingsTable, ProjectTable
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
async def app_client(session):
    """
    An HTTP client over the real app, sharing the test's session.

    Only what the project routes need: the save path is the whole point here.
    """
    import httpx

    from vista_backend.api.api import app
    from vista_backend.db.db import _get_session

    async def _session():
        yield session

    app.dependency_overrides[_get_session] = _session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


def _project(url: str | None = "https://example.invalid/forum.git", name="salt"):
    return ProjectTable(name=name, forum_repo_url=url)


def _settings(**tokens) -> LocalSettingsTable:
    """The settings a forum publishes with: just the row, never saved, with any tokens."""
    return LocalSettingsTable(**tokens)


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
        build_client_for(_project(None), _settings())


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
        build_client_for(_project(), _settings())


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
    await ensure_forum(project, _settings())

    assert (forum_root(project.id) / REPO_DIR / "HEAD").is_file(), "a bare repository"
    client = ForumClient(forum_config_for(project))  # type: ignore[arg-type]
    assert await client.remote() == str(forge)


async def test_ensure_forum_is_idempotent(forum_home, forge):
    """Called on every save, and on first use. Twice must be the same as once."""
    project = _project(str(forge))
    await ensure_forum(project, _settings())
    config = (forum_root(project.id) / REPO_DIR / "config").read_text(encoding="utf-8")
    await ensure_forum(project, _settings())
    assert (forum_root(project.id) / REPO_DIR / "config").read_text(
        encoding="utf-8"
    ) == config


async def test_changing_the_url_moves_the_remote(forum_home, forge, tmp_path):
    other = tmp_path / "forge" / "other.git"
    subprocess.run(["git", "init", "-q", "--bare", str(other)], check=True)
    project = _project(str(forge))
    await ensure_forum(project, _settings())
    project.forum_repo_url = str(other)
    await ensure_forum(project, _settings())
    client = ForumClient(forum_config_for(project))  # type: ignore[arg-type]
    assert await client.remote() == str(other)


async def test_ensure_forum_does_nothing_for_a_project_with_no_lab(forum_home):
    project = _project(None)
    await ensure_forum(project, _settings())
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
        await ensure_forum(_project(typo), _settings())

    assert typo in str(caught.value)
    assert "does not appear to be a git repository" in str(caught.value), (
        "git's reason, not just that it failed"
    )


async def test_saving_without_git_says_the_lab_needs_it(forum_home, monkeypatch):
    missing = GitCheck(ok=False, reason="Git is not installed.")
    monkeypatch.setattr(project_forum, "git_status", lambda: missing)
    with pytest.raises(ForumSetupError, match="needs git. Git is not installed."):
        await ensure_forum(_project(), _settings())


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
    await ensure_forum(project, _settings())
    client = build_client_for(project, _settings())
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


# --------------------------------------------------------------------------- #
# The forum access token
# --------------------------------------------------------------------------- #

TOKEN = "glpat-not-a-real-token"
HTTPS_URL = "https://forge.example.invalid/group/forum.git"


async def test_a_project_holds_no_token(forum_home, forge, app_client):
    """
    A project row holds no secret, so it can be published as it is. A client that
    still sends the old field has it ignored, not stored.
    """
    body = {**_body("tokened", str(forge)), "forum_repo_token": TOKEN}
    resp = await app_client.post("/projects", json=body)

    assert resp.status_code == 201, resp.text
    assert "forum_repo_token" not in resp.json()
    assert "has_forum_token" not in resp.json()
    assert not hasattr(ProjectTable, "forum_repo_token")


GITHUB_FORUM = "https://github.com/o/forum.git"
SETTINGS_TOKEN = "ghp_from_settings"


def _forum_token(project, local=None) -> str | None:
    config = forum_config_for(project, local)
    assert config is not None
    return config.remote_token.get_secret_value() if config.remote_token else None


@pytest.mark.parametrize(
    ("url", "tokens", "expected"),
    [
        # The Settings token for the forum's host.
        (GITHUB_FORUM, {"github_token": SETTINGS_TOKEN}, SETTINGS_TOKEN),
        # Not for another host: a GitHub token never goes to a self-hosted forum,
        # which uses the system git's credentials.
        (HTTPS_URL, {"github_token": SETTINGS_TOKEN}, None),
        # Nothing at all: git uses the system's credentials.
        (GITHUB_FORUM, {}, None),
    ],
)
def test_which_token_a_forum_pushes_with(forum_home, url, tokens, expected):
    project = ProjectTable(name="t", forum_repo_url=url)
    assert _forum_token(project, _settings(**tokens)) == expected


def test_without_settings_a_forum_config_carries_no_token(forum_home):
    """Only `lab_enabled` asks without the settings, and it never reaches the remote."""
    project = ProjectTable(name="t", forum_repo_url=GITHUB_FORUM)
    assert _forum_token(project) is None


def _client(url: str, token: str | None, tmp_path) -> ForumClient:
    from pydantic import SecretStr

    return ForumClient(
        ForumSettings(
            enabled=True,
            repo_root=tmp_path / "forum",
            remote_url=url,
            remote_token=SecretStr(token) if token else None,
        )
    )


def _header_git_sends(env: dict[str, str], url: str) -> str | None:
    """What git itself resolves `http.extraHeader` to for `url`, under `env`."""
    out = subprocess.run(
        ["git", "config", "--get-urlmatch", "http.extraheader", url],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return out.stdout.strip() or None


@pytest.fixture
def machine_wide_header(tmp_path, monkeypatch):
    """
    Git config that already sends headers of its own, as a CI runner's does.

    GitLab's runner sets an unscoped `http.extraHeader`, and its environment
    carries an `Authorization` header for its own remote. So on CI neither "git
    sends nothing elsewhere" nor "no Authorization in the environment" holds.
    What must hold is narrower: the forum's header goes to the forum alone.
    """
    config = tmp_path / "machine-gitconfig"
    config.write_text(
        "[http]\n\textraHeader = X-Gitaly-Correlation-ID: machine\n", encoding="utf-8"
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "http.https://gitlab.com.extraHeader")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "Authorization: Basic cnVubmVy")


def _forum_header() -> str:
    """The header value the forum's token becomes, which nothing else can contain."""
    import base64

    return base64.b64encode(f"oauth2:{TOKEN}".encode()).decode()


def test_git_sends_the_token_to_the_forum_and_nowhere_else(
    forum_home, tmp_path, machine_wide_header
):
    """
    Asked of git, not of our dict: the point is that git scopes the header to the
    remote's URL, so a redirect or another remote never sees it.
    """
    import base64

    env = _client(HTTPS_URL, TOKEN, tmp_path)._env(network=True)

    header = _header_git_sends(env, HTTPS_URL + "/info/refs")
    assert header is not None
    scheme, encoded = header.removeprefix("Authorization: ").split()
    assert scheme == "Basic"
    assert base64.b64decode(encoded).decode() == f"oauth2:{TOKEN}"
    elsewhere = _header_git_sends(env, "https://elsewhere.example.invalid/x.git")
    assert elsewhere is None or _forum_header() not in elsewhere, (
        "only the machine's own header, never the forum's"
    )
    assert TOKEN not in " ".join(env.values()), "only ever encoded"


def test_local_plumbing_never_carries_the_token(
    forum_home, tmp_path, machine_wide_header
):
    """Only fetch and push talk to the remote, so only they get the header."""
    env = _client(HTTPS_URL, TOKEN, tmp_path)._env(network=False)
    assert _forum_header() not in " ".join(env.values())


@pytest.mark.parametrize(
    "url",
    ["http://forge.example.invalid/forum.git", "git@forge.example.invalid:g/forum.git"],
)
def test_no_token_outside_https(forum_home, tmp_path, machine_wide_header, url):
    """Plain HTTP would send it in the clear; SSH authenticates with its own keys."""
    env = _client(url, TOKEN, tmp_path)._env(network=True)
    assert _forum_header() not in " ".join(env.values())


def test_config_already_in_the_environment_is_kept(forum_home, tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "http.sslVerify")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "true")

    env = _client(HTTPS_URL, TOKEN, tmp_path)._env(network=True)

    assert env["GIT_CONFIG_COUNT"] == "2"
    assert env["GIT_CONFIG_KEY_0"] == "http.sslVerify"
    assert env["GIT_CONFIG_KEY_1"] == f"http.{HTTPS_URL}.extraHeader"


# --------------------------------------------------------------------------- #
# A forum hosted on the collaboration server: its credential and its failures
# --------------------------------------------------------------------------- #

SERVER = "https://obsidian.test/vista/api"
HOSTED_URL = f"{SERVER}/git/11111111-2222-4333-8444-555555555555.git"


def _local(**fields) -> LocalSettingsTable:
    base = {
        "collaboration_server_url": SERVER,
        "collaboration_token": "sign-in-token-1234",
        "display_name": "Ada",
        "github_token": "ghp_forge_token",
    }
    return LocalSettingsTable(**{**base, **fields})


def _sent(config: ForumSettings, tmp_path) -> dict[str, str]:
    """The environment git gets for a network command in a client with `config`."""
    return ForumClient(config.model_copy(update={"repo_root": tmp_path / "f"}))._env(
        network=True
    )


def test_a_url_under_the_collaboration_server_is_a_hosted_forum(forum_home):
    config = forum_config_for(_project(HOSTED_URL), _local())
    assert config is not None
    assert config.remote_token_source == "collaboration"
    assert config.remote_token is None, "never a forge token"
    assert config.remote_headers is not None
    assert (
        config.remote_headers["Authorization"].get_secret_value()
        == "Bearer sign-in-token-1234"
    )
    assert "X-Vista-Install-Id" in config.remote_headers


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/lab/forum.git",
        # Another server, and the same server's other paths, are not its forums.
        "https://elsewhere.test/vista/api/git/11111111-2222-4333-8444-555555555555.git",
        f"{SERVER}/items/11111111-2222-4333-8444-555555555555.git",
        f"{SERVER}/git/not-a-forum.git",
    ],
)
def test_other_urls_keep_their_forge_behaviour(forum_home, url):
    config = forum_config_for(_project(url), _local())
    assert config is not None
    assert config.remote_headers is None
    assert config.remote_token_source != "collaboration"


def test_the_forge_token_still_serves_a_forge(forum_home):
    config = forum_config_for(_project("https://github.com/lab/forum.git"), _local())
    assert config is not None
    assert config.remote_token is not None
    assert config.remote_token.get_secret_value() == "ghp_forge_token"
    assert config.remote_token_source == "settings-github"


def test_no_server_configured_means_no_hosted_forum(forum_home, monkeypatch):
    monkeypatch.setattr(app_settings, "collaboration_server_url", None)
    config = forum_config_for(
        _project(HOSTED_URL), _local(collaboration_server_url=None)
    )
    assert config is not None and config.remote_headers is None


# Collaboration off: a hosted forum is out of reach, a forge's is not.


def test_off_a_hosted_forum_has_no_lab(forum_home, monkeypatch):
    built: list = []
    monkeypatch.setattr(project_forum, "client_factory", built.append)
    local = _local(collaboration_enabled=False)
    project = _project(HOSTED_URL)
    assert forum_config_for(project, local) is None
    assert not project_forum.lab_enabled(project, local)
    with pytest.raises(ForumDisabled, match="collaboration is off"):
        build_client_for(project, local)
    assert built == [], "no client, so no git against the server"


async def test_off_saving_a_hosted_forum_sets_nothing_up(forum_home, monkeypatch):
    built: list = []
    monkeypatch.setattr(project_forum, "client_factory", built.append)
    await project_forum.ensure_forum(
        _project(HOSTED_URL), _local(collaboration_enabled=False)
    )
    assert built == []


def test_off_a_forge_forum_is_unaffected(forum_home):
    local = _local(collaboration_enabled=False)
    config = forum_config_for(_project("https://github.com/lab/forum.git"), local)
    assert config is not None
    assert config.remote_token_source == "settings-github"


def test_never_chosen_is_on_for_a_hosted_forum(forum_home):
    """The row's `None` is on: the forum is reachable as before."""
    assert forum_config_for(_project(HOSTED_URL), _local()) is not None


async def test_off_a_new_project_gets_no_hosted_forum(forum_home, session, monkeypatch):
    import httpx

    from vista_backend.db.schemas import LocalSettingsUpdate
    from vista_backend.services import collaboration, hosted_forum, local_settings

    def refuse(request):
        raise AssertionError(f"{request.url} reached the server")

    monkeypatch.setattr(collaboration, "transport", httpx.MockTransport(refuse))
    await local_settings.update(
        session,
        LocalSettingsUpdate(
            collaboration_server_url=SERVER, collaboration_enabled=False
        ),
    )
    project = _project(None)
    session.add(project)
    await session.flush()
    assert await hosted_forum.give_new_project_a_forum(session, project) is None
    assert project.forum_repo_url is None


async def test_off_the_forum_status_says_why(forum_home, session, app_client):
    from vista_backend.db.schemas import LocalSettingsUpdate
    from vista_backend.services import local_settings

    await local_settings.update(
        session,
        LocalSettingsUpdate(
            collaboration_server_url=SERVER, collaboration_enabled=False
        ),
    )
    session.add(_project(HOSTED_URL))
    await session.flush()
    body = (await app_client.get("/projects/salt/forum/status")).json()
    assert body["enabled"] is False and body["collaboration_off"] is True

    await local_settings.update(
        session, LocalSettingsUpdate(collaboration_enabled=True)
    )
    session.add(_project("https://github.com/lab/forum.git", name="forged"))
    await session.flush()
    await local_settings.update(
        session, LocalSettingsUpdate(collaboration_enabled=False)
    )
    forged = (await app_client.get("/projects/forged/forum/status")).json()
    assert forged["collaboration_off"] is False


def test_git_sends_the_sign_in_to_the_hosted_forum_alone(
    forum_home, tmp_path, machine_wide_header
):
    """Asked of git: the sign-in, scoped to the forum's URL; no forge token anywhere."""
    import base64

    config = forum_config_for(_project(HOSTED_URL), _local())
    assert config is not None
    env = _sent(config, tmp_path)

    header = _header_git_sends(env, HOSTED_URL + "/info/refs")
    # git reports only the last of several headers; the others are in the env.
    assert header == "Authorization: Bearer sign-in-token-1234"
    assert "Authorization: Bearer sign-in-token-1234" in env.values()
    assert any(v.startswith("X-Vista-Install-Id: ") for v in env.values())
    elsewhere = _header_git_sends(env, "https://elsewhere.test/x.git")
    assert elsewhere is None or "sign-in-token-1234" not in elsewhere
    joined = " ".join(env.values())
    assert "ghp_forge_token" not in joined
    assert base64.b64encode(b"oauth2:ghp_forge_token").decode() not in joined


def test_the_sign_in_is_never_sent_unencrypted(forum_home, tmp_path):
    remote = (
        "http://obsidian.test/vista/api/git/11111111-2222-4333-8444-555555555555.git"
    )
    local = _local(collaboration_server_url="http://obsidian.test/vista/api")
    config = forum_config_for(_project(remote), local)
    assert config is not None and config.remote_token_source == "collaboration"
    assert "sign-in-token-1234" not in " ".join(_sent(config, tmp_path).values())


def test_this_machine_may_have_the_sign_in_over_http(forum_home, tmp_path):
    remote = "http://127.0.0.1:8002/git/11111111-2222-4333-8444-555555555555.git"
    local = _local(collaboration_server_url="http://127.0.0.1:8002")
    config = forum_config_for(_project(remote), local)
    assert config is not None
    assert "Bearer sign-in-token-1234" in " ".join(_sent(config, tmp_path).values())


def _hosted_client(tmp_path) -> ForumClient:
    config = forum_config_for(_project(HOSTED_URL), _local())
    assert config is not None
    (tmp_path / "forum").mkdir()
    return ForumClient(config.model_copy(update={"repo_root": tmp_path / "forum"}))


async def test_a_refused_sign_in_is_not_a_forum_that_cannot_be_reached(
    forum_home, tmp_path
):
    client = _hosted_client(tmp_path)
    client._record_failure(
        "fatal: unable to access 'https://obsidian.test/': "
        "The requested URL returned error: 401"
    )
    told = await client.publish_error()
    assert told is not None
    assert (told["reason"], told["credential"]) == ("refused", "collaboration")
    assert "collaboration sign-in was refused" in told["message"]
    assert f"{SERVER}/sign-in" in told["message"] and "Settings" in told["message"]
    assert "sign-in-token-1234" not in str(told)


async def test_a_forum_the_account_may_not_use_is_no_access_not_a_refused_sign_in(
    forum_home, tmp_path
):
    client = _hosted_client(tmp_path)
    client._record_failure(
        "fatal: unable to access 'https://obsidian.test/': "
        "The requested URL returned error: 404"
    )
    told = await client.publish_error()
    assert told is not None
    assert (told["reason"], told["credential"]) == ("no_access", "collaboration")
    assert "no forum your account may use" in told["message"]


async def test_a_rule_the_server_refused_is_reported_by_its_message(
    forum_home, tmp_path
):
    client = _hosted_client(tmp_path)
    client._record_failure(
        "remote: Refused refs/heads/vista-forum/threads/t: only the thread's opener "
        "can close it (the account that opened the thread).\n"
        "To https://obsidian.test/vista/api/git/x.git\n"
        " ! [remote rejected] t -> t (pre-receive hook declined)\n"
        "error: failed to push some refs"
    )
    told = await client.publish_error()
    assert told is not None
    assert (told["reason"], told["credential"]) == ("rule", "collaboration")
    assert "only the thread's opener can close it" in told["message"]
    assert "pre-receive" not in told["message"], "the rule, not git's boilerplate"


async def test_a_forge_refusal_is_reported_as_before(forum_home, tmp_path):
    """The forge path is unchanged: its 401 is git's message under the Settings token."""
    config = forum_config_for(_project("https://github.com/lab/forum.git"), _local())
    assert config is not None
    (tmp_path / "forum").mkdir()
    client = ForumClient(config.model_copy(update={"repo_root": tmp_path / "forum"}))
    client._record_failure("remote: Refused x: looks like a rule\nreturned error: 401")
    told = await client.publish_error()
    assert told is not None
    assert (told["reason"], told["credential"]) == ("refused", "settings-github")
    assert "looks like a rule" in told["message"]


# --------------------------------------------------------------------------- #
# Two installs on one hosted forum, and moving a forum onto one
# (a real collaboration server on loopback, and real git)
# --------------------------------------------------------------------------- #

ROSTER = {
    "proposer": "vista-proposer-1",
    "reviewer": "vista-reviewer-1",
    "referee": "vista-referee-1",
}


@pytest.fixture
async def installs(loopback_server, forum_home, tmp_path):
    """Alice and Bob: two VISTA installs, each signed in to the loopback server."""
    from harness.hosted import make_install

    alice = await make_install(tmp_path, "Alice", loopback_server.url)
    bob = await make_install(tmp_path, "Bob", loopback_server.url)
    yield alice, bob
    await alice.close()
    await bob.close()


async def _forum_of(install, project_name: str):
    """(the project's row, the forum client of this install on it); the install is active."""
    from vista_backend.services import local_settings, project as project_service

    row = await project_service.get_project_by_name(install.session, project_name)
    return row, build_client_for(row, await local_settings.get(install.session))


async def test_two_installs_on_one_hosted_forum(
    installs, loopback_server, in_public_team
):
    """
    Alice creates a project (it gets a hosted forum) and publishes it; Bob
    imports it and lists, posts and continues; the server refuses what the lab's
    rules forbid: Bob's Close, and a second run while one is live.
    """
    from harness import api_client
    from vista_backend.services import local_settings, project_collaboration
    from vista_backend.services import threads as thread_service
    from vista_backend.services.forum_git import (
        ForumCommandError,
        PostKind,
        RunRefused,
        rule_refusal,
    )

    alice, bob = installs

    # --- Alice creates the project and opens a thread ----------------------
    async with alice.active() as session:
        with api_client(session) as (api, _):
            created = await api.post("/projects", json=_body("salt", None))
            assert created.status_code == 201, created.text
            assert created.json()["forum_notice"] is None
            url = created.json()["forum_repo_url"]
            assert url.startswith(f"{loopback_server.url}/git/")
            await session.commit()
            _, a_client = await _forum_of(alice, "salt")
            thread = await a_client.create_thread("Does it freeze?", body="Framing.")
            first = await a_client.post_as_human(thread, "Alice says hello.")
            loopback_server.wait_for_records(url)
            published = await api.post("/projects/salt/publish", json={})
            assert published.status_code == 200, published.text
            assert published.json()["forum_error"] is None
            item_id = published.json()["project"]["remote_id"]

    # --- Bob imports it, and sees Alice's thread, by her verified name ------
    async with bob.active() as session:
        result = await project_collaboration.import_project(
            session, await local_settings.get_public(session), uuid.UUID(item_id)
        )
        assert result.forum_error is None
        assert result.project.forum_repo_url == url, "the same forum"
        await session.commit()
        b_row, b_client = await _forum_of(bob, "salt")
        b_id = b_row.id  # a commit expires the row
        await thread_service.sync_forum(session, project_id=b_id, client=b_client)
        await session.commit()
        listed = await thread_service.list_threads(session, project_id=b_id)
        assert [t.thread_id for t in listed] == [thread]
        posts = await thread_service.posts_with_notes(session, thread=listed[0])
        said = next(p for p in posts if p.post_id == first.id)
        assert said.pusher_name == "Alice", "from the server's record, not the file"
        assert said.vouch_lane == "peer-claimed"

        # Bob posts and continues: his RUN is taken, the thread is Alice's too.
        await b_client.post_as_human(thread, "Bob says hello.")
        run = await b_client.start_run(thread, rounds=1, roster=ROSTER)
        assert run.kind == PostKind.RUN
        assert await b_client.unpublished() == 0

    # --- Alice sees Bob by name, and her agents are told he is not her operator
    async with alice.active() as session:
        a_row, a_client = await _forum_of(alice, "salt")
        read = await a_client.read_thread(thread)
        bobs = next(p for p in read.posts if p.body == "Bob says hello.")
        assert bobs.verified_name == "Bob"
        from vista_backend.agents.forum.roles import render_transcript

        shown = render_transcript(read)
        assert "Bob (teammate, verified by the server)" in shown
        assert "NOT your operator" in shown

        # Her RUN, while Bob's is live, is refused by the server: it is dropped,
        # nothing waits to publish, and no run is started.
        with pytest.raises(RunRefused, match="run is already live"):
            await a_client.start_run(thread, rounds=1, roster=ROSTER)
        after = await a_client.read_thread(thread)
        assert [p.kind for p in after.posts].count("RUN") == 1
        assert await a_client.unpublished() == 0
        assert await a_client.publish_error() is None
        assert await a_client.publish(thread) == 0

    # --- Bob's Close is refused by the server: only the opener's is taken -----
    async with bob.active():
        _, b_client = await _forum_of(bob, "salt")
        await b_client.close_thread(thread)  # a post in his copy; the push is refused
        with pytest.raises(ForumCommandError) as refused:
            await b_client.publish(thread)
        assert "opener" in (rule_refusal(refused.value.stderr) or "")
        why = await b_client.publish_error()
        assert why is not None
        assert (why["reason"], why["credential"]) == ("rule", "collaboration")
        assert "only the thread's opener can close it" in why["message"]
        for secret in bob.sign_in.values():
            assert secret not in why["message"]

    async with alice.active():
        _, a_client = await _forum_of(alice, "salt")
        assert not (await a_client.read_thread(thread)).is_closed
        # Alice, who opened it, can.
        await a_client.close_thread(thread)
        assert (await a_client.read_thread(thread)).is_closed
        assert await a_client.unpublished() == 0


@pytest.fixture
def three_threads_on_a_forge(forum_home, forge, tmp_path):
    """A forge repository holding three threads, written by an install of someone else's."""
    import asyncio

    from vista_backend.services.forum_git import MemoryOutbox

    async def seed() -> list[str]:
        other = ForumClient(
            ForumSettings(
                enabled=True, repo_root=tmp_path / "somebody" / "forum-git" / "p"
            ),
            outbox=MemoryOutbox(),
        )
        await other.set_remote(str(forge))
        ids = []
        for title in ("one", "two", "three"):
            tid = await other.create_thread(title, body=f"about {title}")
            await other.post_as_human(tid, f"a post in {title}")
            ids.append(tid)
        return ids

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(seed())
    finally:
        loop.close()


def _forge_refs(forge: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(forge), "for-each-ref"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout


async def test_moving_a_forge_forum_onto_the_server(
    installs, loopback_server, forge, three_threads_on_a_forge
):
    from vista_backend.db.schemas import ProjectCreate
    from vista_backend.services import hosted_forum, project as project_service
    from vista_backend.services import threads as thread_service

    alice, _ = installs
    refs_before = _forge_refs(forge)
    async with alice.active() as session:
        project = await project_service.create_project(
            session, ProjectCreate(name="salt", forum_repo_url=str(forge))
        )
        await session.commit()
        await session.refresh(project)
        moved = await hosted_forum.move_to_hosted(session, project)
        moved_id, hosted_url = moved.id, moved.forum_repo_url
        await session.commit()
        assert hosted_url.startswith(f"{loopback_server.url}/git/")

        _, client = await _forum_of(alice, "salt")
        await thread_service.sync_forum(session, project_id=moved_id, client=client)
        listed = await thread_service.list_threads(session, project_id=moved_id)
        assert sorted(t.thread_id for t in listed) == sorted(three_threads_on_a_forge)
        for row in listed:
            posts = await thread_service.list_posts(session, thread=row)
            assert [p.kind for p in posts] == ["TASK", "ASK"]
            # Copied history is not attributed to whoever moved it.
            assert all(p.pusher_name is None for p in posts)
    assert _forge_refs(forge) == refs_before, "the forge repository is untouched"

    # And a teammate who joins the forum reads the same three, with their history.
    forum_id = hosted_url.rsplit("/", 1)[-1].removesuffix(".git")
    records = loopback_server.api(alice.install_id).get(f"/forums/{forum_id}/pushes")
    assert {r["imported"] for r in records.json()} == {True}


async def test_a_move_that_fails_leaves_the_project_on_its_forum(
    installs, loopback_server, forge, three_threads_on_a_forge, monkeypatch
):
    from fastapi import HTTPException

    from vista_backend.db.schemas import ProjectCreate
    from vista_backend.services import hosted_forum, project as project_service
    from vista_backend.services.forum_git import ForumCommandError

    alice, _ = installs
    created: list[str] = []
    real = ForumClient.push_threads_for_import

    async def refuse(self):
        created.append(self.config.remote_url)
        raise ForumCommandError(["git", "push"], 1, "", "remote: Refused: not today")

    monkeypatch.setattr(ForumClient, "push_threads_for_import", refuse)
    async with alice.active() as session:
        project = await project_service.create_project(
            session, ProjectCreate(name="salt", forum_repo_url=str(forge))
        )
        await session.commit()
        await session.refresh(project)
        with pytest.raises(HTTPException) as failed:
            await hosted_forum.move_to_hosted(session, project)
        assert failed.value.status_code == 400
        assert "still uses" in failed.value.detail
        assert project.forum_repo_url == str(forge), "the URL is unchanged"
        _, client = await _forum_of(alice, "salt")
        assert await client.remote() == str(forge)
    assert real is not refuse and len(created) == 1
    forum_id = created[0].rsplit("/", 1)[-1].removesuffix(".git")
    assert not loopback_server.forum_exists(alice.install_id, forum_id), (
        "the empty forum is deleted"
    )


async def test_a_project_with_no_forum_gets_an_empty_hosted_one(
    installs, loopback_server
):
    from vista_backend.db.schemas import ProjectCreate
    from vista_backend.services import hosted_forum, project as project_service

    alice, _ = installs
    async with alice.active() as session:
        project = await project_service.create_project(
            session, ProjectCreate(name="salt")
        )
        await session.commit()
        await session.refresh(project)
        moved = await hosted_forum.move_to_hosted(session, project)
        assert moved.forum_repo_url.startswith(f"{loopback_server.url}/git/")
        _, client = await _forum_of(alice, "salt")
        assert await client.list_threads() == []


async def _alice_and_bob_on_a_published_forum(installs, loopback_server):
    """Alice's published project with one thread, imported and synced by Bob."""
    from harness import api_client
    from vista_backend.services import local_settings, project_collaboration
    from vista_backend.services import threads as thread_service

    alice, bob = installs
    async with alice.active() as session:
        with api_client(session) as (api, _):
            created = await api.post("/projects", json=_body("salt", None))
            assert created.status_code == 201, created.text
            await session.commit()
            _, a_client = await _forum_of(alice, "salt")
            thread = await a_client.create_thread("Does it freeze?", body="Framing.")
            loopback_server.wait_for_records(created.json()["forum_repo_url"])
            published = await api.post("/projects/salt/publish", json={})
            assert published.status_code == 200, published.text
            item_id = published.json()["project"]["remote_id"]
    async with bob.active() as session:
        await project_collaboration.import_project(
            session, await local_settings.get_public(session), uuid.UUID(item_id)
        )
        await session.commit()
        b_row, b_client = await _forum_of(bob, "salt")
        b_id = b_row.id
        await thread_service.sync_forum(session, project_id=b_id, client=b_client)
        await session.commit()
    return thread


async def test_a_continue_that_loses_the_race_for_a_run_is_a_409(
    installs, loopback_server, in_public_team, monkeypatch
):
    """
    Bob's last fetch predates Alice's RUN, so his own check finds no live run;
    the server refuses his RUN. The Continue answers 409, nothing is scheduled,
    and nothing of his RUN is left to publish or shown in his copy.
    """
    from harness import api_client
    from vista_backend.api import debate as debate_api

    alice, bob = installs
    thread = await _alice_and_bob_on_a_published_forum(installs, loopback_server)
    async with alice.active():
        _, a_client = await _forum_of(alice, "salt")
        await a_client.start_run(thread, rounds=1, roster=ROSTER)

    spawned: list = []

    async def fake_task(*args, **kwargs):
        spawned.append((args, kwargs))

    async def stale(session, row, client):
        """The thread as Bob last read it: before Alice's RUN."""

    monkeypatch.setattr(debate_api, "continue_debate_task", fake_task)
    monkeypatch.setattr(debate_api, "refresh_thread", stale)
    async with bob.active() as session:
        with api_client(session) as (api, _):
            res = await api.post(
                f"/projects/salt/threads/{thread}/runs", json={"rounds": 1}
            )
        assert res.status_code == 409, res.text
        assert res.json()["detail"] == "Another user is continuing this debate."
        assert spawned == []
        _, b_client = await _forum_of(bob, "salt")
        assert await b_client.unpublished() == 0
        assert await b_client.publish_error() is None
        read = await b_client.read_thread(thread)
        assert [p.kind for p in read.posts].count("RUN") == 1, "only Alice's"


async def test_a_hosted_continue_posts_its_run_before_it_answers(
    installs, loopback_server, in_public_team, monkeypatch
):
    from harness import api_client
    from vista_backend.api import debate as debate_api

    _, bob = installs
    thread = await _alice_and_bob_on_a_published_forum(installs, loopback_server)
    spawned: list = []

    async def fake_task(*args, **kwargs):
        spawned.append((args, kwargs))

    monkeypatch.setattr(debate_api, "continue_debate_task", fake_task)
    async with bob.active() as session:
        with api_client(session) as (api, _):
            res = await api.post(
                f"/projects/salt/threads/{thread}/runs", json={"rounds": 2}
            )
        assert res.status_code == 200, res.text
        await asyncio.sleep(0.05)  # the spawned task
        ((args, kwargs),) = spawned
        assert args[1] == 2 and kwargs == {"announced": True}, (
            "run() must not announce again"
        )
        _, b_client = await _forum_of(bob, "salt")
        assert await b_client.unpublished() == 0, "already on the server"
        read = await b_client.read_thread(thread)
        (run,) = [p for p in read.posts if p.kind == "RUN"]
        assert run.run is not None and run.run.rounds == 2
        assert set(run.run.roster) == {"proposer", "reviewer", "referee"}


async def test_a_hosted_continue_that_fails_after_its_run_ends_it(
    installs, loopback_server, in_public_team, monkeypatch
):
    """The RUN is on the server; if the handler then fails, the Referee ends it."""
    from harness import api_client
    from vista_backend.api import debate as debate_api
    from vista_backend.services.forum_git import live_run
    from datetime import UTC, datetime

    _, bob = installs
    thread = await _alice_and_bob_on_a_published_forum(installs, loopback_server)

    async def broken(*_a, **_k):
        raise RuntimeError("could not build the reply")

    monkeypatch.setattr(debate_api, "_thread_public", broken)
    async with bob.active() as session:
        with api_client(session) as (api, _):
            with pytest.raises(RuntimeError, match="could not build the reply"):
                await api.post(
                    f"/projects/salt/threads/{thread}/runs", json={"rounds": 1}
                )
        _, b_client = await _forum_of(bob, "salt")
        read = await b_client.read_thread(thread)
        assert read.posts[-1].kind == "BLOCKED"
        assert live_run(read.posts, now=datetime.now(UTC), expiry=2700.0) is None


@pytest.mark.parametrize("failure", ["commit", "cancelled"])
async def test_a_hosted_continue_ends_its_run_when_the_session_or_request_fails(
    installs, loopback_server, in_public_team, monkeypatch, failure
):
    """
    A failed commit rolls back the adopted run's rows, and a cancelled request
    is no `Exception`: the Referee's BLOCKED goes through the forum client
    alone, so neither leaves a live run.
    """
    import asyncio
    from datetime import UTC, datetime

    from harness import api_client
    from vista_backend.api import debate as debate_api
    from vista_backend.services.forum_git import live_run

    _, bob = installs
    thread = await _alice_and_bob_on_a_published_forum(installs, loopback_server)

    async def cancelled(*_a, **_k):
        raise asyncio.CancelledError

    async with bob.active() as session:
        with monkeypatch.context() as mp:
            if failure == "cancelled":
                mp.setattr(debate_api, "_thread_public", cancelled)
                expected: type[BaseException] = asyncio.CancelledError
            else:

                async def commit():
                    raise RuntimeError("secret commit failure")

                mp.setattr(session, "commit", commit)
                expected = RuntimeError
            with api_client(session) as (api, _):
                with pytest.raises(expected):
                    await api.post(
                        f"/projects/salt/threads/{thread}/runs", json={"rounds": 1}
                    )
        _, b_client = await _forum_of(bob, "salt")
        read = await b_client.read_thread(thread)
        assert read.posts[-1].kind == "BLOCKED"
        assert read.posts[-1].role == "referee"
        assert "secret" not in read.posts[-1].body
        assert live_run(read.posts, now=datetime.now(UTC), expiry=2700.0) is None
