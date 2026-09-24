"""
The Hypothesis Lab's forum, chosen per project.

A forum is a room. Who may post to it is who has push access to its git
repository, and that is a different set of people for every line of work — a
molten-salt debate and an alloy-design debate are not the same conversation and
should not share a collaborator list. So the repository is a property of the
*project*, set by the people who decide who is in the room, rather than a
deployment-wide `.env` line set by whoever installed the backend.

A project with no `forum_repo_url` has no lab. That is the honest default: a
debate with nowhere to publish is a private argument with nobody to check it,
and offering the feature anyway would promise a peer review that cannot arrive.

The lab also needs git on this machine (`services/git_check.py`). Without it
every project's lab is off, and saving a forum URL says why.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from ...config import ForumSettings, settings
from ...db.schemas import ProjectTable
from ...services.forum_git import ForumClient, ForumDisabled
from ...services.git_check import git_status


logger = logging.getLogger(__name__)


client_factory: type[ForumClient] = ForumClient
"""
What `build_client_for` constructs. Tests put an in-memory fake here; nothing
else should change it.
"""


class ForumSetupError(RuntimeError):
    """
    Initialising a project's forum failed, with the reason the tooling gave.

    Raised rather than logged because this runs while someone is saving a
    project and watching a dialog: a URL that does not resolve is a typo to fix
    now, not a warning to find later in a log they are not reading.
    """


def forum_root(project_id: uuid.UUID) -> Path:
    """
    Where this project's forum lives: its bare repository and kept attachments.

    Keyed by id rather than name so renaming a project does not orphan its
    forum.
    """
    return settings.forum_git_dir / str(project_id)


def forum_url_of(project: ProjectTable) -> str | None:
    """The project's forum URL, or None when it has no lab. Blank counts as none."""
    url = (project.forum_repo_url or "").strip()
    return url or None


def forum_config_for(project: ProjectTable) -> ForumSettings | None:
    """
    This project's forum settings, or None when the project has no lab.

    None when the forum is disabled, the project has no URL, or this machine has
    no usable git. Every field that names a repository is set here from the
    project, never inherited: a `ForumSettings` read from the environment
    carries no repository at all, so a caller that forgets to scope gets a
    client that refuses rather than one quietly working in some other project's
    forum.
    """
    url = forum_url_of(project)
    if url is None or not settings.forum.enabled or not git_status().ok:
        return None
    return settings.forum.model_copy(
        update={"repo_root": forum_root(project.id), "remote_url": url}
    )


def build_client_for(project: ProjectTable) -> ForumClient:
    """A forum client scoped to this project. Raises when the project has no lab."""
    config = forum_config_for(project)
    if config is None:
        git = git_status()
        why = (
            git.reason
            if forum_url_of(project) and settings.forum.enabled and not git.ok
            else "set a forum repository on the project to turn it on"
        )
        raise ForumDisabled(f"project {project.name!r} has no Hypothesis Lab: {why}")
    return client_factory(config)


def lab_enabled(project: ProjectTable) -> bool:
    """Whether this project has a Hypothesis Lab at all."""
    return forum_config_for(project) is not None


async def ensure_forum(project: ProjectTable) -> None:
    """
    Make this project's forum exist and point at its repository. Idempotent.

    Safe to call on every save and on first use. The `sync` is not decoration:
    git accepts any string as a remote URL, so a typo is not discovered until
    something tries to reach it, and syncing here is what turns a bad URL into
    an error in the dialog the person is looking at. It also pulls whatever
    threads the repository already holds, so pointing a new project at an
    existing forum joins that conversation rather than starting an empty one
    beside it.
    """
    if forum_url_of(project) is None or not settings.forum.enabled:
        return
    git = git_status()
    if not git.ok:
        raise ForumSetupError(f"The Hypothesis Lab needs git. {git.reason}")
    config = forum_config_for(project)
    assert config is not None and config.remote_url is not None

    client = client_factory(config)
    try:
        await client.ensure_repo()
        if await client.remote() != config.remote_url:
            await client.set_remote(config.remote_url)
        result = await client.sync()
    except OSError as exc:
        raise ForumSetupError(f"could not create {config.repo_root}: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 — the reason belongs in the dialog
        raise ForumSetupError(f"could not reach {config.remote_url}: {exc}") from exc
    logger.info(
        "forum: %r is on %s (%d pulled, %d pushed)",
        project.name,
        config.remote_url,
        result.pulled,
        result.pushed,
    )


def check_legacy_forum_env() -> None:
    """
    Disown `VISTA_BACKEND_FORUM__REPO_ROOT` / `__REMOTE_URL`, loudly.

    They used to select one forum for the whole deployment. A stale line in a
    `.env` would otherwise keep resolving to a real directory, so a call site
    that forgot to scope itself to a project would work — against the old shared
    forum, silently, and only on the machine that still had that line. Clearing
    them turns that into an immediate refusal, and the warning says where the
    setting went.
    """
    if settings.forum.repo_root is None and settings.forum.remote_url is None:
        return
    logger.warning(
        "forum: VISTA_BACKEND_FORUM__REPO_ROOT and __REMOTE_URL no longer do "
        "anything — the Hypothesis Lab's repository is set per project now. "
        "Put %s on the project that should use it, and delete these lines.",
        settings.forum.remote_url or "the forum URL",
    )
    settings.forum.repo_root = None
    settings.forum.remote_url = None
