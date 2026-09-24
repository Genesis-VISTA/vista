"""
The Hypothesis Lab's forum, kept in plain git.

Replaces `h5i_forum.py` (h5i removed its forum in v0.4.0). The design and the
reasons for it are in openspec/changes/forum-git-backend/design.md; the short
version:

  - Each project's forum is a **bare** repository, `<repo_root>/repo.git`,
    synced with the project's forge remote (named `forum`). The forge is the
    only server.
  - A thread is a branch, `refs/heads/vista-forum/threads/<uuid7>`. Its root
    commit adds `thread.json` (and the framing post); every later commit adds
    one `posts/<post-id>.json` and at most one attachment beside it. Post order
    is the order the remote accepted the commits, never timestamps.
  - Writes use plumbing (`hash-object`, `mktree`, `commit-tree`) and move the
    ref with a compare-and-swap `update-ref`, under a per-thread lock. There is
    no index or checkout, so several debates can post at once.
  - Posting is **local-first**: a post is real once committed locally, and is
    published on the next successful push. Pushes are never forced; a rejected
    push fetches, replays this install's posts on top of the remote tip (post
    paths are unique, so replay never conflicts) and tries again.
  - Whether a post is *ours* is recorded in an `Outbox` at write time and never
    read back from the post file, whose `origin` and `identity` a peer can copy.

Every git call is an argument list; post bodies go into blobs through stdin
and never into an argv or a commit message.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shlex
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..config import ForumSettings, settings

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Format constants
# --------------------------------------------------------------------------- #

FORMAT_VERSION = 1
REMOTE = "forum"
THREAD_PREFIX = "refs/heads/vista-forum/threads/"
TRACKING_PREFIX = "refs/remotes/forum/vista-forum/threads/"
FETCH_REFSPEC = "+refs/heads/vista-forum/*:refs/remotes/forum/vista-forum/*"
HOST_ID_FILE = "host_id"
REPO_DIR = "repo.git"
ATTACHMENTS_DIR = "attachments"

AUTHOR_NAME = "vista-forum"
"""
Every forum commit's author and committer, with `<host-id>@vista-forum.invalid`.

Fixed rather than the user's `git config`: a forum is read by people outside
the project, and the user's identity would publish their email address.
"""

HUMAN_SENDER = "human"
""" The identity of the operator, the person at this install. """

_HOST_ID = re.compile(r"[0-9a-f]{32}")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def is_host_id(value: object) -> bool:
    """Whether `value` is shaped like a host id. Says nothing about whose it is."""
    return isinstance(value, str) and _HOST_ID.fullmatch(value) is not None


def is_thread_id(value: object) -> bool:
    """
    Whether `value` can name a thread here.

    Also the guard on what goes into a ref name. An h5i-era id fails this and so
    reads as a missing thread, which is exactly how those debates should behave.
    """
    return isinstance(value, str) and _UUID.fullmatch(value) is not None


def load_host_id(root: Path) -> str:
    """
    This install's host id, created on first use.

    Stamped as `origin` on every post this install writes, so peers can tell
    installs apart. Random rather than derived from anything about the user —
    `git config user.email` would publish their address and collide across two
    installs of one person — and written once, `0600`, then only ever read.

    It is a label, not a credential: a peer can copy it, which is why whether a
    post is ours is decided by the local outbox and never by this.
    """
    root.mkdir(parents=True, exist_ok=True)
    path = root / HOST_ID_FILE
    if not path.exists():
        new = uuid.uuid4().hex
        tmp = root / f".{HOST_ID_FILE}.{new}"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(new + "\n")
        try:
            # A hard link appears complete or not at all and refuses to replace
            # an existing file, so two processes starting at once agree on
            # whichever id landed first and neither reads a half-written one.
            os.link(tmp, path)
        except FileExistsError:
            pass
        else:
            return new
        finally:
            tmp.unlink()

    value = path.read_text().strip()
    if not is_host_id(value):
        raise RuntimeError(
            f"{path} does not hold a host id ({value[:40]!r}). Delete it and "
            "VISTA will make a new one; this install's posts will then carry "
            "a different origin, which only affects how peers label them."
        )
    return value


# --------------------------------------------------------------------------- #
# Kinds
# --------------------------------------------------------------------------- #


class PostKind(StrEnum):
    """
    Every kind a thread can hold.

    Some are written only by specific verbs — `create_thread` writes TASK,
    `close_thread` writes CLOSED, `vote` writes UPVOTE/DOWNVOTE — so this set is
    wider than what a role may post. See `POSTABLE_KINDS`.
    """

    ASK = "ASK"
    FINDING = "FINDING"
    RISK = "RISK"
    PROPOSAL = "PROPOSAL"
    HANDOFF = "HANDOFF"
    ACK = "ACK"
    BLOCKED = "BLOCKED"
    DONE = "DONE"
    TASK = "TASK"
    CLOSED = "CLOSED"
    UPVOTE = "UPVOTE"
    DOWNVOTE = "DOWNVOTE"


POSTABLE_KINDS: frozenset[PostKind] = frozenset(
    {
        PostKind.ASK,
        PostKind.FINDING,
        PostKind.RISK,
        PostKind.PROPOSAL,
        PostKind.HANDOFF,
        PostKind.ACK,
        PostKind.BLOCKED,
        PostKind.DONE,
    }
)
""" Kinds a role or the operator may post. Checked before anything is written. """

VOTE_KINDS: frozenset[PostKind] = frozenset({PostKind.UPVOTE, PostKind.DOWNVOTE})

_KNOWN_KINDS = frozenset(k.value for k in PostKind)


class VouchLane(StrEnum):
    """
    How much this install actually knows about where a post came from.

    Only OBSERVED is knowledge. Everything else in a post file — identity,
    role, origin — is whoever pushed it describing themselves.
    """

    OBSERVED = "host-observed"
    """This install wrote it; its outbox says so."""

    PEER_CLAIMED = "peer-claimed"
    """Arrived over the remote naming an origin. Every field is that peer's claim."""

    UNATTRIBUTED = "unattributed"
    """Arrived over the remote naming no usable origin."""


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class ForumError(Exception):
    """Base for every forum failure."""


