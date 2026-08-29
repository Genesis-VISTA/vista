"""
A typed async client over the `h5i forum` CLI.

The forum is a git-backed store the *host* owns. There are two sides to it, and
which one a command runs on decides what it can do and how it is attributed:

  - **Host side** — any `h5i forum` call outside a box. Owns `create`, `attach`,
    `revoke`, `close`. Posts land immediately and are attributed to `human`.
  - **Box side** — `h5i forum` run inside a box via `h5i box run`. Owns `post`,
    `read`, `up`/`down`. Posts are staged and the host picks them up on its next
    tend pass.

Debate content is posted box-side, one box per role, because host-side `post` has
no `--as` flag: posting a three-way debate from the host would attribute every
role to `human` and throw away the attribution that makes a thread readable.

Written against h5i v0.3.8. Every quirk this module works around was measured,
not inferred — see `docs/h5i-forum-contract.md`, which also carries the JSON
shapes these models parse. h5i is not a stable public API; re-verify the contract
and re-record the test fixtures on upgrade.
"""

import asyncio
import json
import re
import shlex
from enum import StrEnum
from pathlib import Path
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field

from ..config import ForumSettings, settings


# --------------------------------------------------------------------------- #
# Kinds
# --------------------------------------------------------------------------- #


class PostKind(StrEnum):
    """
    Every kind that can appear in a thread.

    Some are produced by verbs other than `post` — `create` writes TASK, `close`
    writes CLOSED, `up`/`down` write UPVOTE/DOWNVOTE, `submit` writes
    REVIEW_REQUEST — so this set is wider than what `--kind` accepts. See
    `POSTABLE_KINDS`.
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
    CLAIM = "CLAIM"
    REVIEW_REQUEST = "REVIEW_REQUEST"
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
"""
Kinds `h5i forum post --kind` will actually publish.

This set is enforced *before* spawning, and that is not defensive tidiness: h5i
accepts an unrecognised kind, exits 0, prints "✔ staged", and then silently drops
the post at the host's tend pass. Validating here is the only thing standing
between a typo and a post that reports success and never exists.
"""


VOTE_KINDS: frozenset[PostKind] = frozenset({PostKind.UPVOTE, PostKind.DOWNVOTE})


class VouchLane(StrEnum):
    """
    How much the local host actually knows about where a post came from.

    The distinction only starts to matter once a forum has a remote, and it
    matters completely: `sender`, `role` and `origin` are stamped by *whichever*
    host observed the post, so on anything but OBSERVED they are that host's
    account of itself, not ours. h5i's own note is blunt about it — unsigned, and
    a reader may not treat it as proof of anything.
    """

    OBSERVED = "host-observed"
    """This host watched it happen. The stamped fields are ours and are reliable."""

    PEER_CLAIMED = "peer-claimed"
    """Arrived over the remote naming an origin. Every field is that peer's claim."""

    UNATTRIBUTED = "unattributed"
    """Arrived over the remote naming no origin at all."""


HUMAN_SENDER = "human"
"""
What the host stamps on its own posts.

Hardcoded in h5i (`host_identity()`), and deliberately not `$H5I_AGENT`: that
variable names an agent runtime, and reading it there would let an exported shell
variable rename the operator.
"""


class ParticipantRole(StrEnum):
    """
    h5i's role vocabulary, which is fixed and small.

    A debate's scientific roles (proposer, reviewer, referee) live in the forum
    *identity* instead — `--as vista-proposer` — which the host also stamps.
    """

    WORKER = "worker"
    REVIEWER = "reviewer"
    OBSERVER = "observer"


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class ForumError(Exception):
    """Base for every forum failure."""


class ForumDisabled(ForumError):
    """The feature is off, or `repo_root` is unset. Raised before any subprocess."""


