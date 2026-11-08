"""
The hooks of a hosted forum's repository: the lab's rules, and who pushed what.

`vista-collab-forum-hook pre` is `pre-receive`: it refuses a push that breaks a
rule the server enforces (below), and changes nothing. `post` is
`post-receive`: it writes one `forum_push` row for each post the push added
(and one `THREAD` row for each thread root, which is how a later `CLOSED` finds
the thread's opener).

The rules, for every ref a push updates:

- only `refs/heads/vista-forum/threads/<thread-id>`;
- no deletion, and nothing but a fast-forward;
- a `CLOSED` post only from the account that pushed the thread's root;
- a `RUN` post only when the thread's latest run is not live.

A run's liveness is the forum format's rule (`docs/forum-git-format.md`, "Runs"),
timed by when this server received the posts (`forum_push.received_at`), not by
their `ts`. It is reimplemented here: the server does not import the backend.

A push with the `vista-import` push option may create threads the forum does not
have yet (a project moved onto it): their posts are recorded `imported`, and the
`CLOSED` and `RUN` rules do not apply to them. For a thread that exists already
the option changes nothing.

The hooks run in git's environment, so the server hands them what they need in
`VISTA_*` variables when it starts `git http-backend` (`api/forums.py`). Whatever
goes wrong inside them is written to `forum-hook-errors.log` in the forums
directory, with the forum id, and refuses the push with a message that has no
traceback in it.
"""

import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import time
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from ..config import CollabSettings
from ..db.schemas import now_iso

THREAD_REF_PREFIX = "refs/heads/vista-forum/threads/"
IMPORT_OPTION = "vista-import"

FORUM_ID_ENV = "VISTA_FORUM_ID"
PUSHER_NAME_ENV = "VISTA_PUSHER_NAME"

_THREAD_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_POST_PATH = re.compile(r"^posts/([^/]+)\.json$")


class Refused(Exception):
    """A rule the push breaks; the message is what the pusher is told."""


@dataclass
class Entry:
    """A post (or a thread's root) a push adds, or one the thread already has."""

    kind: str
    post_id: str
    commit: str
    identity: str | None = None
    run: dict[str, object] | None = None
    received: datetime | None = None


def _is_zero(sha: str) -> bool:
    return set(sha) <= {"0"}


# ---------------------------------------------------------------------------
# Reading commits
# ---------------------------------------------------------------------------


def _git(*args: str, stdin: bytes | None = None) -> bytes:
    return subprocess.run(
        ["git", "-c", "core.quotePath=false", *args],
        input=stdin,
        capture_output=True,
        check=True,
    ).stdout


def _other_refs(ref: str) -> list[str]:
    out = _git("for-each-ref", "--format=%(refname)").decode("utf-8")
    return [r for r in out.splitlines() if r and r != ref]


def _added(revs: list[str]) -> list[tuple[str, str]]:
    """
    The files each commit adds, as (commit, path) in the order the remote took
    the commits (first-parent, oldest first). `revs` go to `git log --stdin`:
    tips to include, `^`-prefixed ones to leave out.
    """
    out = _git(
        "log",
        "--reverse",
        "--first-parent",
        "--root",
        "--name-only",
        "--diff-filter=A",
        "--format=%x00%H",
        "--stdin",
        stdin=("\n".join(revs) + "\n").encode("utf-8"),
    ).decode("utf-8")
    found: list[tuple[str, str]] = []
    for chunk in out.split("\0"):
        if not chunk.strip():
            continue
        commit, _, names = chunk.partition("\n")
        for name in names.splitlines():
            if name:
                found.append((commit.strip(), name))
    return found


def _blobs(specs: list[str]) -> list[bytes | None]:
    """The content of each `<commit>:<path>`, or None where it cannot be read."""
    if not specs:
        return []
    out = _git("cat-file", "--batch", stdin=("\n".join(specs) + "\n").encode("utf-8"))
    contents: list[bytes | None] = []
    pos = 0
    for _ in specs:
        end = out.index(b"\n", pos)
        header = out[pos:end].split()
        pos = end + 1
        if len(header) == 3 and header[1] == b"blob":
            size = int(header[2])
            contents.append(out[pos : pos + size])
            pos += size + 1
        else:
            contents.append(None)
    return contents