class ForumDisabled(ForumError):
    """The feature is off, or `repo_root` is unset. Raised before any subprocess."""


DETAIL_LIMIT = 400
"""How much of a failed command's output goes in the exception message."""


def _collapse(output: str) -> str:
    """
    Every line of a command's output, on one line, bounded.

    Git puts the cause first and boilerplate after ("ERROR: Repository not
    found" four lines above "and the repository exists."), so neither end is
    dropped. The streams stay on the exception in full.
    """
    joined = " · ".join(line.strip() for line in output.splitlines() if line.strip())
    if len(joined) > DETAIL_LIMIT:
        return joined[: DETAIL_LIMIT - 1].rstrip() + "…"
    return joined or "no output"


class ForumCommandError(ForumError):
    """A git command failed. Carries the argv and both streams for diagnosis."""

    def __init__(
        self, argv: list[str], returncode: int, stdout: str, stderr: str
    ) -> None:
        self.argv = argv
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        super().__init__(
            f"`{shlex.join(argv)}` exited {returncode}: "
            f"{_collapse(stderr.strip() or stdout.strip() or 'no output')}"
        )


class ThreadClosed(ForumError):
    """
    The thread holds a CLOSED post — ours or a peer's — so nothing more may be
    posted. The human's early-stop path, and expected control flow.
    """


class ThreadMissing(ForumError):
    """
    The thread is on neither the remote nor this install, or the remote no
    longer has it. Stored copies stay readable; posting to it is refused.
    """


class InvalidKind(ForumError, ValueError):
    """A kind that may not be posted this way."""


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #


class Post(BaseModel):
    """
    One post, as read from its file.

    Only `body` is the agent's words. `sender` (the post file's `identity`),
    `role` and `origin` are also written by the poster, so on anything but a
    host-observed post they are that poster's claim — see `Thread.lane`.
    """

    id: str
    thread: str
    kind: str
    body: str
    sender: str
    role: str
    ts: str
    origin: str | None = None
    reply_to: str | None = None
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    denied: str | None = None
    """ Kept for the projection's shape. Nothing in the git format records a refusal. """

    @property
    def is_vote(self) -> bool:
        return self.kind in VOTE_KINDS

    @property
    def claims_human(self) -> bool:
        """
        The post says it is from a person. **Not** proof it is your operator:
        every install's operator posts as `human`. Use `Thread.is_operator`.
        """
        return self.sender == HUMAN_SENDER

    @property
    def looks_agentic(self) -> bool:
        """Whether this presents as an agent's post. A label; on a peer post, their claim."""
        return not self.claims_human

    @property
    def agent_authored(self) -> bool:
        """False for the framing and closing posts, which nobody argued."""
        return self.kind not in (PostKind.TASK, PostKind.CLOSED)


class ThreadHeader(BaseModel):
    id: str
    title: str
    created_at: str
    created_by: str
    version: int = FORMAT_VERSION


class Thread(BaseModel):
    """
    A thread and its posts, with the provenance lanes kept beside them.

    `vouch` maps post id to lane rather than being a field on `Post`, so the
    lane can never be mistaken for something the post file said.

    `posts` stops at the first CLOSED post. Anything committed after it stays
    in git history and is not part of the thread.
    """

    header: ThreadHeader
    status: str
    posts: list[Post] = Field(default_factory=list)
    vouch: dict[str, str] = Field(default_factory=dict)

    @property
    def id(self) -> str:
        return self.header.id

    @property
    def is_closed(self) -> bool:
        return self.status == "closed"

    def lane(self, post_id: str) -> str | None:
        return self.vouch.get(post_id)

    def is_observed(self, post: Post) -> bool:
        """Did *this* install write this post? Everything else is a claim."""
        return self.lane(post.id) == VouchLane.OBSERVED

    def is_operator(self, post: Post) -> bool:
        """The human at this install: claims `human` *and* this install wrote it."""
        return post.claims_human and self.is_observed(post)

    def is_peer(self, post: Post) -> bool:
        return not self.is_observed(post)

    def content_posts(self) -> list[Post]:
        """Posts a reader would read — votes folded away."""
        return [p for p in self.posts if not p.is_vote]

    def tally_split(self, post_id: str) -> tuple[int, int]:
        """
        Net votes on a post as `(observed, peer)`.

        One vote per voter, the voter's latest winning, so repeating a vote or
        changing one never counts twice. A voter is `(origin, identity)`, kept
        apart per lane: a peer copying our origin and identity is a different
        voter from us, not a way to overwrite our vote.
        """
        latest: dict[tuple[bool, str | None, str], int] = {}
        for post in self.posts:
            if post.reply_to != post_id or post.kind not in VOTE_KINDS:
                continue
            voter = (self.is_observed(post), post.origin, post.sender)
            latest[voter] = 1 if post.kind == PostKind.UPVOTE else -1
        observed = sum(v for (seen, _, _), v in latest.items() if seen)
        peer = sum(v for (seen, _, _), v in latest.items() if not seen)
        return observed, peer

    def tally(self, post_id: str) -> int:
        """Net votes on a post, both lanes together."""
        return sum(self.tally_split(post_id))


class ThreadSummary(BaseModel):
    """A thread's header and a few counts, without its posts."""

    header: ThreadHeader
    status: str
    posts: int = 0
    last_activity: str | None = None
    denials: int = 0

    @property
    def id(self) -> str:
        return self.header.id


class SyncResult(BaseModel):
    """What one exchange with the remote moved, counted in posts."""

    pulled: int = 0
    pushed: int = 0


class Participant(BaseModel):
    """A role on a debate: the identity it posts under, and its role name."""

    identity: str
    """E.g. `vista-proposer-1a2b3c4d`, or `human`. Written on each post."""

    role: str
    """The role's name as shown with its posts: proposer, reviewer, referee, human."""


HUMAN = Participant(identity=HUMAN_SENDER, role=HUMAN_SENDER)


# --------------------------------------------------------------------------- #
# Outbox: which posts are ours, and which the remote has
# --------------------------------------------------------------------------- #