class ForumCommandError(ForumError):
    """An h5i command failed. Carries the argv and both streams for diagnosis."""

    def __init__(
        self, argv: list[str], returncode: int, stdout: str, stderr: str
    ) -> None:
        self.argv = argv
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        detail = (stderr.strip() or stdout.strip() or "no output").splitlines()
        super().__init__(
            f"`{shlex.join(argv)}` exited {returncode}: {detail[-1] if detail else ''}"
        )


class ThreadClosed(ForumError):
    """
    The thread is closed, so a box may no longer post to it.

    This is the human's early-stop path and is expected control flow, not a
    fault: `close` is host-only, and after it the thread leaves every box's inbox
    so a box-side post fails closed. The orchestrator ends the run on this.
    """


class PostNotConfirmed(ForumError):
    """
    The command reported success but the post never appeared in the thread.

    The case this exists for is an unrecognised kind, which `POSTABLE_KINDS`
    should already have caught. Reaching here means h5i dropped a post for a
    reason this client does not yet model — surface it rather than pretend the
    post landed.
    """


class InvalidKind(ForumError, ValueError):
    """A kind `h5i forum post` would accept and then discard."""


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #


class Post(BaseModel):
    """
    One post as h5i renders it in `forum read --json`.

    Only `body` was written by the agent. `sender`, `role`, `box_id`,
    `policy_digest`, `origin` and `ts` are stamped by the host and the record
    format has no field a poster can write them through. Anything rendering a
    post has to keep that boundary visible.
    """

    id: str
    thread: str
    kind: str
    body: str
    sender: str
    role: str
    ts: str
    origin: str | None = None
    box_id: str | None = None
    policy_digest: str | None = None
    reply_to: str | None = None
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    denied: str | None = None
    """A host-recorded refusal. Read the post as evidence, not as a contribution."""

    @property
    def is_vote(self) -> bool:
        return self.kind in VOTE_KINDS

    @property
    def looks_agentic(self) -> bool:
        """
        Whether this reads as an agent's post rather than a person's.

        A person posts host-side and gets `sender == "human"` with no box; an
        agent posts through an attached box and carries its id. Useful for
        labelling and nothing more: on a peer's post all of it is their claim, so
        an outside human could present as an agent or the reverse.
        """
        return bool(self.box_id) and not self.claims_human

    @property
    def agent_authored(self) -> bool:
        """False for host-generated posts (TASK, CLOSED) — nobody claimed those."""
        return self.kind not in (PostKind.TASK, PostKind.CLOSED)

    @property
    def claims_human(self) -> bool:
        """
        The post's sender field says `human`. **Not** proof it is your operator.

        Every h5i host stamps its own operator's posts with this same literal, so
        once a forum is shared, an external participant posting from their own
        machine arrives as `human` too — not as an attack, but as the ordinary
        default. Deciding who the operator is needs the vouch lane, which lives
        on the thread: use `Thread.is_operator`.
        """
        return self.sender == HUMAN_SENDER


class ThreadHeader(BaseModel):
    id: str
    title: str
    created_at: str
    created_by: str
    version: int = 1


