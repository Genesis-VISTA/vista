"""
An in-memory stand-in for `services.forum_git.ForumClient`.

For the orchestrator, API, simulation and grounding tests, which make hundreds
of forum calls and are about what VISTA does with a thread, not how git stores
one. The client itself is tested against real git in `test_forum_git.py`.

It reuses the real models (`Thread`, `Post`, `thread_status`), so tallies, lanes
and statuses are computed by the same code production runs. State is shared by
`repo_root`, because the API builds a fresh client per request and they must all
see the same forum — `reset()` clears it between tests.

Peers are simulated with `peer_post`, `peer_close` and `delete_thread`, which
act on "the remote" the way someone outside VISTA would.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from vista_backend.config import ForumSettings, settings
from vista_backend.services.forum_git import (
    HUMAN,
    POSTABLE_KINDS,
    ForumDisabled,
    ForumError,
    InvalidKind,
    Participant,
    Post,
    PostKind,
    SyncResult,
    Thread,
    ThreadClosed,
    ThreadHeader,
    ThreadMissing,
    ThreadSummary,
    VouchLane,
    is_thread_id,
    thread_status,
)

FAKE_HOST_ID = "a" * 32
PEER_HOST_ID = "b" * 32


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


@dataclass
class _Entry:
    post: Post
    ours: bool
    published: bool


@dataclass
class _Thread:
    header: ThreadHeader
    entries: list[_Entry] = field(default_factory=list)


@dataclass
class _Forum:
    threads: dict[str, _Thread] = field(default_factory=dict)
    remote: str | None = None
    online: bool = True
    attachments: dict[str, tuple[str, str]] = field(default_factory=dict)
    """ post id → (name, published content), for tests to inspect. """


_forums: dict[str, _Forum] = {}


def reset() -> None:
    _forums.clear()


class FakeForumClient:
    """Same surface as `ForumClient`; see the module docstring."""

    host_id = FAKE_HOST_ID

    def __init__(self, config: ForumSettings | None = None, **_: Any) -> None:
        self.config = config or settings.forum
        self._staged: dict[str, tuple[str, str, str]] = {}

    # -- plumbing ---------------------------------------------------------- #

    @property
    def repo_root(self) -> Path:
        if not self.config.enabled:
            raise ForumDisabled("the agent forum is disabled")
        if self.config.repo_root is None:
            raise ForumDisabled("this forum client has no project repository")
        return self.config.repo_root

    @property
    def forum(self) -> _Forum:
        return _forums.setdefault(str(self.repo_root), _Forum())

    def _thread(self, thread: str) -> _Thread:
        if not is_thread_id(thread) or thread not in self.forum.threads:
            raise ThreadMissing(f"thread {thread} is not on this forum")
        return self.forum.threads[thread]

    async def ensure_repo(self) -> None:
        self.repo_root

    async def remote(self) -> str | None:
        return self.forum.remote

    async def set_remote(self, url: str) -> None:
        self.forum.remote = url

    def _publish(self) -> int:
        if self.forum.remote is None or not self.forum.online:
            return 0
        n = 0
        for t in self.forum.threads.values():
            for e in t.entries:
                if e.ours and not e.published:
                    e.published = True
                    n += 1
        return n

    async def sync(self) -> SyncResult:
        if self.forum.remote is None:
            raise ForumDisabled("this forum has no remote")
        if not self.forum.online:
            raise ForumError(f"could not reach {self.forum.remote}")
        return SyncResult(pushed=self._publish())

    async def publish(self, thread: str) -> int:
        self._thread(thread)
        return self._publish()

    async def unpublished(self, thread: str | None = None) -> int:
        return sum(
            1
            for tid, t in self.forum.threads.items()
            if thread is None or tid == thread
            for e in t.entries
            if e.ours and not e.published
        )

    # -- threads ----------------------------------------------------------- #

    async def create_thread(self, title: str, *, body: str | None = None) -> str:
        thread = str(uuid.uuid7())
        self.forum.threads[thread] = _Thread(
            ThreadHeader(
                id=thread, title=title, created_at=_now(), created_by=self.host_id
            )
        )
        if body is not None:
            self._append(thread, HUMAN, PostKind.TASK, body)
        return thread

    def _visible(self, t: _Thread) -> list[_Entry]:
        for i, e in enumerate(t.entries):
            if e.post.kind == PostKind.CLOSED:
                return t.entries[: i + 1]
        return list(t.entries)

    async def read_thread(self, thread: str) -> Thread:
        t = self._thread(thread)
        visible = self._visible(t)
        return Thread(
            header=t.header,
            status=thread_status([e.post for e in visible]),
            posts=[e.post for e in visible],
            vouch={
                e.post.id: (
                    VouchLane.OBSERVED
                    if e.ours
                    else VouchLane.PEER_CLAIMED
                    if e.post.origin
                    else VouchLane.UNATTRIBUTED
                ).value
                for e in visible
            },
            published={e.post.id: e.published for e in visible if e.ours},
        )

    async def list_threads(
        self, *, include_closed: bool = False
    ) -> list[ThreadSummary]:
        rows = []
        for t in self.forum.threads.values():
            posts = [e.post for e in self._visible(t)]
            status = thread_status(posts)
            if status == "closed" and not include_closed:
                continue
            rows.append(
                ThreadSummary(
                    header=t.header,
                    status=status,
                    posts=len(posts),
                    last_activity=posts[-1].ts if posts else t.header.created_at,
                )
            )
        return sorted(rows, key=lambda r: r.header.created_at, reverse=True)

    async def close_thread(self, thread: str) -> None:
        t = self._thread(thread)
        if any(e.post.kind == PostKind.CLOSED for e in t.entries):
            return
        self._append(thread, HUMAN, PostKind.CLOSED, "")

    # -- posting ----------------------------------------------------------- #

    def _append(
        self,
        thread: str,
        who: Participant,
        kind: PostKind,
        body: str,
        *,
        reply_to: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
        ours: bool = True,
        origin: str | None = None,
    ) -> Post:
        t = self._thread(thread)
        post = Post(
            id=str(uuid.uuid7()),
            thread=thread,
            kind=str(kind),
            body=body,
            sender=who.identity,
            role=who.role,
            ts=_now(),
            origin=origin if not ours else self.host_id,
            reply_to=reply_to,
            attachments=attachments or [],
        )
        t.entries.append(_Entry(post, ours=ours, published=False))
        if ours:
            self._publish()
        return post

    def _refuse_closed(self, thread: str) -> None:
        t = self._thread(thread)
        if any(e.post.kind == PostKind.CLOSED for e in t.entries):
            raise ThreadClosed(f"thread {thread} is closed")

    async def stage_attachment(
        self, participant: Participant, name: str, content: str, *, kind: str = "text"
    ) -> str:
        safe = Path(name).name
        if not safe or safe.startswith("."):
            raise ValueError(f"unusable attachment name: {name!r}")
        handle = f"{uuid.uuid4().hex}/{safe}"
        self._staged[handle] = (safe, content, kind)
        return handle

    async def post_as_human(
        self, thread: str, body: str, *, kind: PostKind = PostKind.ASK
    ) -> Post:
        _validate(kind)
        self._refuse_closed(thread)
        return self._append(thread, HUMAN, kind, body)

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
        _validate(kind)
        self._refuse_closed(thread)
        meta = []
        staged = None
        if attachment is not None:
            staged = self._staged.pop(attachment, None)
            if staged is None:
                raise ForumError(f"no staged attachment {attachment!r} on this client")
            name, content, default_kind = staged
            meta = [
                {
                    "name": name,
                    "kind": attachment_kind or default_kind,
                    "size": len(content.encode()),
                    "truncated": False,
                }
            ]
        post = self._append(
            thread, participant, kind, body, reply_to=reply_to, attachments=meta
        )
        if staged is not None:
            self.forum.attachments[post.id] = (staged[0], staged[1])
        return post

    async def vote(
        self, participant: Participant, thread: str, post_id: str, *, up: bool = True
    ) -> None:
        self._refuse_closed(thread)
        if post_id not in {e.post.id for e in self._visible(self._thread(thread))}:
            raise ForumError(f"post {post_id} is not in thread {thread}")
        self._append(
            thread,
            participant,
            PostKind.UPVOTE if up else PostKind.DOWNVOTE,
            "",
            reply_to=post_id,
        )

    # -- what peers do ------------------------------------------------------ #

    def peer_post(
        self,
        thread: str,
        body: str,
        *,
        kind: str = "FINDING",
        identity: str = "vista-reviewer-99999999",
        role: str = "reviewer",
        origin: str | None = PEER_HOST_ID,
        reply_to: str | None = None,
    ) -> Post:
        """A post that arrived over the remote from another install."""
        return self._append(
            thread,
            Participant(identity=identity, role=role),
            PostKind(kind),
            body,
            reply_to=reply_to,
            ours=False,
            origin=origin,
        )

    def peer_close(self, thread: str) -> Post:
        return self.peer_post(thread, "", kind="CLOSED", identity="human", role="human")

    def delete_thread(self, thread: str) -> None:
        """Someone deleted the thread's branch on the forge."""
        self.forum.threads.pop(thread, None)

    def go_offline(self, offline: bool = True) -> None:
        self.forum.online = not offline


def _validate(kind: PostKind | str) -> None:
    if kind not in POSTABLE_KINDS:
        raise InvalidKind(f"{kind} is not postable")