class Outbox(Protocol):
    """
    This install's record of the posts it wrote.

    It is the only source of the `host-observed` lane, and the publish queue: a
    post with no `published_at` is replayed onto the remote on the next push.
    """

    async def record(self, post_id: str, thread_id: str, created_at: str) -> None: ...

    async def ours(self, post_ids: Iterable[str]) -> set[str]: ...

    async def published(self, post_ids: Iterable[str]) -> set[str]: ...

    async def mark_published(self, post_ids: Iterable[str], at: str) -> None: ...

    async def mark_unpublished(self, post_ids: Iterable[str]) -> None: ...

    async def forget(self, post_ids: Iterable[str]) -> None: ...

    async def unpublished_count(self, thread_id: str | None = None) -> int: ...


@dataclass
class MemoryOutbox:
    """An in-process outbox, for tests and the fake client."""

    rows: dict[str, dict[str, str | None]] = field(default_factory=dict)

    async def record(self, post_id: str, thread_id: str, created_at: str) -> None:
        self.rows[post_id] = {
            "thread_id": thread_id,
            "created_at": created_at,
            "published_at": None,
        }

    async def ours(self, post_ids: Iterable[str]) -> set[str]:
        return {p for p in post_ids if p in self.rows}

    async def published(self, post_ids: Iterable[str]) -> set[str]:
        return {p for p in post_ids if p in self.rows and self.rows[p]["published_at"]}

    async def mark_published(self, post_ids: Iterable[str], at: str) -> None:
        for p in post_ids:
            if p in self.rows and not self.rows[p]["published_at"]:
                self.rows[p]["published_at"] = at

    async def mark_unpublished(self, post_ids: Iterable[str]) -> None:
        for p in post_ids:
            if p in self.rows:
                self.rows[p]["published_at"] = None

    async def forget(self, post_ids: Iterable[str]) -> None:
        for p in post_ids:
            self.rows.pop(p, None)

    async def unpublished_count(self, thread_id: str | None = None) -> int:
        return sum(
            1
            for r in self.rows.values()
            if r["published_at"] is None
            and (thread_id is None or r["thread_id"] == thread_id)
        )


class DbOutbox:
    """The outbox in the `forum_outbox` table, scoped to one project."""

    def __init__(self, engine: AsyncEngine, project_id: uuid.UUID) -> None:
        self.engine = engine
        self.project_id = project_id

    async def record(self, post_id: str, thread_id: str, created_at: str) -> None:
        from ..db.schemas import ForumOutboxTable

        async with AsyncSession(self.engine) as session:
            session.add(
                ForumOutboxTable(
                    post_id=post_id,
                    project_id=self.project_id,
                    thread_id=thread_id,
                    created_at=created_at,
                )
            )
            await session.commit()

    async def _select(self, post_ids: Iterable[str], *, published: bool) -> set[str]:
        from ..db.schemas import ForumOutboxTable as T

        ids = list(post_ids)
        if not ids:
            return set()
        query = select(T.post_id).where(
            T.project_id == self.project_id, col(T.post_id).in_(ids)
        )
        if published:
            query = query.where(col(T.published_at).is_not(None))
        async with AsyncSession(self.engine) as session:
            return set((await session.exec(query)).all())

    async def ours(self, post_ids: Iterable[str]) -> set[str]:
        return await self._select(post_ids, published=False)

    async def published(self, post_ids: Iterable[str]) -> set[str]:
        return await self._select(post_ids, published=True)

    async def _set_published(self, post_ids: Iterable[str], at: str | None) -> None:
        from ..db.schemas import ForumOutboxTable as T

        ids = list(post_ids)
        if not ids:
            return
        async with AsyncSession(self.engine) as session:
            query = select(T).where(
                T.project_id == self.project_id, col(T.post_id).in_(ids)
            )
            for row in (await session.exec(query)).all():
                if at is None or row.published_at is None:
                    row.published_at = at
                    session.add(row)
            await session.commit()

    async def mark_published(self, post_ids: Iterable[str], at: str) -> None:
        await self._set_published(post_ids, at)

    async def mark_unpublished(self, post_ids: Iterable[str]) -> None:
        await self._set_published(post_ids, None)

    async def forget(self, post_ids: Iterable[str]) -> None:
        from ..db.schemas import ForumOutboxTable as T

        ids = list(post_ids)
        if not ids:
            return
        async with AsyncSession(self.engine) as session:
            query = select(T).where(
                T.project_id == self.project_id, col(T.post_id).in_(ids)
            )
            for row in (await session.exec(query)).all():
                await session.delete(row)
            await session.commit()

    async def unpublished_count(self, thread_id: str | None = None) -> int:
        from ..db.schemas import ForumOutboxTable as T

        query = select(T.post_id).where(
            T.project_id == self.project_id, col(T.published_at).is_(None)
        )
        if thread_id is not None:
            query = query.where(T.thread_id == thread_id)
        async with AsyncSession(self.engine) as session:
            return len((await session.exec(query)).all())


# --------------------------------------------------------------------------- #
# Locks
# --------------------------------------------------------------------------- #

# Module-level rather than per client: `build_client_for` makes a new client per
# request, and two clients on one repository must still take turns.
_thread_locks: dict[tuple[str, str], asyncio.Lock] = {}
_fetch_locks: dict[str, asyncio.Lock] = {}


def _thread_lock(repo: Path, thread: str) -> asyncio.Lock:
    return _thread_locks.setdefault((str(repo), thread), asyncio.Lock())


def _fetch_lock(repo: Path) -> asyncio.Lock:
    return _fetch_locks.setdefault(str(repo), asyncio.Lock())


# --------------------------------------------------------------------------- #
# Reading a thread's history
# --------------------------------------------------------------------------- #


@dataclass
class _Commit:
    sha: str
    subject: str
    added: list[str]


