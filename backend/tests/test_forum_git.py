"""
The git-backed forum client (openspec/changes/forum-git-backend).

Hermetic: real git, a temporary bare repository as "the forge", and two
clients with separate data roots and databases as two installs ("host A" and
"host B"). No network, no forge, no LLM. What a peer does outside VISTA — a
malformed file, a force-push, a deleted branch — is done with plain git on the
remote, because that is what a peer would do.
"""

import asyncio
import hashlib
import json
import os
import shutil
import stat
import subprocess
import threading
import uuid
from pathlib import Path

import pytest

from vista_backend.config import ForumSettings, settings
from vista_backend.services import forum_git
from vista_backend.services.forum_git import (
    HOST_ID_FILE,
    THREAD_PREFIX,
    FileOutbox,
    OUTBOX_FILE,
    ForumClient,
    ForumCommandError,
    InvalidKind,
    Participant,
    PostKind,
    ThreadClosed,
    ThreadMissing,
    VouchLane,
    is_host_id,
    load_host_id,
)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

PROJECT = uuid.UUID("11111111-2222-3333-4444-555555555555")
PROPOSER = Participant(identity="vista-proposer-1a2b3c4d", role="proposer")
REVIEWER = Participant(identity="vista-reviewer-1a2b3c4d", role="reviewer")
PEER = Participant(identity="vista-proposer-9f9f9f9f", role="proposer")


# --------------------------------------------------------------------------- #
# Fixtures and raw-git helpers
# --------------------------------------------------------------------------- #

_RAW_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "peer",
    "GIT_AUTHOR_EMAIL": "peer@example.invalid",
    "GIT_COMMITTER_NAME": "peer",
    "GIT_COMMITTER_EMAIL": "peer@example.invalid",
}


def git(repo: Path, *args: str, input: bytes | None = None) -> str:
    """Plain git, as a peer outside VISTA would run it."""
    proc = subprocess.run(
        ["git", *args], cwd=repo, env=_RAW_ENV, input=input, capture_output=True
    )
    assert proc.returncode == 0, proc.stderr.decode()
    return proc.stdout.decode().strip()


@pytest.fixture
def remote(tmp_path) -> Path:
    path = tmp_path / "forge" / "forum.git"
    path.parent.mkdir()
    git(path.parent, "init", "-q", "--bare", path.name)
    return path


async def _host(root: Path, remote_url: str | None) -> ForumClient:
    """An install: its own data root, host id and outbox file, like a real one."""
    client = ForumClient(
        ForumSettings(
            enabled=True, repo_root=root / "forum-git" / str(PROJECT), timeout=30.0
        )
    )
    await client.ensure_repo()
    if remote_url is not None:
        await client.set_remote(remote_url)
    return client


@pytest.fixture
async def a(tmp_path, remote):
    return await _host(tmp_path / "host-a", str(remote))


@pytest.fixture
async def b(tmp_path, remote):
    return await _host(tmp_path / "host-b", str(remote))


def remote_refs(remote: Path) -> list[str]:
    out = git(remote, "for-each-ref", "--format=%(refname)")
    return out.split() if out else []


def remote_post_ids(remote: Path, thread: str) -> list[str]:
    out = git(
        remote,
        "log",
        "--first-parent",
        "--reverse",
        "--root",
        "--diff-filter=A",
        "--name-only",
        "--format=",
        THREAD_PREFIX + thread,
    )
    return [
        Path(p).stem
        for p in out.split()
        if p.startswith("posts/") and p.endswith(".json")
    ]


def peer_commit(remote: Path, thread: str, files: dict[str, bytes]) -> None:
    """A peer pushing a commit that adds `files` to a thread, with plain git."""
    ref = THREAD_PREFIX + thread
    parent = git(remote, "rev-parse", ref)
    env_index = remote / "peer-index"
    env = {**_RAW_ENV, "GIT_INDEX_FILE": str(env_index)}
    subprocess.run(["git", "read-tree", parent], cwd=remote, env=env, check=True)
    for path, content in files.items():
        blob = git(remote, "hash-object", "-w", "--stdin", input=content)
        subprocess.run(
            ["git", "update-index", "--add", "--cacheinfo", f"100644,{blob},{path}"],
            cwd=remote,
            env=env,
            check=True,
        )
    tree = (
        subprocess.run(
            ["git", "write-tree"], cwd=remote, env=env, capture_output=True, check=True
        )
        .stdout.decode()
        .strip()
    )
    env_index.unlink()
    commit = git(remote, "commit-tree", tree, "-p", parent, "-m", "peer")
    git(remote, "update-ref", ref, commit, parent)