class Thread(BaseModel):
    """
    A thread and its posts, plus the vouch lanes kept deliberately beside them.

    h5i never merges `engine-claimed` with `host-observed`, so neither does this:
    `vouch` stays a mapping from post id to lane rather than a field on `Post`.
    Callers that want the lane ask for it.
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
        """The vouch lane for a post, or None when h5i vouched for nothing."""
        return self.vouch.get(post_id)

    def is_observed(self, post: Post) -> bool:
        """Did *this* host watch this post happen? Everything else is a claim."""
        return self.lane(post.id) == VouchLane.OBSERVED

    def is_operator(self, post: Post) -> bool:
        """
        Is this the human who owns this forum?

        Both halves are required. `sender == "human"` alone is what every host
        stamps its own operator with, so on a shared forum it is satisfied by
        every external participant. Only a post this host observed can be ours.
        """
        return post.claims_human and self.is_observed(post)

    def is_peer(self, post: Post) -> bool:
        """Did this arrive over the remote — i.e. is its attribution unverified?"""
        return not self.is_observed(post)

    def content_posts(self) -> list[Post]:
        """Posts a reader would read — votes folded away."""
        return [p for p in self.posts if not p.is_vote]

    def tally_split(self, post_id: str) -> tuple[int, int]:
        """
        Net votes on a post as `(observed, peer)`.

        Kept apart rather than summed because they answer different questions:
        one is what this forum's own participants would act on, the other is what
        outside readers think. A single number hides which.
        """
        observed = peer = 0
        for post in self.posts:
            if post.reply_to != post_id or post.kind not in VOTE_KINDS:
                continue
            delta = 1 if post.kind == PostKind.UPVOTE else -1
            if self.is_observed(post):
                observed += delta
            else:
                peer += delta
        return observed, peer

    def tally(self, post_id: str) -> int:
        """
        Net votes on a post, counted one per vote post.

        A vote is itself a post, carrying `reply_to` = its target. h5i's own
        rendered score applies a vote *policy* on top — one vote per machine by
        default, or per enrolled account under `forum policy --vote principal` —
        which this count does not model. For a single-host debate the two agree;
        where they differ, h5i's rendering is authoritative.
        """
        up = sum(
            1 for p in self.posts if p.kind == PostKind.UPVOTE and p.reply_to == post_id
        )
        down = sum(
            1
            for p in self.posts
            if p.kind == PostKind.DOWNVOTE and p.reply_to == post_id
        )
        return up - down

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> Self:
        """
        Parse `forum read --json`, whose `vouch` is a list of `{id, lane}` rather
        than a mapping.
        """
        vouch = {
            entry["id"]: entry["lane"]
            for entry in payload.get("vouch", [])
            if "id" in entry and "lane" in entry
        }
        return cls(
            header=ThreadHeader.model_validate(payload["header"]),
            status=payload.get("status", "open"),
            posts=[Post.model_validate(p) for p in payload.get("posts", [])],
            vouch=vouch,
        )


class ThreadSummary(BaseModel):
    """A row of `forum list --json`. `posts` is a count here, not the posts."""

    header: ThreadHeader
    status: str
    posts: int = 0
    last_activity: str | None = None
    denials: int = 0

    @property
    def id(self) -> str:
        return self.header.id


class VotePolicy(StrEnum):
    """
    How h5i counts a vote.

    `origin` needs no setup and counts one vote per machine. `principal` counts
    one per enrolled forge account — and counts nothing at all from a machine
    nobody enrolled, which is the trap: setting it before anyone enrolls makes
    every vote worthless, including our own agents'.
    """

    ORIGIN = "origin"
    PRINCIPAL = "principal"


class SyncResult(BaseModel):
    """What one exchange with the remote moved."""

    pulled: int = 0
    pushed: int = 0


class Enrollment(BaseModel):
    """
    A machine bound to a forge account, signed with that account's SSH key.

    There is deliberately no `verified` field. `enrollments --verify` re-checks
    each pinned key against the forge and reports the result in its *human*
    output only — `--json` emits the same object either way (measured against
    v0.3.8). A field that could never be populated would read as "not verified"
    rather than "not asked", so callers needing the check must run the CLI and
    read its text.
    """

    model_config = ConfigDict(populate_by_name=True)

    principal: str | None = None
    origin: str | None = None
    name: str | None = Field(default=None, alias="display_name")
    """h5i calls this `display_name`; the forge login, e.g. `jqyin`."""


class Participant(BaseModel):
    """
    A role on the forum: a box, the identity it posts under, and the policy it
    was attached at.
    """

    identity: str
    """The forum identity, e.g. `vista-proposer`. Host-stamped on every post."""

    role: ParticipantRole
    box_slug: str
    box_id: str
    """h5i's full box id, e.g. `env/human/proposer`."""

    policy_digest: str | None = None


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #


class ForumClient:
    """
    Async wrapper over the `h5i` CLI.

    Every invocation goes through `create_subprocess_exec` with an argument list.
    Nothing is ever handed to a shell: post bodies are model-generated, and a
    debate about shell metacharacters would otherwise be a debate about shell
    metacharacters.
    """

    def __init__(
        self,
        config: ForumSettings | None = None,
        *,
        confirm_attempts: int = 3,
        confirm_delay: float = 0.4,
    ) -> None:
        self.config = config or settings.forum
        # How hard to look for a post before declaring it lost. A host-side read
        # tends first, so the first attempt normally finds it; the retries cover
        # a tend pass that has not run yet. Tests set the delay to zero.
        self.confirm_attempts = confirm_attempts
        self.confirm_delay = confirm_delay
        # `up`/`down` resolve a position from the *last read* by that identity,
        # via a per-identity file on disk. So a read-then-vote pair has to be
        # atomic per role. Roles have separate boxes and separate last-view
        # files, so per-role locking is enough — a global lock would serialise
        # the whole debate for no benefit.
        self._vote_locks: dict[str, asyncio.Lock] = {}

    # -- plumbing ---------------------------------------------------------- #

    @property
    def repo_root(self) -> Path:
        if not self.config.enabled:
            raise ForumDisabled(
                "the agent forum is disabled; set VISTA_BACKEND_FORUM__ENABLED=true"
            )
        if self.config.repo_root is None:
            raise ForumDisabled(
                "the agent forum has no repo_root; set VISTA_BACKEND_FORUM__REPO_ROOT"
            )
        return self.config.repo_root

    async def _run(self, *args: str, check: bool = True) -> tuple[int, str, str]:
        """
        Run one h5i command in the forum repo and return (returncode, out, err).

        `check=False` is for commands whose non-zero exit is information — a
        box-side post to a closed thread, for instance.
        """
        argv = [self.config.binary, *args]
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(self.repo_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            raw_out, raw_err = await asyncio.wait_for(
                proc.communicate(), timeout=self.config.timeout
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise ForumCommandError(argv, -1, "", "timed out") from None

        out = raw_out.decode(errors="replace")
        err = raw_err.decode(errors="replace")
        if check and proc.returncode != 0:
            raise ForumCommandError(argv, proc.returncode or -1, out, err)
        return proc.returncode or 0, out, err

    async def _run_json(self, *args: str) -> Any:
        _, out, _ = await self._run(*args)
        try:
            return json.loads(out)
        except json.JSONDecodeError as exc:
            raise ForumCommandError(
                [self.config.binary, *args], 0, out, f"expected JSON: {exc}"
            ) from exc

    def _box_argv(self, box_slug: str, args: tuple[str, ...] | list[str]) -> list[str]:
        """
        The full argv for running an h5i command inside a box.

        Everything after `--` is a command the box execs, so the h5i binary has to
        be named there explicitly — `box run x -- forum post` asks the box to run
        a program called `forum` and fails with execvp exit 71.
        """
        return [
            self.config.binary,
            "box",
            "run",
            box_slug,
            "--",
            self.config.binary,
            *args,
        ]

    async def _run_in_box(
        self, box_slug: str, *args: str, check: bool = True
    ) -> tuple[int, str, str]:
        """Run an h5i command *inside* a role's box, which is what gives it an identity."""
        return await self._run(
            "box", "run", box_slug, "--", self.config.binary, *args, check=check
        )

    # -- participants ------------------------------------------------------ #

    async def create_participant(
        self, *, box_slug: str, identity: str, role: ParticipantRole
    ) -> Participant:
        """
        Create a box and put it on the forum under `identity`.

        Measured at ~0.5s for the pair, so a debate can afford one per role.
        """
        await self._run(
            "box",
            "create",
            box_slug,
            "--profile",
            self.config.box_profile,
            "--isolation",
            self.config.box_isolation,
        )
        await self._run(
            "forum", "attach", box_slug, "--as", identity, "--role", str(role)
        )
        status = await self._run_json("box", "status", box_slug, "--json")
        return Participant(
            identity=identity,
            role=role,
            box_slug=box_slug,
            box_id=status["id"],
            policy_digest=status.get("policy_digest"),
        )

    async def remove_participant(self, participant: Participant) -> None:
        """
        Take a role off the forum and delete its box.

        Its posts stay, attributed — revocation changes who may post next, not
        what was said. Best effort: a half-removed participant should not fail a
        debate that already finished.
        """
        for args in (
            ("forum", "revoke", participant.identity),
            ("box", "rm", participant.box_slug, "--force"),
        ):
            try:
                await self._run(*args)
            except ForumCommandError:
                pass

    def work_dir(self, participant: Participant) -> Path:
        """
        The box's working directory on the host.

        The only place a file can be staged from and still be readable inside the
        box: the fail-closed FS policy denies host paths, so attaching `/tmp/x`
        fails with EPERM. Derived from the box id rather than assembled from a
        guessed agent name.
        """
        return self.repo_root / ".git" / ".h5i" / participant.box_id / "work"

    async def stage_attachment(
        self, participant: Participant, name: str, content: str
    ) -> str:
        """
        Write an attachment into the box's work dir and return the relative name
        to pass to `--attach`.

        Used for citations and `h5i browser` receipts: h5i stores the payload
        content-addressed on the thread, so a reader can fetch back exactly the
        bytes the agent cited.
        """
        safe = Path(name).name
        if not safe or safe.startswith("."):
            raise ValueError(f"unusable attachment name: {name!r}")
        target = self.work_dir(participant) / safe
        await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(target.write_text, content)
        return safe

    # -- threads ----------------------------------------------------------- #

    # -- federation --------------------------------------------------------- #

    async def set_remote(self, url: str, *, branch_refs: bool = True) -> None:
        """
        Publish this forum to a git URL. Host-only.

        `branch_refs` publishes threads under `refs/heads/h5i-forum/**` so a forge
        can protect them. It defaults on because the alternative has no
        server-side protection at all: branch rules only reach `refs/heads/**`,
        so under a custom ref namespace anyone with push access can delete or
        force-push a thread and nothing refuses.
        """
        args = ["forum", "remote", url]
        if branch_refs:
            args.append("--branch-refs")
        await self._run(*args)

    async def remote(self) -> str:
        """Where this forum publishes, as h5i describes it. No JSON form exists."""
        _, out, _ = await self._run("forum", "remote")
        return out.strip()

    async def sync(self) -> SyncResult:
        """
        Exchange with the remote now.

        Rarely needed on its own: every host-side read tends the forum, and the
        tend pass syncs. This is for the moments nothing is reading — an idle
        debate whose thread a peer has just commented on.
        """
        _, out, _ = await self._run("forum", "sync")
        match = re.search(r"(\d+)\s+pulled,\s*(\d+)\s+pushed", out)
        if match is None:
            return SyncResult()
        return SyncResult(pulled=int(match.group(1)), pushed=int(match.group(2)))

    async def vote_policy(self) -> VotePolicy:
        payload = await self._run_json("forum", "policy", "--json")
        return VotePolicy(payload.get("vote", VotePolicy.ORIGIN))

    async def set_vote_policy(self, vote: VotePolicy) -> None:
        """
        Set how votes are counted. Host-only.

        Switching to `principal` before participants enroll silently zeroes every
        vote on the forum, so a caller should check `enrollments()` rather than
        assume the change was free.
        """
        await self._run("forum", "policy", "--vote", str(vote))

    async def enrollments(self) -> list[Enrollment]:
        """Machines bound to a forge account. Empty means `principal` counts nothing."""
        payload = await self._run_json("forum", "enrollments", "--json")
        return [Enrollment.model_validate(row) for row in payload]

    async def create_thread(
        self, title: str, *, body: str | None = None, ceiling: str | None = None
    ) -> str:
        """
        Open a thread and return its id. Host-only.

        The body becomes the thread's first post (`TASK`), so the human's framing
        is numbered, votable and scrubbed like everything else rather than
        floating above the record.
        """
        args = ["forum", "create", title]
        if body is not None:
            args += ["--body", body]
        if ceiling is not None:
            args += ["--ceiling", ceiling]
        await self._run(*args)

        # `create` prints the id but the JSON listing is the contract; take the
        # newest thread with this title rather than parsing decorated output.
        for row in await self.list_threads(include_closed=True):
            if row.header.title == title:
                return row.id
        raise ForumError(f"created thread {title!r} but it is not in the listing")

    async def list_threads(
        self, *, include_closed: bool = False
    ) -> list[ThreadSummary]:
        args = ["forum", "list", "--json"]
        if include_closed:
            args.insert(2, "--all")
        payload = await self._run_json(*args)
        rows = [ThreadSummary.model_validate(r) for r in payload]
        # Newest first — `list` has no ordering guarantee worth relying on.
        return sorted(rows, key=lambda r: r.header.created_at, reverse=True)

    async def read_thread(self, thread: str) -> Thread:
        """
        Read a thread. Accepts a unique id prefix.

        Host-side reads call h5i's tend pass first, so this also flushes any
        box-side post still staged — which is why confirming a post by reading
        costs nothing extra.
        """
        return Thread.from_json(await self._run_json("forum", "read", thread, "--json"))

    async def close_thread(self, thread: str) -> None:
        """
        Close a thread. Host-only, and the human's way to end a debate early.

        Nothing is deleted: the thread moves to the attic, reads with `--all`,
        and gains a host-stamped CLOSED post.
        """
        await self._run("forum", "close", thread)

    async def fetch_attachment(self, thread: str, position: int, out: Path) -> Path:
        """
        Save a post's attachment host-side, by its position in the last read.

        Position-based like the other stateful verbs, so the read that resolves
        it happens here rather than in the caller.
        """
        await self._run("forum", "read", thread)
        await self._run("forum", "fetch", str(position), "--out", str(out))
        return out

    # -- posting ----------------------------------------------------------- #

    async def post_as_human(
        self, thread: str, body: str, *, kind: PostKind = PostKind.ASK
    ) -> Post:
        """
        Post host-side, attributed to `human`.

        This is the human joining their own debate. It is the *only* correct use
        of host-side posting: agent content must go through `post_as`, or it
        would arrive wearing the human's name.
        """
        _validate_kind(kind)
        before = {p.id for p in (await self.read_thread(thread)).posts}
        await self._run("forum", "post", thread, "--kind", str(kind), body)
        return await self._confirm(thread, before, sender="human", kind=kind, body=body)

    async def post_as(
        self,
        participant: Participant,
        thread: str,
        body: str,
        *,
        kind: PostKind,
        reply_to: str | None = None,
        attachment: str | None = None,
        attachment_kind: str = "text",
    ) -> Post:
        """
        Post as a role, through its box, and confirm it landed.

        Two things here are load-bearing. The kind is validated first, because
        h5i accepts an unknown one and discards it after reporting success. And
        `--reply-to` takes a post id, unlike `forum reply`, which takes a
        position from that identity's last read — the id form is the only one
        that is safe to issue concurrently.
        """
        _validate_kind(kind)
        before = {p.id for p in (await self.read_thread(thread)).posts}

        args = ["forum", "post", thread, "--kind", str(kind), body]
        if reply_to is not None:
            args += ["--reply-to", reply_to]
        if attachment is not None:
            args += ["--attach", attachment, "--attach-kind", attachment_kind]

        code, out, err = await self._run_in_box(
            participant.box_slug, *args, check=False
        )
        if code != 0:
            if _is_thread_gone(out, err):
                raise ThreadClosed(
                    f"thread {thread} is no longer in {participant.identity}'s inbox "
                    "— the human closed it"
                )
            raise ForumCommandError(
                self._box_argv(participant.box_slug, args), code, out, err
            )

        return await self._confirm(
            thread, before, sender=participant.identity, kind=kind, body=body
        )

    async def vote(
        self, participant: Participant, thread: str, post_id: str, *, up: bool = True
    ) -> None:
        """
        Agree (or disagree) with a post — cheaper than a reply that adds nothing.

        `up`/`down` take a position from that identity's last read, so the read
        and the vote have to be one atomic unit. Hence the per-role lock, and
        hence resolving the position here instead of asking the caller for one.
        """
        lock = self._vote_locks.setdefault(participant.identity, asyncio.Lock())
        async with lock:
            code, out, err = await self._run_in_box(
                participant.box_slug, "forum", "read", thread, "--json", check=False
            )
            if code != 0:
                if _is_thread_gone(out, err):
                    raise ThreadClosed(f"thread {thread} is closed")
                raise ForumCommandError(
                    self._box_argv(
                        participant.box_slug, ["forum", "read", thread, "--json"]
                    ),
                    code,
                    out,
                    err,
                )

            position = _position_of(out, post_id)
            if position is None:
                raise ForumError(
                    f"post {post_id} is not visible to {participant.identity}"
                )
            await self._run_in_box(
                participant.box_slug, "forum", "up" if up else "down", str(position)
            )

    # -- confirmation ------------------------------------------------------ #

    async def _confirm(
        self,
        thread: str,
        before: set[str],
        *,
        sender: str,
        kind: PostKind,
        body: str,
    ) -> Post:
        """
        Re-read the thread until the new post shows up, and return it.

        Exit 0 does not mean posted. A box-side post is staged and the host
        publishes it on its next tend pass, and a post h5i decides not to publish
        is dropped with no error on the caller's side. Reading is the only
        acknowledgement there is; host-side reads tend first, so this converges
        on the first attempt in the normal case and the retries cover a pass that
        has not run yet.
        """
        for attempt in range(self.confirm_attempts):
            if attempt and self.confirm_delay:
                await asyncio.sleep(self.confirm_delay)
            thread_now = await self.read_thread(thread)
            for post in thread_now.posts:
                if (
                    post.id not in before
                    and post.sender == sender
                    and post.kind == str(kind)
                    and post.body == body
                ):
                    return post
        raise PostNotConfirmed(
            f"{kind} by {sender} reported success but never appeared in {thread}"
        )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _validate_kind(kind: PostKind) -> None:
    if kind not in POSTABLE_KINDS:
        raise InvalidKind(
            f"{kind} is not postable — h5i would accept it, report success, and "
            f"then drop the post. Postable kinds: "
            f"{', '.join(sorted(str(k) for k in POSTABLE_KINDS))}"
        )


def _is_thread_gone(*streams: str) -> bool:
    """
    Distinguish "the human closed this thread" from a real failure.

    A closed thread leaves every box's inbox, so the box-side error is about the
    thread not being visible rather than about closure.

    Both streams are searched because `box run` relays the *inner* command's
    stderr onto the host's stdout, under a `----- stderr -----` header; the
    host's own stderr carries only the run receipt. Checking stderr alone reads
    every closed thread as a hard failure.
    """
    return any("no thread matching" in s for s in streams)


def _position_of(read_json: str, post_id: str) -> int | None:
    """
    The 1-based position of a post as `up`/`down` count them.

    Votes are posts but they are not turns in the conversation, so h5i filters
    them out before numbering — `up 3` means the third thing somebody *said*.
    Indexing the raw `posts` array instead would drift by one for every vote
    already on the thread and silently vote on the wrong post.
    """
    try:
        payload = json.loads(read_json)
    except json.JSONDecodeError:
        return None
    turns = [p for p in payload.get("posts", []) if p.get("kind") not in VOTE_KINDS]
    for index, post in enumerate(turns, start=1):
        if post.get("id") == post_id:
            return index
    return None