@dataclass
class _History:
    """A thread ref, parsed: its header, every valid post, and where each came from."""

    tip: str
    header: ThreadHeader | None
    posts: list[Post]
    """ In commit order, not yet cut at CLOSED. """
    commit_of: dict[str, str]
    """ post id → the commit that added it. """

    @property
    def ids(self) -> set[str]:
        return {p.id for p in self.posts}

    def visible(self) -> list[Post]:
        """The posts a reader sees: up to and including the first CLOSED."""
        for i, post in enumerate(self.posts):
            if post.kind == PostKind.CLOSED:
                return self.posts[: i + 1]
        return list(self.posts)

    @property
    def closed(self) -> bool:
        return any(p.kind == PostKind.CLOSED for p in self.posts)


_POST_PATH = re.compile(r"posts/([^/]+)\.json")

_PUSH_RACES = (
    "[rejected]",
    "fetch first",
    "non-fast-forward",
    # Two pushes arriving together: the check passed for both and the loser
    # finds the ref moved when it takes the lock. Found by the two-host test.
    "cannot lock ref",
    "failed to update ref",
)
"""Push failures that mean a peer got there first, so fetch, replay and retry."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _parse_post(raw: bytes, *, thread: str, stem: str, where: str) -> Post | None:
    """A post file, validated, or None (logged) when it is not one we can read."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError, UnicodeDecodeError:
        logger.warning("forum: skipping %s: not JSON", where)
        return None
    if not isinstance(data, dict):
        logger.warning("forum: skipping %s: not an object", where)
        return None
    if data.get("v") != FORMAT_VERSION:
        logger.warning(
            "forum: skipping %s: unknown format version %r", where, data.get("v")
        )
        return None
    if data.get("kind") not in _KNOWN_KINDS:
        logger.warning("forum: skipping %s: unknown kind %r", where, data.get("kind"))
        return None
    if data.get("id") != stem or data.get("thread") != thread:
        logger.warning(
            "forum: skipping %s: id or thread does not match its path", where
        )
        return None
    for key in ("body", "identity", "ts"):
        if not isinstance(data.get(key), str):
            logger.warning("forum: skipping %s: %s is not a string", where, key)
            return None
    origin = data.get("origin")
    reply_to = data.get("reply_to")
    attachments = data.get("attachments") or []
    return Post(
        id=stem,
        thread=thread,
        kind=data["kind"],
        body=data["body"],
        sender=data["identity"],
        role=data["role"] if isinstance(data.get("role"), str) else "",
        ts=data["ts"],
        origin=origin if isinstance(origin, str) else None,
        reply_to=reply_to if isinstance(reply_to, str) else None,
        attachments=[a for a in attachments if isinstance(a, dict)]
        if isinstance(attachments, list)
        else [],
    )


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #


@dataclass
class StagedAttachment:
    name: str
    content: str
    kind: str = "text"