def peer_post_doc(thread_id: str, /, **over) -> dict:
    post_id = over.pop("id", str(uuid.uuid7()))
    doc = {
        "v": 1,
        "id": post_id,
        "thread": thread_id,
        "kind": "FINDING",
        "body": "a peer's finding",
        "identity": "vista-reviewer-77777777",
        "role": "reviewer",
        "origin": "f" * 32,
        "ts": "2026-09-24T12:00:00+00:00",
        "reply_to": None,
        "attachments": [],
    }
    doc.update(over)
    return doc


def peer_post(remote: Path, thread_id: str, /, **over) -> str:
    doc = peer_post_doc(thread_id, **over)
    peer_commit(
        remote, thread_id, {f"posts/{doc['id']}.json": json.dumps(doc).encode()}
    )
    return doc["id"]


# --------------------------------------------------------------------------- #
# Host id and settings (1.2, 1.3)
# --------------------------------------------------------------------------- #


def test_the_host_id_is_created_once_and_reused(tmp_path):
    first = load_host_id(tmp_path)
    assert is_host_id(first)
    assert load_host_id(tmp_path) == first
    assert (tmp_path / HOST_ID_FILE).read_text(encoding="utf-8").strip() == first


def test_the_host_id_is_private_to_the_user(tmp_path):
    load_host_id(tmp_path)
    mode = stat.S_IMODE((tmp_path / HOST_ID_FILE).stat().st_mode)
    assert mode == 0o600


def test_a_fresh_data_dir_gets_a_new_host_id(tmp_path):
    assert load_host_id(tmp_path / "a") != load_host_id(tmp_path / "b")


def test_concurrent_first_reads_agree_on_one_id(tmp_path):
    seen: list[str] = []
    threads = [
        threading.Thread(target=lambda: seen.append(load_host_id(tmp_path)))
        for _ in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(seen)) == 1
    assert [p.name for p in tmp_path.iterdir()] == [HOST_ID_FILE], "no temp files left"