def _usable_run(value: object) -> dict[str, object] | None:
    """A RUN post's `run`, if the backend's reader would use it (`forum_git._parse_run`)."""
    if not isinstance(value, dict):
        return None
    rounds, roster = value.get("rounds"), value.get("roster")
    if not isinstance(rounds, int) or isinstance(rounds, bool) or rounds < 1:
        return None
    if not isinstance(roster, dict) or not roster:
        return None
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in roster.items()):
        return None
    return value


MAX_STATED_RUN_EXPIRY = 3 * 3600.0
"""
The longest run expiry a RUN post may ask for, in seconds. Three hours covers
a job-wait setting of up to about 2 3/4 hours plus the 15-minute margin, while
a single RUN by any team member can hold a thread against Continue for at most
3 hours, not a day. Must match the backend's `forum_git.MAX_STATED_RUN_EXPIRY`.
"""


def effective_run_expiry(own: float, run: dict[str, object]) -> float:
    """
    The server's expiry, or the one the RUN's starter stated in `run` if longer
    (capped), so a run on an install that waits longer than the server's default
    is not called dead while its backends still show it live. Never shorter than
    `own`. The backend's `forum_git.effective_run_expiry` is the same rule.
    """
    stated = run.get("expiry_seconds")
    if isinstance(stated, bool) or not isinstance(stated, int | float):
        return own
    try:
        number = float(stated)
    except OverflowError:
        return own
    if not math.isfinite(number) or number <= 0:
        return own
    return max(own, min(number, MAX_STATED_RUN_EXPIRY))


def entries(revs: list[str], thread_id: str) -> list[Entry]:
    """
    The posts and root the commits in `revs` add to thread `thread_id`, in
    order. A post file that is not valid JSON, or whose id or thread does not
    match its path and ref, is skipped, as every reader skips it.
    """
    files = _added(revs)
    wanted = [
        (c, p)
        for c, p in files
        if p == "thread.json" or _POST_PATH.match(p) is not None
    ]
    contents = _blobs([f"{c}:{p}" for c, p in wanted])
    found: list[Entry] = []
    for (commit, path), raw in zip(wanted, contents):
        if raw is None:
            continue
        try:
            data = json.loads(raw.decode("utf-8"))
        except UnicodeDecodeError, ValueError:
            continue
        if not isinstance(data, dict):
            continue
        if path == "thread.json":
            if data.get("id") == thread_id:
                found.append(Entry("THREAD", thread_id, commit))
            continue
        match = _POST_PATH.match(path)
        assert match is not None
        kind = data.get("kind")
        if (
            not isinstance(kind, str)
            or data.get("id") != match.group(1)
            or data.get("thread") != thread_id
        ):
            continue
        identity = data.get("identity")
        found.append(
            Entry(
                kind,
                match.group(1),
                commit,
                identity if isinstance(identity, str) else None,
                _usable_run(data.get("run")) if kind == "RUN" else None,
            )
        )
    return found


def _added_to(ref: str, old: str, new: str) -> list[Entry]:
    """What an update adds to its thread: everything new on the ref, and on no other."""
    thread_id = ref.removeprefix(THREAD_REF_PREFIX)
    if _is_zero(old):
        revs = [new, *(f"^{r}" for r in _other_refs(ref))]
    else:
        revs = [new, f"^{old}"]
    return entries(revs, thread_id)


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


def live_run(posts: list[Entry], *, now: datetime, expiry: float) -> Entry | None:
    """
    The RUN post of the thread's live run, or None: the latest RUN, unless the
    thread has a `CLOSED`, the run's Referee has posted `DONE` or `BLOCKED` since,
    or nothing (the RUN, or a post by its roster) was received within `expiry`
    seconds before `now`. The backend's `live_run`, with the server's clock.
    """
    if any(p.kind == "CLOSED" for p in posts):
        return None
    start = next(
        (
            i
            for i in range(len(posts) - 1, -1, -1)
            if posts[i].kind == "RUN" and posts[i].run is not None
        ),
        None,
    )
    if start is None:
        return None
    marker = posts[start]
    run = marker.run
    assert run is not None and marker.received is not None
    roster_map = run["roster"]
    assert isinstance(roster_map, dict)
    roster = set(roster_map.values())
    referee = roster_map.get("referee")
    newest = marker.received
    for post in posts[start + 1 :]:
        if post.identity not in roster:
            continue
        if post.identity == referee and post.kind in ("DONE", "BLOCKED"):
            return None
        if post.received is not None and post.received > newest:
            newest = post.received
    if (now - newest).total_seconds() > effective_run_expiry(expiry, run):
        return None
    return marker


# ---------------------------------------------------------------------------
# The database
# ---------------------------------------------------------------------------


