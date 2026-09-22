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

Initialising one is four commands, and they are the same four a human peer runs
to join a forum (docs/hypothesis-forum-hosting.md §5) — which is the point: this
deployment is a participant in that repository, not its owner.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

from ...config import ForumSettings, settings
from ...db.schemas import ProjectTable
from ...services.h5i_forum import ForumClient, ForumDisabled, VotePolicy


logger = logging.getLogger(__name__)


class ForumSetupError(RuntimeError):
    """
    Initialising a project's forum failed, with the reason the tooling gave.

    Raised rather than logged because this runs while someone is saving a
    project and watching a dialog: a URL that does not resolve is a typo to fix
    now, not a warning to find later in a log they are not reading.
    """


def forum_root(project_id: uuid.UUID) -> Path:
    """
    Where this project's forum working repository lives.

    Keyed by id rather than name so renaming a project does not orphan its
    forum — the h5i state lives in this directory's `.git/.h5i/`, and a rename
    would otherwise read as "this project has no lab, initialise a new one".
    """
    return settings.forums_dir / str(project_id)


def forum_url_of(project: ProjectTable) -> str | None:
    """The project's forum URL, or None when it has no lab. Blank counts as none."""
    url = (project.forum_repo_url or "").strip()
    return url or None


def forum_config_for(project: ProjectTable) -> ForumSettings | None:
    """
    This project's forum settings, or None when the project has no lab.

    Every field that names a repository is set here from the project, never
    inherited: a `ForumSettings` read from the environment carries no repository
    at all, so a caller that forgets to scope gets a client that refuses rather
    than one quietly working in some other project's forum.
    """
    url = forum_url_of(project)
    if url is None or not settings.forum.enabled:
        return None
    return settings.forum.model_copy(
        update={"repo_root": forum_root(project.id), "remote_url": url}
    )


def build_client_for(project: ProjectTable) -> ForumClient:
    """A forum client scoped to this project. Raises when the project has no lab."""
    config = forum_config_for(project)
    if config is None:
        raise ForumDisabled(
            f"project {project.name!r} has no Hypothesis Lab: set a forum "
            "repository on the project to turn it on"
        )
    return ForumClient(config)


def lab_enabled(project: ProjectTable) -> bool:
    """Whether this project has a Hypothesis Lab at all."""
    return forum_config_for(project) is not None


async def _git(root: Path, *args: str) -> None:
    """One git command in the forum repo, with its stderr kept for the caller."""
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(root),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        raise ForumSetupError(
            f"git {' '.join(args)} failed: {err.decode(errors='replace').strip()}"
        )


async def ensure_forum(project: ProjectTable) -> None:
    """
    Make this project's forum exist and point at its repository. Idempotent.

    Safe to call on every save and on first use. The git steps are skipped once
    the directory is a repository, and `forum remote` is only issued when it
    would change something — h5i accepts a redundant set, but a log line saying
    the remote moved when it did not is a lie a reader has to disprove.

    The `sync` is not decoration. `forum remote` accepts any string, so a typo
    is not discovered until something tries to reach it; syncing here is what
    turns a bad URL into an error in the dialog the person is looking at. It
    also pulls whatever threads the repository already holds, so pointing a new
    project at an existing forum joins that conversation rather than starting an
    empty one beside it.
    """
    config = forum_config_for(project)
    if config is None:
        return
    root = config.repo_root
    assert root is not None and config.remote_url is not None  # forum_config_for

    try:
        if not (root / ".git").is_dir():
            root.mkdir(parents=True, exist_ok=True)
            await _git(root, "init", "-q")
            # An empty commit, because h5i stores the forum under `.git/.h5i/` of
            # a repository that has a HEAD. A fresh `git init` has none.
            await _git(root, "commit", "-q", "--allow-empty", "-m", "forum")
            logger.info("forum: created a repository for %r at %s", project.name, root)
    except ForumSetupError:
        raise
    except OSError as exc:
        raise ForumSetupError(f"could not create {root}: {exc}") from exc

    client = ForumClient(config)
    try:
        if config.remote_url not in await client.remote():
            await client.set_remote(config.remote_url)
        result = await client.sync()
    except Exception as exc:  # noqa: BLE001 — the reason belongs in the dialog
        raise ForumSetupError(f"could not reach {config.remote_url}: {exc}") from exc
    logger.info(
        "forum: %r is on %s (%d pulled, %d pushed)",
        project.name,
        config.remote_url,
        result.pulled,
        result.pushed,
    )

    await _apply_vote_policy(client)


async def _apply_vote_policy(client: ForumClient) -> None:
    """
    Tighten the vote policy, but only once it would mean something.

    `principal` counts one vote per enrolled forge account and *nothing* from an
    unenrolled machine, so setting it on a forum where nobody has run
    `h5i forum enroll` silently zeroes every vote, our own agents' included.

    Failures here are logged, not raised: the forum is already usable, and a
    policy that could not be set is not a reason to refuse the project.
    """
    if not settings.forum.vote_policy:
        return
    try:
        wanted = VotePolicy(settings.forum.vote_policy)
        if await client.vote_policy() == wanted:
            return
        if wanted is VotePolicy.PRINCIPAL and not await client.enrollments():
            logger.warning(
                "forum: leaving the vote policy alone — `principal` counts "
                "nothing from an unenrolled machine, and nobody has run "
                "`h5i forum enroll` yet, so every vote would be discarded"
            )
            return
        await client.set_vote_policy(wanted)
        logger.info("forum: vote policy is now %s", wanted)
    except Exception:  # noqa: BLE001
        logger.warning("forum: could not set the vote policy", exc_info=True)


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