def test_a_corrupt_host_id_is_refused_not_replaced(tmp_path):
    (tmp_path / HOST_ID_FILE).write_text("not-a-host-id\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="does not hold a host id"):
        load_host_id(tmp_path)
    assert (tmp_path / HOST_ID_FILE).read_text(encoding="utf-8") == "not-a-host-id\n"


@pytest.mark.parametrize(
    "value, ok",
    [
        ("0123456789abcdef0123456789abcdef", True),
        ("0123456789ABCDEF0123456789ABCDEF", False),
        ("0123456789abcdef", False),
        ("", False),
        (None, False),
        (42, False),
    ],
)
def test_is_host_id(value, ok):
    assert is_host_id(value) is ok


def test_forum_settings_defaults():
    config = ForumSettings()
    assert config.git_binary == "git"
    assert config.push_retries == 5
    assert config.attachment_cap_bytes == 1_048_576


def test_the_forum_lives_under_forum_git():
    assert settings.forum_git_dir == settings.data_dir / "forum-git"


# --------------------------------------------------------------------------- #
# The runner (2.2)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_a_failing_command_reports_gits_first_line(a):
    with pytest.raises(ForumCommandError) as info:
        await a._git("rev-parse", "--verify", "no-such-ref")
    assert "fatal: Needed a single revision" in str(info.value)
    assert info.value.argv[1:] == ["rev-parse", "--verify", "no-such-ref"]


@pytest.mark.anyio
async def test_the_disabled_forum_refuses_before_running_anything(tmp_path):
    client = ForumClient(ForumSettings(enabled=False, repo_root=tmp_path))
    with pytest.raises(forum_git.ForumDisabled):
        await client.list_threads()


# --------------------------------------------------------------------------- #
# Repository and remote (2.3, 2.4)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_two_new_hosts_see_no_threads(a, b):
    assert await a.list_threads(include_closed=True) == []
    assert await b.list_threads(include_closed=True) == []


@pytest.mark.anyio
async def test_the_remote_round_trips(a, tmp_path):
    other = str(tmp_path / "elsewhere.git")
    await a.set_remote(other)
    assert await a.remote() == other
    await a.set_remote(other)  # idempotent
    assert await a.remote() == other


@pytest.mark.anyio
async def test_an_unreachable_remote_fails_the_first_sync(tmp_path):
    client = await _host(tmp_path / "lonely", str(tmp_path / "does-not-exist.git"))
    with pytest.raises(ForumCommandError, match="does-not-exist"):
        await client.sync()


@pytest.mark.anyio
async def test_an_empty_remote_syncs_cleanly(a):
    result = await a.sync()
    assert (result.pulled, result.pushed) == (0, 0)


# --------------------------------------------------------------------------- #
# Threads (2.5)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_a_new_thread_is_published_and_joinable(a, b, remote):
    thread = await a.create_thread("Why does the knee move?", body="Debate it.")
    assert remote_refs(remote) == [THREAD_PREFIX + thread]

    listed = await b.list_threads()
    assert [t.id for t in listed] == [thread]
    assert listed[0].header.title == "Why does the knee move?"
    assert listed[0].header.created_by == a.host_id
    assert listed[0].posts == 1

    read = await b.read_thread(thread)
    assert [p.kind for p in read.posts] == ["TASK"]
    assert read.posts[0].body == "Debate it."
    assert read.posts[0].sender == "human"


@pytest.mark.anyio
async def test_threads_are_listed_newest_first(a, b):
    first = await a.create_thread("first")
    second = await b.create_thread("second")
    third = await a.create_thread("third")
    assert [t.id for t in await b.list_threads()] == [third, second, first]


@pytest.mark.anyio
async def test_a_thread_without_framing_has_no_posts(a):
    thread = await a.create_thread("bare")
    assert (await a.read_thread(thread)).posts == []


# --------------------------------------------------------------------------- #
# Posting (2.6)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_an_invalid_kind_creates_no_commit(a):
    thread = await a.create_thread("t")
    tip = await a._rev(THREAD_PREFIX + thread)
    for kind in (PostKind.CLOSED, PostKind.TASK, PostKind.UPVOTE, "NONSENSE"):
        with pytest.raises(InvalidKind):
            await a.post_as(PROPOSER, thread, "x", kind=kind)  # type: ignore[arg-type]
    assert await a._rev(THREAD_PREFIX + thread) == tip
    assert await a.unpublished() == 0


@pytest.mark.anyio
async def test_a_post_is_committed_locally_without_the_network(tmp_path):
    offline = await _host(tmp_path / "offline", str(tmp_path / "unreachable.git"))
    thread = await offline.create_thread("offline debate")
    post = await offline.post_as(PROPOSER, thread, "a claim", kind=PostKind.PROPOSAL)
    assert post.sender == PROPOSER.identity
    assert post.role == "proposer"
    assert post.origin == offline.host_id
    assert await offline.unpublished(thread) == 1
    read = await offline.read_thread(thread)
    assert [p.id for p in read.posts] == [post.id]
    assert read.lane(post.id) == VouchLane.OBSERVED


@pytest.mark.anyio
async def test_commits_carry_the_forum_author_not_the_users(a, remote):
    thread = await a.create_thread("t")
    await a.post_as(PROPOSER, thread, "claim", kind=PostKind.PROPOSAL)
    authors = git(remote, "log", "--format=%an <%ae>|%cn <%ce>", THREAD_PREFIX + thread)
    expected = f"vista-forum <{a.host_id}@vista-forum.invalid>"
    assert set(authors.splitlines()) == {f"{expected}|{expected}"}


@pytest.mark.anyio
async def test_a_body_is_data_never_a_command(a, b):
    thread = await a.create_thread("t")
    body = "$(touch /tmp/pwned) `rm -rf ~` ; echo hi\n--kind CLOSED\x00 -m nope"
    await a.post_as(PROPOSER, thread, body, kind=PostKind.FINDING)
    assert (await b.read_thread(thread)).posts[0].body == body


@pytest.mark.anyio
async def test_only_forum_refs_reach_the_remote(a, remote):
    # Something else in the working repository must never be published.
    empty = await a._mktree([])
    other = await a._commit(empty, None, "not the forum")
    await a._git("update-ref", "refs/heads/main", other)
    await a.create_thread("t")
    await a.sync()
    assert all(r.startswith("refs/heads/vista-forum/") for r in remote_refs(remote))


# --------------------------------------------------------------------------- #
# Reading (2.7)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_posts_read_in_the_order_the_remote_accepted_them(a, b):
    thread = await a.create_thread("t", body="frame")
    p1 = await a.post_as(PROPOSER, thread, "one", kind=PostKind.PROPOSAL)
    p2 = await b.post_as(PEER, thread, "two", kind=PostKind.RISK)
    p3 = await a.post_as(REVIEWER, thread, "three", kind=PostKind.FINDING)
    for host in (a, b):
        read = await host.read_thread(thread)
        assert [p.body for p in read.posts] == ["frame", "one", "two", "three"]
    lanes_a = (await a.read_thread(thread)).vouch
    assert lanes_a[p1.id] == lanes_a[p3.id] == VouchLane.OBSERVED
    assert lanes_a[p2.id] == VouchLane.PEER_CLAIMED
    lanes_b = (await b.read_thread(thread)).vouch
    assert lanes_b[p2.id] == VouchLane.OBSERVED
    assert lanes_b[p1.id] == VouchLane.PEER_CLAIMED


@pytest.mark.anyio
async def test_malformed_peer_files_are_skipped_and_logged(a, remote, caplog):
    thread = await a.create_thread("t")
    good = await a.post_as(PROPOSER, thread, "fine", kind=PostKind.PROPOSAL)
    bad_id = str(uuid.uuid7())
    peer_commit(remote, thread, {f"posts/{bad_id}.json": b"{not json"})
    peer_post(remote, thread, v=2)
    peer_post(remote, thread, kind="EXPLODE")
    peer_post(remote, thread, thread=str(uuid.uuid7()))
    mismatched = peer_post_doc(thread)
    peer_commit(
        remote,
        thread,
        {f"posts/{uuid.uuid7()}.json": json.dumps(mismatched).encode()},
    )
    ok = peer_post(remote, thread, body="a peer's good post")

    read = await a.read_thread(thread)
    assert [p.id for p in read.posts] == [good.id, ok]
    skipped = [r for r in caplog.records if "skipping" in r.getMessage()]
    assert len(skipped) == 5


@pytest.mark.anyio
async def test_unknown_fields_are_ignored(a, remote):
    thread = await a.create_thread("t")
    post = peer_post(remote, thread, signer="github.com/jqyin", extra={"x": 1})
    assert [p.id for p in (await a.read_thread(thread)).posts] == [post]


@pytest.mark.anyio
async def test_a_peer_copying_our_origin_and_identity_stays_peer_claimed(a, remote):
    thread = await a.create_thread("t")
    ours = await a.post_as(PROPOSER, thread, "ours", kind=PostKind.PROPOSAL)
    forged = peer_post(
        remote,
        thread,
        identity=PROPOSER.identity,
        role="proposer",
        origin=a.host_id,
        body="pretending to be host A",
    )
    read = await a.read_thread(thread)
    assert read.lane(ours.id) == VouchLane.OBSERVED
    assert read.lane(forged) == VouchLane.PEER_CLAIMED


@pytest.mark.anyio
async def test_a_post_with_no_origin_is_unattributed(a, remote):
    thread = await a.create_thread("t")
    anon = peer_post(remote, thread, origin=None)
    odd = peer_post(remote, thread, origin="someone")
    read = await a.read_thread(thread)
    assert read.lane(anon) == read.lane(odd) == VouchLane.UNATTRIBUTED


@pytest.mark.anyio
async def test_posts_after_the_first_close_are_not_part_of_the_thread(a, b, remote):
    thread = await a.create_thread("t")
    await a.post_as(PROPOSER, thread, "before", kind=PostKind.PROPOSAL)
    await b.close_thread(thread)
    late = peer_post(remote, thread, body="after the close")
    read = await a.read_thread(thread)
    assert [p.kind for p in read.posts] == ["PROPOSAL", "CLOSED"]
    assert late not in read.vouch
    assert read.is_closed
    assert late in remote_post_ids(remote, thread), "still in git history"


@pytest.mark.anyio
async def test_a_failed_fetch_reads_what_is_here(a, tmp_path):
    thread = await a.create_thread("t")
    await a.post_as(PROPOSER, thread, "x", kind=PostKind.PROPOSAL)
    await a.set_remote(str(tmp_path / "gone.git"))
    assert len((await a.read_thread(thread)).posts) == 1


# --------------------------------------------------------------------------- #
# Publishing (2.8)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_two_hosts_posting_at_once_both_land(a, b, remote, tmp_path):
    thread = await a.create_thread("race")
    await b.read_thread(thread)
    # Both commit while neither can reach the remote...
    for host in (a, b):
        await host.set_remote(str(tmp_path / "offline.git"))
    pa = await a.post_as(PROPOSER, thread, "from A", kind=PostKind.PROPOSAL)
    pb = await b.post_as(PEER, thread, "from B", kind=PostKind.PROPOSAL)
    # ...then both publish at the same moment.
    for host in (a, b):
        await host.set_remote(str(remote))
    await asyncio.gather(a.sync(), b.sync())
    await a.sync()  # whichever went first learns of the other
    assert set(remote_post_ids(remote, thread)) == {pa.id, pb.id}
    assert await a.unpublished() == await b.unpublished() == 0
    assert [p.id for p in (await a.read_thread(thread)).posts] == [
        p.id for p in (await b.read_thread(thread)).posts
    ]


@pytest.mark.anyio
async def test_a_rejected_push_is_replayed_and_retried(a, b, remote, monkeypatch):
    """Deterministic race: B's push is beaten by A's between B's fetch and push."""
    thread = await a.create_thread("race")
    await b.read_thread(thread)
    real_push = ForumClient._push
    raced = False

    async def push_after_a(self, thread_id):
        nonlocal raced
        if self is b and not raced:
            raced = True
            await a.post_as(
                PROPOSER, thread_id, "A got there first", kind=PostKind.PROPOSAL
            )
        return await real_push(self, thread_id)

    monkeypatch.setattr(ForumClient, "_push", push_after_a)
    pb = await b.post_as(PEER, thread, "B", kind=PostKind.PROPOSAL)
    assert raced
    ids = remote_post_ids(remote, thread)
    assert pb.id in ids and len(ids) == 2
    assert await b.unpublished() == 0
    assert git(remote, "rev-list", "--count", THREAD_PREFIX + thread) == "3", (
        "no merges"
    )


@pytest.mark.anyio
async def test_offline_posts_flush_on_reconnect(a, b, remote, tmp_path):
    thread = await a.create_thread("t")
    await a.set_remote(str(tmp_path / "offline.git"))
    posts = [
        await a.post_as(PROPOSER, thread, f"offline {i}", kind=PostKind.FINDING)
        for i in range(3)
    ]
    assert await a.unpublished(thread) == 3
    await a.set_remote(str(remote))
    result = await a.sync()
    assert result.pushed == 3
    assert await a.unpublished() == 0
    assert [p.body for p in (await b.read_thread(thread)).posts] == [
        p.body for p in posts
    ]


@pytest.mark.anyio
async def test_a_force_push_that_drops_our_post_gets_it_republished(a, b, remote):
    thread = await a.create_thread("t")
    ours = await a.post_as(PROPOSER, thread, "ours", kind=PostKind.PROPOSAL)
    theirs = await b.post_as(PEER, thread, "theirs", kind=PostKind.RISK)
    await a.read_thread(thread)

    # Someone rewrites the thread back to its root, dropping both posts.
    ref = THREAD_PREFIX + thread
    root = git(remote, "rev-list", "--max-parents=0", ref)
    git(remote, "update-ref", ref, root)

    await a.sync()
    assert remote_post_ids(remote, thread) == [ours.id]
    read = await a.read_thread(thread)
    assert [p.id for p in read.posts] == [ours.id], (
        "the peer's post is gone from the thread"
    )
    assert await a.unpublished() == 0
    # The rewrite was never answered with a force-push: the remote's root stands.
    assert git(remote, "rev-list", "--max-parents=0", ref) == root
    assert theirs.id not in remote_post_ids(remote, thread)


@pytest.mark.anyio
async def test_a_published_post_that_vanishes_reads_unpublished_until_repushed(
    a, remote, tmp_path
):
    thread = await a.create_thread("t")
    await a.post_as(PROPOSER, thread, "ours", kind=PostKind.PROPOSAL)
    ref = THREAD_PREFIX + thread
    git(remote, "update-ref", ref, git(remote, "rev-list", "--max-parents=0", ref))
    # Fetch sees the rewrite, but the push cannot happen yet.
    await a._fetch()
    async with forum_git._thread_lock(a.repo, thread):
        await a._integrate(thread, fetched=True)
    assert await a.unpublished(thread) == 1


# --------------------------------------------------------------------------- #
# Close (2.9)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_posting_after_a_local_close_is_refused(a):
    thread = await a.create_thread("t")
    await a.close_thread(thread)
    with pytest.raises(ThreadClosed):
        await a.post_as(PROPOSER, thread, "too late", kind=PostKind.FINDING)
    with pytest.raises(ThreadClosed):
        await a.post_as_human(thread, "me too")
    await a.close_thread(thread)  # closing twice is fine
    assert [p.kind for p in (await a.read_thread(thread)).posts] == ["CLOSED"]


@pytest.mark.anyio
async def test_posting_after_a_peers_close_is_refused(a, b):
    thread = await a.create_thread("t")
    await b.read_thread(thread)
    await b.close_thread(thread)
    with pytest.raises(ThreadClosed):
        await a.post_as(PROPOSER, thread, "too late", kind=PostKind.FINDING)
    read = await a.read_thread(thread)
    assert read.is_closed
    assert read.lane(read.posts[-1].id) == VouchLane.PEER_CLAIMED
    assert not read.is_operator(read.posts[-1])


@pytest.mark.anyio
async def test_the_operators_close_is_the_operators(a):
    thread = await a.create_thread("t")
    await a.close_thread(thread)
    read = await a.read_thread(thread)
    assert read.is_operator(read.posts[-1])


# --------------------------------------------------------------------------- #
# Votes (2.10)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_a_repeated_vote_counts_once(a):
    thread = await a.create_thread("t")
    target = await a.post_as(PROPOSER, thread, "claim", kind=PostKind.PROPOSAL)
    await a.vote(REVIEWER, thread, target.id)
    await a.vote(REVIEWER, thread, target.id)
    assert (await a.read_thread(thread)).tally_split(target.id) == (1, 0)


@pytest.mark.anyio
async def test_a_changed_vote_replaces_the_earlier_one(a):
    thread = await a.create_thread("t")
    target = await a.post_as(PROPOSER, thread, "claim", kind=PostKind.PROPOSAL)
    await a.vote(REVIEWER, thread, target.id, up=True)
    await a.vote(REVIEWER, thread, target.id, up=False)
    read = await a.read_thread(thread)
    assert read.tally_split(target.id) == (-1, 0)
    assert read.tally(target.id) == -1


@pytest.mark.anyio
async def test_votes_are_split_by_lane(a, b, remote):
    thread = await a.create_thread("t")
    target = await a.post_as(PROPOSER, thread, "claim", kind=PostKind.PROPOSAL)
    await a.vote(REVIEWER, thread, target.id)
    await b.vote(PEER, thread, target.id)
    # A peer copying our reviewer's identity and origin is another voter.
    peer_post(
        remote,
        thread,
        kind="DOWNVOTE",
        body="",
        reply_to=target.id,
        identity=REVIEWER.identity,
        origin=a.host_id,
    )
    read = await a.read_thread(thread)
    assert read.tally_split(target.id) == (1, 0)
    assert read.tally(target.id) == 1
    assert len(read.content_posts()) == 1


@pytest.mark.anyio
async def test_voting_on_a_post_not_in_the_thread_is_refused(a):
    thread = await a.create_thread("t")
    with pytest.raises(forum_git.ForumError, match="not in thread"):
        await a.vote(REVIEWER, thread, str(uuid.uuid7()))


# --------------------------------------------------------------------------- #
# Attachments (2.11)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_a_small_attachment_is_published_whole(a, b, remote):
    thread = await a.create_thread("t")
    handle = await a.stage_attachment(PROPOSER, "citations.json", '{"ok": true}')
    post = await a.post_as(
        PROPOSER, thread, "cited", kind=PostKind.FINDING, attachment=handle
    )
    assert post.attachments == [
        {
            "name": "citations.json",
            "kind": "text",
            "size": 12,
            "sha256": hashlib.sha256(b'{"ok": true}').hexdigest(),
            "truncated": False,
        }
    ]
    blob = git(
        remote, "show", f"{THREAD_PREFIX + thread}:posts/{post.id}/citations.json"
    )
    assert blob == '{"ok": true}'
    assert (await b.read_thread(thread)).posts[0].attachments == post.attachments


@pytest.mark.anyio
async def test_a_large_receipt_is_bounded_and_kept_whole_locally(a, remote):
    thread = await a.create_thread("t")
    content = ("é receipts " * 200_000)[: 2 * 1024 * 1024]  # ~2 MB, multibyte
    full = content.encode()
    handle = await a.stage_attachment(REVIEWER, "citations.json", content)
    post = await a.post_as(
        REVIEWER, thread, "cited", kind=PostKind.FINDING, attachment=handle
    )
    info = post.attachments[0]
    assert info["truncated"] is True
    assert info["size"] == len(full)
    assert info["sha256"] == hashlib.sha256(full).hexdigest()

    published = subprocess.run(
        [
            "git",
            "cat-file",
            "blob",
            f"{THREAD_PREFIX + thread}:posts/{post.id}/citations.json",
        ],
        cwd=remote,
        capture_output=True,
        check=True,
    ).stdout
    assert len(published) <= 1_048_576
    published.decode()  # never cut mid-character
    assert published.endswith(
        f"[truncated by VISTA: {len(full)} bytes, sha256 {info['sha256']}; "
        "full copy on the posting host]".encode()
    )
    kept = a.attachment_copy(post.id, "citations.json")
    assert kept is not None
    assert hashlib.sha256(kept.read_bytes()).hexdigest() == info["sha256"]


@pytest.mark.anyio
async def test_attachment_kind_and_names(a):
    thread = await a.create_thread("t")
    handle = await a.stage_attachment(PROPOSER, "../../etc/simulation-1.json", "{}")
    post = await a.post_as(
        PROPOSER,
        thread,
        "result",
        kind=PostKind.FINDING,
        attachment=handle,
        attachment_kind="test-report",
    )
    assert post.attachments[0]["name"] == "simulation-1.json"
    assert post.attachments[0]["kind"] == "test-report"
    for bad in (".hidden", "", "a\nb"):
        with pytest.raises(ValueError):
            await a.stage_attachment(PROPOSER, bad, "x")
    with pytest.raises(forum_git.ForumError, match="no staged attachment"):
        await a.post_as(PROPOSER, thread, "x", kind=PostKind.FINDING, attachment="nope")


# --------------------------------------------------------------------------- #
# Missing threads (2.12)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_a_thread_deleted_on_the_forge_is_missing(a, b, remote):
    thread = await a.create_thread("t")
    await a.post_as(PROPOSER, thread, "x", kind=PostKind.PROPOSAL)
    await b.read_thread(thread)
    git(remote, "update-ref", "-d", THREAD_PREFIX + thread)
    for host in (a, b):
        with pytest.raises(ThreadMissing):
            await host.read_thread(thread)
        with pytest.raises(ThreadMissing):
            await host.post_as(PROPOSER, thread, "y", kind=PostKind.FINDING)
        assert await host.list_threads(include_closed=True) == []
    assert remote_refs(remote) == [], "never resurrected by a push"


@pytest.mark.anyio
async def test_an_unknown_or_h5i_era_thread_is_missing(a):
    for thread in (str(uuid.uuid7()), "7f3a9c", "../../HEAD", "a b"):
        with pytest.raises(ThreadMissing):
            await a.read_thread(thread)
        with pytest.raises(ThreadMissing):
            await a.post_as_human(thread, "hello?")


@pytest.mark.anyio
async def test_an_unpublished_thread_is_not_missing(tmp_path, remote):
    """Offline-created, then the remote is reachable: it is published, not lost."""
    host = await _host(tmp_path / "late", str(tmp_path / "offline.git"))
    thread = await host.create_thread("made offline", body="frame")
    await host.set_remote(str(remote))
    assert len((await host.read_thread(thread)).posts) == 1
    await host.sync()
    assert remote_refs(remote) == [THREAD_PREFIX + thread]


@pytest.mark.anyio
async def test_concurrent_posts_from_one_host_all_land(a, b, remote):
    thread = await a.create_thread("busy")
    other = await a.create_thread("another debate")
    posts = await asyncio.gather(
        *(
            a.post_as(PROPOSER, thread, f"post {i}", kind=PostKind.FINDING)
            for i in range(5)
        ),
        a.post_as(REVIEWER, other, "elsewhere", kind=PostKind.FINDING),
    )
    assert set(remote_post_ids(remote, thread)) == {p.id for p in posts[:5]}
    assert remote_post_ids(remote, other) == [posts[5].id]
    assert len((await b.read_thread(thread)).posts) == 5


# --------------------------------------------------------------------------- #
# The outbox's own file (task 3.7)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_the_outbox_lives_beside_the_repository(a):
    thread = await a.create_thread("t")
    await a.post_as(PROPOSER, thread, "x", kind=PostKind.PROPOSAL)
    assert (a.repo_root / OUTBOX_FILE).is_file()
    assert isinstance(a.outbox, FileOutbox)


@pytest.mark.anyio
async def test_posting_works_while_the_caller_holds_an_app_db_write(a, tmp_path):
    """
    The regression. The campaign monitor records a job's result and then posts
    it, inside one uncommitted transaction; a project save flushes and then
    syncs. With the outbox in the app database that post waited on the caller's
    own write until "database is locked". It must now go straight through.
    """
    import time

    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlmodel import SQLModel
    from sqlmodel.ext.asyncio.session import AsyncSession

    from vista_backend.db.schemas import ProjectTable

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'vista.db'}")

    @event.listens_for(engine.sync_engine, "connect")
    def _pragmas(conn, _):  # production pragmas, with a short timeout
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=2000")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    thread = await a.create_thread("t")
    try:
        async with AsyncSession(engine) as session:
            session.add(ProjectTable(name="held"))
            await session.flush()  # the app database's write lock is now held
            started = time.perf_counter()
            post = await a.post_as(PROPOSER, thread, "result", kind=PostKind.FINDING)
            assert time.perf_counter() - started < 1.5, "the post waited on a lock"
            await session.commit()
    finally:
        await engine.dispose()
    assert (await a.read_thread(thread)).lane(post.id) == VouchLane.OBSERVED