@dataclass
class Context:
    settings: CollabSettings
    forum_id: str
    """ As the table stores a UUID: 32 hex digits, no dashes. """
    user: str
    user_name: str | None
    imports: bool
    _db: sqlite3.Connection | None = field(default=None, repr=False)

    @property
    def db(self) -> sqlite3.Connection:
        if self._db is None:
            url = self.settings.database_url
            if not url.startswith("sqlite"):
                raise RuntimeError("the forum hooks need a SQLite database")
            conn = sqlite3.connect(url.split("///", 1)[-1], timeout=30)
            conn.execute(f"PRAGMA journal_mode={self.settings.sqlite_journal_mode}")
            conn.execute("PRAGMA busy_timeout=30000")
            self._db = conn
        return self._db


def _received(ctx: Context, thread_id: str) -> dict[str, datetime]:
    rows = ctx.db.execute(
        "SELECT post_id, received_at FROM forum_push WHERE forum_id = ? AND thread_id = ?",
        (ctx.forum_id, thread_id),
    ).fetchall()
    return {post_id: datetime.fromisoformat(at) for post_id, at in rows}


def _root_pusher(ctx: Context, thread_id: str) -> str | None:
    row = ctx.db.execute(
        "SELECT pusher_sub FROM forum_push WHERE forum_id = ? AND thread_id = ? "
        "AND kind = 'THREAD' ORDER BY seq LIMIT 1",
        (ctx.forum_id, thread_id),
    ).fetchone()
    return row[0] if row else None


# ---------------------------------------------------------------------------
# pre-receive
# ---------------------------------------------------------------------------


def _check_update(ctx: Context, old: str, new: str, ref: str) -> None:
    if not ref.startswith(THREAD_REF_PREFIX) or not _THREAD_ID.match(
        ref.removeprefix(THREAD_REF_PREFIX)
    ):
        raise Refused(
            f"Refused {ref}: a forum accepts pushes only to {THREAD_REF_PREFIX}<thread-id>."
        )
    thread_id = ref.removeprefix(THREAD_REF_PREFIX)
    if _is_zero(new):
        raise Refused(f"Refused {ref}: a thread cannot be deleted.")
    creating = _is_zero(old)
    if not creating:
        ancestor = subprocess.run(
            ["git", "merge-base", "--is-ancestor", old, new], capture_output=True
        )
        if ancestor.returncode != 0:
            raise Refused(
                f"Refused {ref}: a thread is append-only; "
                "force-pushes and rewritten history are not accepted."
            )
    if creating and ctx.imports:
        return  # an import: nothing it brings is judged
    added = _added_to(ref, old, new)
    if not any(e.kind in ("CLOSED", "RUN") for e in added):
        return
    if creating:
        opener: str | None = ctx.user
        history: list[Entry] = []
    else:
        opener = _root_pusher(ctx, thread_id)
        history = entries([old], thread_id)
        received = _received(ctx, thread_id)
        epoch = datetime.fromtimestamp(0, UTC)
        for post in history:
            post.received = received.get(post.post_id, epoch)
    now = datetime.now(UTC)
    for post in added:
        post.received = now
        if post.kind == "CLOSED" and opener != ctx.user:
            raise Refused(
                f"Refused {ref}: only the thread's opener can close it "
                "(the account that opened the thread)."
            )
        if post.kind == "RUN" and post.run is not None:
            live = live_run(
                history, now=now, expiry=ctx.settings.forum_run_expiry_seconds
            )
            if live is not None:
                raise Refused(
                    f"Refused {ref}: a run is already live on this thread "
                    "(another install is continuing the debate); start yours when it ends."
                )
        history.append(post)


def pre_receive(ctx: Context, updates: list[tuple[str, str, str]]) -> None:
    for old, new, ref in updates:
        _check_update(ctx, old, new, ref)


# ---------------------------------------------------------------------------
# post-receive
# ---------------------------------------------------------------------------


def post_receive(ctx: Context, updates: list[tuple[str, str, str]]) -> None:
    rows = []
    for old, new, ref in updates:
        if not ref.startswith(THREAD_REF_PREFIX) or _is_zero(new):
            continue
        thread_id = ref.removeprefix(THREAD_REF_PREFIX)
        imported = ctx.imports and _is_zero(old)
        for post in _added_to(ref, old, new):
            rows.append(
                (
                    ctx.forum_id,
                    thread_id,
                    post.post_id,
                    post.kind,
                    post.commit,
                    ctx.user,
                    ctx.user_name,
                    imported,
                    now_iso(),
                )
            )
    if not rows:
        return
    with ctx.db:
        ctx.db.executemany(
            "INSERT OR IGNORE INTO forum_push (forum_id, thread_id, post_id, kind, "
            '"commit", pusher_sub, pusher_name, imported, received_at) '
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )


# ---------------------------------------------------------------------------
# Pending records
# ---------------------------------------------------------------------------
#
# `git receive-pack` reports a push as accepted to its client *before* it runs
# `post-receive`, so the client can push again while the records of its last push
# are not written yet, and a rule that reads them (who opened a thread, when a run
# began) would read too little. A push that passed `pre-receive` leaves a marker
# until its `post-receive` is done, and the next `pre-receive` waits for the markers
# to clear. Both hooks are children of the same `git receive-pack`, so its pid names
# the marker. A marker older than `_PENDING_TTL` is a push that never finished.

_PENDING_TTL = 10.0


def _pending_dir() -> Path:
    return Path(os.environ.get("GIT_DIR", ".")).resolve() / "vista-pending"


def _receive_pack_gone(pid: str) -> bool:
    """Whether the `git receive-pack` that left a marker has exited."""
    if sys.platform == "win32" or not pid.isdigit():
        return False  # `os.kill(pid, 0)` is not a probe there: the TTL covers it
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    return False


def _wait_for_pending() -> None:
    directory = _pending_dir()
    deadline = time.monotonic() + _PENDING_TTL
    while time.monotonic() < deadline:
        live = False
        for marker in directory.glob("*") if directory.is_dir() else []:
            try:
                if _receive_pack_gone(marker.name):
                    # `post-receive` does not run when every ref update fails
                    # after `pre-receive` passed (a lost compare-and-swap).
                    marker.unlink(missing_ok=True)
                elif time.time() - marker.stat().st_mtime < _PENDING_TTL:
                    live = True
            except OSError:
                continue
        if not live:
            return
        time.sleep(0.02)


def _mark_pending() -> None:
    directory = _pending_dir()
    directory.mkdir(exist_ok=True)
    (directory / str(os.getppid())).touch()


def _clear_pending() -> None:
    (_pending_dir() / str(os.getppid())).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _push_options() -> list[str]:
    count = int(os.environ.get("GIT_PUSH_OPTION_COUNT", "0") or 0)
    return [os.environ.get(f"GIT_PUSH_OPTION_{i}", "") for i in range(count)]


def _log_error(settings: CollabSettings, forum_id: str) -> None:
    try:
        settings.forums_dir.mkdir(parents=True, exist_ok=True)
        with (settings.forums_dir / "forum-hook-errors.log").open(
            "a", encoding="utf-8"
        ) as f:
            f.write(f"{now_iso()} forum {forum_id}\n{traceback.format_exc()}\n")
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    stage = args[0] if args else ""
    forum_id = os.environ.get(FORUM_ID_ENV, "?")
    settings: CollabSettings | None = None
    try:
        if stage not in ("pre", "post"):
            raise RuntimeError(f"unknown hook stage {stage!r}")
        settings = CollabSettings.model_validate({})
        ctx = Context(
            settings=settings,
            forum_id=uuid.UUID(forum_id).hex,
            user=os.environ["REMOTE_USER"],
            user_name=os.environ.get(PUSHER_NAME_ENV) or None,
            imports=IMPORT_OPTION in _push_options(),
        )
        updates = []
        for line in sys.stdin.read().splitlines():
            parts = line.split(" ", 2)
            if len(parts) == 3:
                updates.append((parts[0], parts[1], parts[2]))
        try:
            if stage == "pre":
                _wait_for_pending()
                pre_receive(ctx, updates)
                _mark_pending()
            else:
                try:
                    post_receive(ctx, updates)
                finally:
                    _clear_pending()
        finally:
            if ctx._db is not None:
                ctx._db.close()
    except Refused as e:
        print(str(e), file=sys.stderr)
        return 1
    except Exception:
        if settings is None:
            settings = CollabSettings.model_validate({})
        _log_error(settings, forum_id)
        if stage == "post":
            return 0  # the push is accepted already; the log has what went wrong
        print(
            "The server could not check this push; nothing was changed. "
            "Try again, and tell the server's administrator if it keeps failing.",
            file=sys.stderr,
        )
        return 1
    return 0


def run() -> None:
    sys.exit(main())


if __name__ == "__main__":
    run()