class ForumClient:
    """
    One project's forum. `config.repo_root` is that project's directory under
    `settings.forum_git_dir`; the host id lives one level up, shared by every
    project on this install.
    """

    def __init__(
        self,
        config: ForumSettings | None = None,
        *,
        outbox: Outbox | None = None,
        host_id: str | None = None,
    ) -> None:
        self.config = config or settings.forum
        self._outbox = outbox
        self._host_id = host_id
        self._staged: dict[str, StagedAttachment] = {}

    # -- paths and identity ------------------------------------------------ #

    @property
    def repo_root(self) -> Path:
        if not self.config.enabled:
            raise ForumDisabled(
                "the agent forum is disabled; set VISTA_BACKEND_FORUM__ENABLED=true"
            )
        if self.config.repo_root is None:
            raise ForumDisabled("this forum client has no project repository")
        return self.config.repo_root

    @property
    def repo(self) -> Path:
        return self.repo_root / REPO_DIR

    @property
    def host_id(self) -> str:
        if self._host_id is None:
            self._host_id = load_host_id(self.repo_root.parent)
        return self._host_id

    @property
    def outbox(self) -> Outbox:
        if self._outbox is None:
            from ..db.db import get_engine

            self._outbox = DbOutbox(get_engine(), uuid.UUID(self.repo_root.name))
        return self._outbox

    # -- the git runner ---------------------------------------------------- #

    def _env(self, *, network: bool) -> dict[str, str]:
        env = {
            **os.environ,
            "LC_ALL": "C",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_AUTHOR_NAME": AUTHOR_NAME,
            "GIT_AUTHOR_EMAIL": f"{self.host_id}@vista-forum.invalid",
            "GIT_COMMITTER_NAME": AUTHOR_NAME,
            "GIT_COMMITTER_EMAIL": f"{self.host_id}@vista-forum.invalid",
        }
        for leak in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_NAMESPACE"):
            env.pop(leak, None)
        if not network:
            # Local plumbing ignores the user's git config entirely, so nothing
            # like `commit.gpgSign`, `log.showSignature` or a hooks path can
            # change what it writes or how its output parses. Only fetch and
            # push read it, because that is where their credentials live.
            env["GIT_CONFIG_GLOBAL"] = os.devnull
            env["GIT_CONFIG_NOSYSTEM"] = "1"
        return env

    async def _git(
        self,
        *args: str,
        input: bytes | None = None,
        check: bool = True,
        network: bool = False,
        cwd: Path | None = None,
    ) -> tuple[int, bytes, str]:
        """Run one git command in the bare repo; returns (code, stdout bytes, stderr)."""
        argv = [self.config.git_binary]
        if network:
            # The user's hooks are theirs, not the forum's: a pre-push hook
            # written for their code has no business running on a debate post.
            argv += ["-c", f"core.hooksPath={self.repo / 'no-hooks'}"]
        argv += list(args)
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd or self.repo),
            env=self._env(network=network),
            stdin=asyncio.subprocess.PIPE
            if input is not None
            else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, raw_err = await asyncio.wait_for(
                proc.communicate(input), timeout=self.config.timeout
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise ForumCommandError(argv, -1, "", "timed out") from None
        err = raw_err.decode(errors="replace")
        code = proc.returncode or 0
        if check and code != 0:
            raise ForumCommandError(argv, code, out.decode(errors="replace"), err)
        return code, out, err

    async def _text(self, *args: str, **kw: Any) -> str:
        _, out, _ = await self._git(*args, **kw)
        return out.decode().strip()

    async def _rev(self, ref: str) -> str | None:
        code, out, _ = await self._git(
            "rev-parse", "-q", "--verify", f"{ref}^{{commit}}", check=False
        )
        return out.decode().strip() if code == 0 else None

    async def _blob(self, content: bytes) -> str:
        return await self._text("hash-object", "-w", "--stdin", input=content)

    async def _mktree(self, entries: Sequence[tuple[str, str, str, str]]) -> str:
        """`entries` are (mode, type, sha, name)."""
        payload = b"".join(
            f"{mode} {kind} {sha}\t{name}".encode() + b"\0"
            for mode, kind, sha, name in entries
        )
        return await self._text("mktree", "-z", input=payload)

    async def _ls_tree(self, treeish: str) -> list[tuple[str, str, str, str]]:
        code, out, _ = await self._git("ls-tree", "-z", treeish, check=False)
        if code != 0:
            return []
        entries = []
        for record in out.split(b"\0"):
            if not record:
                continue
            meta, name = record.split(b"\t", 1)
            mode, kind, sha = meta.decode().split(" ")
            entries.append((mode, kind, sha, name.decode()))
        return entries

    async def _commit(self, tree: str, parent: str | None, subject: str) -> str:
        args = ["commit-tree", tree, "-m", subject]
        if parent is not None:
            args += ["-p", parent]
        return await self._text(*args)

    async def _cas(self, ref: str, new: str, old: str | None) -> None:
        # The zero id as "old" means "must not exist yet".
        await self._git("update-ref", ref, new, old or "0" * 40)

    # -- repository and remote --------------------------------------------- #

    async def ensure_repo(self) -> None:
        """Create the bare repository if it is not there. Idempotent."""
        if (self.repo / "HEAD").exists():
            return
        self.repo_root.mkdir(parents=True, exist_ok=True)
        await self._git("init", "-q", "--bare", REPO_DIR, cwd=self.repo_root)
        # Reflogs make a peer's force-push recoverable locally, which costs
        # nothing and is not on by default in a bare repository.
        await self._git("config", "core.logAllRefUpdates", "always")

    async def remote(self) -> str | None:
        """The URL this forum publishes to, or None."""
        await self.ensure_repo()
        code, out, _ = await self._git(
            "config", "--get", f"remote.{REMOTE}.url", check=False
        )
        if code != 0:
            return None
        return out.decode().strip() or None

    async def set_remote(self, url: str) -> None:
        """Point this forum at `url`. Checked for reachability by the next `sync`."""
        await self.ensure_repo()
        await self._git("config", f"remote.{REMOTE}.url", url)
        await self._git(
            "config", "--replace-all", f"remote.{REMOTE}.fetch", FETCH_REFSPEC
        )

    async def _fetch(self) -> None:
        """Fetch every thread from the remote. Raises when it cannot be reached."""
        async with _fetch_lock(self.repo):
            await self._git(
                "fetch",
                "--prune",
                "--no-tags",
                "--quiet",
                REMOTE,
                FETCH_REFSPEC,
                network=True,
            )

    async def _try_fetch(self) -> bool:
        """Fetch if there is a remote; False (logged) when it cannot be reached."""
        if await self.remote() is None:
            return False
        try:
            await self._fetch()
            return True
        except ForumCommandError as exc:
            logger.info("forum: fetch failed, reading local state: %s", exc)
            return False

    # -- history ----------------------------------------------------------- #

    async def _log(self, ref: str) -> list[_Commit]:
        _, out, _ = await self._git(
            "-c",
            "core.quotePath=false",
            "log",
            "--first-parent",
            "--reverse",
            "--diff-merges=first-parent",
            "--root",
            "--diff-filter=A",
            "--name-only",
            "--no-renames",
            "--no-color",
            "--format=%x1e%H%x1f%s",
            ref,
        )
        commits = []
        for chunk in out.decode(errors="replace").split("\x1e"):
            if not chunk.strip():
                continue
            head, _, rest = chunk.partition("\n")
            sha, _, subject = head.partition("\x1f")
            commits.append(
                _Commit(sha, subject, [p for p in rest.splitlines() if p.strip()])
            )
        return commits

    async def _cat(self, specs: list[str]) -> list[bytes | None]:
        """Blob contents for `<rev>:<path>` specs, in order; None where missing."""
        if not specs:
            return []
        _, out, _ = await self._git(
            "cat-file", "--batch", input="".join(s + "\n" for s in specs).encode()
        )
        results: list[bytes | None] = []
        pos = 0
        for _ in specs:
            nl = out.index(b"\n", pos)
            header = out[pos:nl].decode(errors="replace").split(" ")
            pos = nl + 1
            if header[-1] in ("missing", "ambiguous"):
                results.append(None)
                continue
            if header[1] != "blob":  # a tree or commit: skip its body
                results.append(None)
                pos += int(header[2]) + 1
                continue
            size = int(header[2])
            results.append(out[pos : pos + size])
            pos += size + 1
        return results

    async def _history(self, thread: str, ref: str) -> _History | None:
        tip = await self._rev(ref)
        if tip is None:
            return None
        commits = await self._log(ref)
        wanted: list[tuple[str, str, str]] = []  # (commit, path, stem)
        for c in commits:
            for path in c.added:
                m = _POST_PATH.fullmatch(path)
                if m:
                    wanted.append((c.sha, path, m[1]))
        blobs = await self._cat(
            [f"{tip}:thread.json"] + [f"{sha}:{path}" for sha, path, _ in wanted]
        )
        header = None
        try:
            data = json.loads(blobs[0] or b"")
            if data.get("id") == thread:
                header = ThreadHeader(
                    id=thread,
                    title=str(data.get("title", "")),
                    created_at=str(data.get("created_at", "")),
                    created_by=str(data.get("created_by", "")),
                    version=int(data.get("v", FORMAT_VERSION)),
                )
            else:
                logger.warning(
                    "forum: thread %s: thread.json names another thread", thread
                )
        except json.JSONDecodeError, UnicodeDecodeError, AttributeError, ValueError:
            logger.warning("forum: thread %s has no readable thread.json", thread)

        posts: list[Post] = []
        commit_of: dict[str, str] = {}
        for (sha, path, stem), raw in zip(wanted, blobs[1:]):
            if raw is None or stem in commit_of:
                continue
            post = _parse_post(
                raw, thread=thread, stem=stem, where=f"{thread[:8]}:{path}@{sha[:8]}"
            )
            if post is not None:
                posts.append(post)
                commit_of[stem] = sha
        return _History(tip, header, posts, commit_of)

    # -- integrating the remote ------------------------------------------- #

    async def _integrate(self, thread: str, *, fetched: bool) -> _History:
        """
        Bring the local thread ref up to date with the remote-tracking one.

        Call with the thread lock held. Never pushes. Raises `ThreadMissing`
        when the thread exists nowhere, or when a successful fetch shows the
        remote no longer has a thread that was published.
        """
        local_ref = THREAD_PREFIX + thread
        local = await self._history(thread, local_ref)
        remote = await self._history(thread, TRACKING_PREFIX + thread)

        if remote is None:
            if local is None:
                raise ThreadMissing(f"thread {thread} is not on this forum")
            if fetched and await self._was_published(local):
                raise ThreadMissing(f"thread {thread} is no longer on the forum remote")
            return local  # not published yet, or the remote cannot be reached

        if local is None:
            await self._cas(local_ref, remote.tip, None)
            return remote
        if local.tip == remote.tip:
            return local
        if await self._is_ancestor(local.tip, remote.tip):
            await self._cas(local_ref, remote.tip, local.tip)
            return remote

        ours = await self.outbox.ours(local.ids)
        extra = local.ids - remote.ids
        if await self._is_ancestor(remote.tip, local.tip) and extra <= ours:
            # Only our posts on top, which the remote does not have — whether
            # never pushed or dropped by a rewrite. Push as is.
            await self.outbox.mark_unpublished(extra)
            return local

        # Diverged, or the remote dropped something. Rebuild our posts that
        # the remote lacks on top of its tip; peer posts it dropped are gone
        # from the thread (the projection keeps its copy).
        replay = [p.id for p in local.posts if p.id in extra and p.id in ours]
        await self.outbox.mark_unpublished(replay)
        new_tip = remote.tip
        for post_id in replay:
            new_tip = await self._replay_one(new_tip, local.commit_of[post_id], post_id)
        await self._cas(local_ref, new_tip, local.tip)
        dropped = extra - ours
        if dropped:
            logger.info(
                "forum: thread %s: %d peer posts no longer on the remote",
                thread,
                len(dropped),
            )
        return await self._history(thread, local_ref)  # type: ignore[return-value]

    async def _was_published(self, local: _History) -> bool:
        ids = local.ids
        if not ids:
            return False
        ours = await self.outbox.ours(ids)
        return bool(ids - ours) or bool(await self.outbox.published(ids))

    async def _is_ancestor(self, a: str, b: str) -> bool:
        code, _, _ = await self._git("merge-base", "--is-ancestor", a, b, check=False)
        return code == 0

    async def _replay_one(self, parent: str, source: str, post_id: str) -> str:
        """Re-create the commit that added `post_id` on top of `parent`."""
        wanted = {f"{post_id}.json", post_id}
        added = [e for e in await self._ls_tree(f"{source}:posts") if e[3] in wanted]
        subject = await self._text("show", "-s", "--format=%s", source)
        tree = await self._add_to_tree(parent, added)
        return await self._commit(tree, parent, subject)

    async def _add_to_tree(
        self,
        parent: str | None,
        posts_entries: Sequence[tuple[str, str, str, str]],
        root_entries: Sequence[tuple[str, str, str, str]] = (),
    ) -> str:
        root = await self._ls_tree(parent) if parent else []
        posts_tree = next(
            (e[2] for e in root if e[3] == "posts" and e[1] == "tree"), None
        )
        existing = await self._ls_tree(posts_tree) if posts_tree else []
        names = {e[3] for e in posts_entries}
        new_posts = await self._mktree(
            [e for e in existing if e[3] not in names] + list(posts_entries)
        )
        replaced = {"posts"} | {e[3] for e in root_entries}
        return await self._mktree(
            [e for e in root if e[3] not in replaced]
            + list(root_entries)
            + [("040000", "tree", new_posts, "posts")]
        )

    # -- publishing -------------------------------------------------------- #

    async def _push(self, thread: str) -> tuple[bool, str]:
        """Push one thread, never forced. (accepted, why-not)."""
        ref = THREAD_PREFIX + thread
        code, out, err = await self._git(
            "push",
            "--porcelain",
            "--quiet",
            REMOTE,
            f"{ref}:{ref}",
            network=True,
            check=False,
        )
        if code == 0:
            return True, ""
        text = out.decode(errors="replace") + err
        if any(race in text for race in _PUSH_RACES):
            return False, "rejected"
        raise ForumCommandError(
            [self.config.git_binary, "push", REMOTE, f"{ref}:{ref}"],
            code,
            out.decode(errors="replace"),
            err,
        )

    async def _publish_locked(self, thread: str, *, fetched: bool = False) -> int:
        """
        Fetch, replay and push one thread until the remote takes it. Thread lock
        held. Returns how many of our posts became published. Raises when the
        remote cannot be reached or refuses for a reason other than a race.
        `fetched` skips the first fetch when the caller has just done one.
        """
        for attempt in range(max(1, self.config.push_retries)):
            if not (fetched and attempt == 0):
                await self._fetch()
            local = await self._integrate(thread, fetched=True)
            remote_tip = await self._rev(TRACKING_PREFIX + thread)
            if remote_tip != local.tip:
                accepted, _ = await self._push(thread)
                if not accepted:
                    logger.info(
                        "forum: thread %s: push raced a peer (attempt %d)",
                        thread,
                        attempt + 1,
                    )
                    continue
                # Keep the tracking ref in step even if the refspec is not configured.
                await self._git("update-ref", TRACKING_PREFIX + thread, local.tip)
            ours = await self.outbox.ours(local.ids)
            pending = ours - await self.outbox.published(ours)
            await self.outbox.mark_published(pending, _now())
            return len(pending)
        raise ForumError(
            f"thread {thread}: the remote kept moving; gave up after "
            f"{self.config.push_retries} attempts (posts stay local and publish later)"
        )

    async def _publish_quietly(self, thread: str) -> None:
        """Best effort after a post: a failure leaves the post unpublished, never raises."""
        if await self.remote() is None:
            return
        try:
            await self._publish_locked(thread)
        except (ForumCommandError, ForumError) as exc:
            logger.info("forum: thread %s stays unpublished for now: %s", thread, exc)

    async def publish(self, thread: str) -> int:
        """Publish one thread's unpublished posts now. Raises if the remote refuses."""
        self._check_thread_id(thread)
        async with _thread_lock(self.repo, thread):
            return await self._publish_locked(thread)

    async def _thread_ids(self) -> list[str]:
        _, out, _ = await self._git(
            "for-each-ref", "--format=%(refname)", THREAD_PREFIX, TRACKING_PREFIX
        )
        ids = []
        for ref in out.decode().split():
            tid = ref.rsplit("/", 1)[-1]
            if is_thread_id(tid) and tid not in ids:
                ids.append(tid)
        return ids

    async def sync(self) -> SyncResult:
        """
        Exchange with the remote now: pull every thread, publish every post
        still waiting. Raises when the remote cannot be reached, which is what
        makes saving a project with a mistyped URL fail.
        """
        await self.ensure_repo()
        if await self.remote() is None:
            raise ForumDisabled("this forum has no remote")
        await self._fetch()
        result = SyncResult()
        for thread in await self._thread_ids():
            async with _thread_lock(self.repo, thread):
                before = await self._history(thread, THREAD_PREFIX + thread)
                try:
                    after = await self._integrate(thread, fetched=True)
                except ThreadMissing:
                    continue
                result.pulled += len(after.ids - (before.ids if before else set()))
                if await self.outbox.unpublished_count(thread):
                    result.pushed += await self._publish_locked(thread)
        return result

    # -- threads ----------------------------------------------------------- #

    def _check_thread_id(self, thread: str) -> None:
        if not is_thread_id(thread):
            raise ThreadMissing(f"{thread!r} is not a thread on this forum")

    async def create_thread(self, title: str, *, body: str | None = None) -> str:
        """
        Open a thread and return its id. `body`, if given, becomes its first
        post (TASK), so the framing is numbered and votable like any other.
        """
        await self.ensure_repo()
        thread = str(uuid.uuid7())
        header = {
            "v": FORMAT_VERSION,
            "id": thread,
            "title": title,
            "created_at": _now(),
            "created_by": self.host_id,
        }
        async with _thread_lock(self.repo, thread):
            header_blob = await self._blob(_dump(header))
            posts: list[tuple[str, str, str, str]] = []
            post_id = None
            if body is not None:
                post_id, doc = self._post_doc(
                    thread, HUMAN, PostKind.TASK, body, None, []
                )
                posts.append(
                    ("100644", "blob", await self._blob(_dump(doc)), f"{post_id}.json")
                )
                await self.outbox.record(post_id, thread, doc["ts"])
            header_entry = ("100644", "blob", header_blob, "thread.json")
            if posts:
                tree = await self._add_to_tree(None, posts, [header_entry])
            else:
                tree = await self._mktree([header_entry])
            sha = await self._commit(tree, None, f"THREAD {thread}")
            await self._cas(THREAD_PREFIX + thread, sha, None)
            await self._publish_quietly(thread)
        return thread

    async def list_threads(
        self, *, include_closed: bool = False
    ) -> list[ThreadSummary]:
        """Every thread on this forum, newest first."""
        await self.ensure_repo()
        fetched = await self._try_fetch()
        rows = []
        for thread in await self._thread_ids():
            async with _thread_lock(self.repo, thread):
                try:
                    history = await self._integrate(thread, fetched=fetched)
                except ThreadMissing:
                    continue
            if history.header is None:
                continue
            visible = history.visible()
            status = "closed" if history.closed else "open"
            if status == "closed" and not include_closed:
                continue
            rows.append(
                ThreadSummary(
                    header=history.header,
                    status=status,
                    posts=len(visible),
                    last_activity=visible[-1].ts
                    if visible
                    else history.header.created_at,
                )
            )
        return sorted(rows, key=lambda r: r.header.created_at, reverse=True)

    async def read_thread(self, thread: str) -> Thread:
        """
        Read a thread, fetching first. A failed fetch reads what is here.
        Raises `ThreadMissing` when the thread is gone.
        """
        self._check_thread_id(thread)
        await self.ensure_repo()
        fetched = await self._try_fetch()
        async with _thread_lock(self.repo, thread):
            history = await self._integrate(thread, fetched=fetched)
        return await self._to_thread(history, thread)

    async def _to_thread(self, history: _History, thread: str) -> Thread:
        if history.header is None:
            raise ForumError(f"thread {thread} has no readable thread.json")
        visible = history.visible()
        ours = await self.outbox.ours(p.id for p in visible)
        vouch = {}
        for post in visible:
            if post.id in ours:
                vouch[post.id] = VouchLane.OBSERVED.value
            elif is_host_id(post.origin):
                vouch[post.id] = VouchLane.PEER_CLAIMED.value
            else:
                vouch[post.id] = VouchLane.UNATTRIBUTED.value
        return Thread(
            header=history.header,
            status="closed" if history.closed else "open",
            posts=visible,
            vouch=vouch,
        )

    async def close_thread(self, thread: str) -> None:
        """End a thread: post CLOSED as the operator. A no-op if it is already closed."""
        try:
            await self._post(
                thread, HUMAN, PostKind.CLOSED, "", reply_to=None, staged=None
            )
        except ThreadClosed:
            pass

    # -- posting ----------------------------------------------------------- #

    def _post_doc(
        self,
        thread: str,
        who: Participant,
        kind: PostKind,
        body: str,
        reply_to: str | None,
        attachments: list[dict[str, Any]],
        post_id: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        post_id = post_id or str(uuid.uuid7())
        return post_id, {
            "v": FORMAT_VERSION,
            "id": post_id,
            "thread": thread,
            "kind": str(kind),
            "body": body,
            "identity": who.identity,
            "role": who.role,
            "origin": self.host_id,
            "ts": _now(),
            "reply_to": reply_to,
            "attachments": attachments,
        }

    async def stage_attachment(
        self, participant: Participant, name: str, content: str, *, kind: str = "text"
    ) -> str:
        """
        Hold an attachment for this client's next `post_as`, which commits it
        beside the post. Returns the handle to pass as `attachment=`.
        """
        safe = Path(name).name
        if not safe or safe.startswith(".") or any(c in safe for c in "\0\n\r\t"):
            raise ValueError(f"unusable attachment name: {name!r}")
        handle = f"{uuid.uuid4().hex}/{safe}"
        self._staged[handle] = StagedAttachment(safe, content, kind)
        return handle

    async def post_as_human(
        self, thread: str, body: str, *, kind: PostKind = PostKind.ASK
    ) -> Post:
        """The operator joining their own debate."""
        _validate_kind(kind)
        return await self._post(thread, HUMAN, kind, body, reply_to=None, staged=None)

    async def post_as(
        self,
        participant: Participant,
        thread: str,
        body: str,
        *,
        kind: PostKind,
        reply_to: str | None = None,
        attachment: str | None = None,
        attachment_kind: str | None = None,
    ) -> Post:
        """
        Post as a role. Returns once the post is committed locally; publishing
        is attempted straight after and its failure never fails the post.
        """
        _validate_kind(kind)
        staged = None
        if attachment is not None:
            staged = self._staged.pop(attachment, None)
            if staged is None:
                raise ForumError(f"no staged attachment {attachment!r} on this client")
            if attachment_kind:
                staged.kind = attachment_kind
        return await self._post(
            thread, participant, kind, body, reply_to=reply_to, staged=staged
        )

    async def vote(
        self, participant: Participant, thread: str, post_id: str, *, up: bool = True
    ) -> None:
        """Agree or disagree with a post. Voting again replaces the earlier vote."""
        kind = PostKind.UPVOTE if up else PostKind.DOWNVOTE
        await self._post(
            thread, participant, kind, "", reply_to=post_id, staged=None, target=post_id
        )

    async def _post(
        self,
        thread: str,
        who: Participant,
        kind: PostKind,
        body: str,
        *,
        reply_to: str | None,
        staged: StagedAttachment | None,
        target: str | None = None,
    ) -> Post:
        self._check_thread_id(thread)
        await self.ensure_repo()
        fetched = await self._try_fetch()
        async with _thread_lock(self.repo, thread):
            history = await self._integrate(thread, fetched=fetched)
            if history.closed:
                raise ThreadClosed(f"thread {thread} is closed")
            if target is not None and target not in {p.id for p in history.visible()}:
                raise ForumError(f"post {target} is not in thread {thread}")

            post_id = str(uuid.uuid7())
            entries: list[tuple[str, str, str, str]] = []
            meta: list[dict[str, Any]] = []
            if staged is not None:
                published, info = self._bound(post_id, staged)
                sha = await self._blob(published)
                entries.append(
                    (
                        "040000",
                        "tree",
                        await self._mktree([("100644", "blob", sha, staged.name)]),
                        post_id,
                    )
                )
                meta.append(info)
            _, doc = self._post_doc(thread, who, kind, body, reply_to, meta, post_id)
            entries.append(
                ("100644", "blob", await self._blob(_dump(doc)), f"{post_id}.json")
            )
            tree = await self._add_to_tree(history.tip, entries)
            sha = await self._commit(tree, history.tip, f"{kind} {post_id}")
            # Recorded before the ref moves: a crash in between leaves an
            # outbox row for a post that does not exist, which reads as nothing,
            # rather than a post nobody knows is ours.
            await self.outbox.record(post_id, thread, doc["ts"])
            try:
                await self._cas(THREAD_PREFIX + thread, sha, history.tip)
            except ForumCommandError:
                await self.outbox.forget([post_id])
                raise
            if fetched:  # offline: do not wait on the network a second time
                try:
                    await self._publish_locked(thread, fetched=True)
                except (ForumCommandError, ForumError) as exc:
                    logger.info(
                        "forum: thread %s stays unpublished for now: %s", thread, exc
                    )
        post = _parse_post(_dump(doc), thread=thread, stem=post_id, where="new post")
        assert post is not None
        return post

    def _bound(
        self, post_id: str, staged: StagedAttachment
    ) -> tuple[bytes, dict[str, Any]]:
        """
        The attachment bytes to publish, and its metadata. Over the cap, the
        published copy is cut with a marker and the whole one kept here.
        """
        full = staged.content.encode()
        digest = hashlib.sha256(full).hexdigest()
        cap = self.config.attachment_cap_bytes
        info = {
            "name": staged.name,
            "kind": staged.kind,
            "size": len(full),
            "sha256": digest,
            "truncated": False,
        }
        if len(full) <= cap:
            return full, info
        marker = (
            f"\n[truncated by VISTA: {len(full)} bytes, sha256 {digest}; "
            "full copy on the posting host]"
        ).encode()
        keep = full[: max(0, cap - len(marker))].decode(errors="ignore").encode()
        kept = self.repo_root / ATTACHMENTS_DIR / post_id / staged.name
        kept.parent.mkdir(parents=True, exist_ok=True)
        kept.write_bytes(full)
        info["truncated"] = True
        return keep + marker, info

    def attachment_copy(self, post_id: str, name: str) -> Path | None:
        """The full copy of a truncated attachment this install posted, if kept."""
        path = self.repo_root / ATTACHMENTS_DIR / post_id / Path(name).name
        return path if path.is_file() else None

    async def unpublished(self, thread: str | None = None) -> int:
        """How many of this install's posts the remote does not have yet."""
        return await self.outbox.unpublished_count(thread)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _dump(doc: dict[str, Any]) -> bytes:
    return (
        json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    ).encode()


def _validate_kind(kind: PostKind | str) -> None:
    if kind not in POSTABLE_KINDS:
        raise InvalidKind(
            f"{kind} is not postable. Postable kinds: "
            f"{', '.join(sorted(str(k) for k in POSTABLE_KINDS))}"
        )