@pytest.mark.anyio
async def test_file_outbox_round_trip(tmp_path):
    box = FileOutbox(tmp_path / "p" / OUTBOX_FILE)
    ids = [f"post-{i}" for i in range(1200)]  # more than one IN chunk
    for i, post_id in enumerate(ids):
        await box.record(post_id, "t1" if i % 2 else "t2", f"2026-09-24T00:00:{i:04d}")
    assert await box.ours(ids + ["stranger"]) == set(ids)
    assert await box.unpublished_count() == 1200
    assert await box.unpublished_count("t1") == 600

    await box.mark_published(ids[:700], "now")
    assert await box.published(ids) == set(ids[:700])
    await box.mark_published(ids[:10], "later")  # already published: untouched
    await box.mark_unpublished(ids[:5])
    assert await box.unpublished_count() == 505
    await box.forget(ids[-3:])
    assert await box.ours(ids) == set(ids[:-3])


def test_file_outbox_import_keeps_existing_rows(tmp_path):
    box = FileOutbox(tmp_path / OUTBOX_FILE)
    assert box.insert_rows([("p1", "t", "a", None), ("p2", "t", "b", "c")]) == 2
    assert box.insert_rows([("p1", "t", "a", "changed"), ("p3", "t", "d", None)]) == 1
