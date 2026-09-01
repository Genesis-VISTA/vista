"""
Tests for the h5i forum client.

Hermetic: they drive `tests/fixtures/fake_h5i.py`, which replays the behaviour
recorded in `docs/h5i-forum-contract.md` — including the parts that bite. Most of
what is asserted here is not "the client can post" but "the client does not
believe h5i when h5i is wrong": a dropped post, a vote position that skips votes,
an attachment the sandbox will refuse.

There is no real binary, no network and no sandbox in this module. Tests against
a real h5i belong behind the `live` marker.
"""

import pytest

from vista_backend.config import ForumSettings
from vista_backend.services import h5i_forum
from vista_backend.services.h5i_forum import (
    ForumClient,
    ForumDisabled,
    InvalidKind,
    ParticipantRole,
    PostKind,
    PostNotConfirmed,
    Thread,
    ThreadClosed,
    VotePolicy,
)


FAKE = __import__("pathlib").Path(__file__).parent / "fixtures" / "fake_h5i.py"


@pytest.fixture
def forum_root(tmp_path):
    (tmp_path / ".git" / ".h5i").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def client(forum_root) -> ForumClient:
    return ForumClient(
        ForumSettings(
            enabled=True,
            binary=str(FAKE),
            repo_root=forum_root,
            timeout=30.0,
        ),
        confirm_delay=0.0,  # nothing here is racing a real tend pass
    )


async def _debate(client: ForumClient, topic: str = "why does the knee move?"):
    """A thread with a proposer and a reviewer on it."""
    thread = await client.create_thread(topic, body="Debate it.")
    proposer = await client.create_participant(
        box_slug="proposer", identity="vista-proposer", role=ParticipantRole.WORKER
    )
    reviewer = await client.create_participant(
        box_slug="reviewer", identity="vista-reviewer", role=ParticipantRole.REVIEWER
    )
    return thread, proposer, reviewer


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_disabled_forum_refuses_before_spawning():
    client = ForumClient(ForumSettings(enabled=False))
    with pytest.raises(ForumDisabled):
        await client.list_threads()


@pytest.mark.anyio
async def test_enabled_without_repo_root_refuses(tmp_path):
    client = ForumClient(ForumSettings(enabled=True, repo_root=None))
    with pytest.raises(ForumDisabled):
        await client.list_threads()


# --------------------------------------------------------------------------- #
# Threads and attribution
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_thread_body_becomes_the_first_post(client):
    thread_id = await client.create_thread("topic", body="the human's framing")
    thread = await client.read_thread(thread_id)
    assert [p.kind for p in thread.posts] == [PostKind.TASK]
    assert thread.posts[0].body == "the human's framing"
    assert thread.posts[0].sender == "human"


@pytest.mark.anyio
async def test_each_role_posts_under_its_own_identity(client):
    """
    The reason the whole feature posts through boxes. Host-side posting has no
    `--as`, so without this the debate would be three posts by `human`.
    """
    thread_id, proposer, reviewer = await _debate(client)

    await client.post_as(
        proposer, thread_id, "rigidity, not activation", kind=PostKind.PROPOSAL
    )
    await client.post_as(
        reviewer, thread_id, "that predicts shear dependence", kind=PostKind.RISK
    )

    thread = await client.read_thread(thread_id)
    authored = {p.kind: p for p in thread.posts if p.sender != "human"}
    assert authored[PostKind.PROPOSAL].sender == "vista-proposer"
    assert authored[PostKind.PROPOSAL].role == "worker"
    assert authored[PostKind.RISK].sender == "vista-reviewer"
    assert authored[PostKind.RISK].role == "reviewer"
    # Host-stamped, not claimed: these arrive from the box, not the body.
    assert authored[PostKind.PROPOSAL].box_id == "env/human/proposer"
    assert authored[PostKind.PROPOSAL].policy_digest


@pytest.mark.anyio
async def test_human_posts_as_human(client):
    thread_id = await client.create_thread("topic", body="framing")
    post = await client.post_as_human(thread_id, "constrain to 1 bar")
    assert post.sender == "human"
    assert post.kind == PostKind.ASK


@pytest.mark.anyio
async def test_reply_to_threads_the_argument(client):
    thread_id, proposer, reviewer = await _debate(client)
    proposal = await client.post_as(
        proposer, thread_id, "claim", kind=PostKind.PROPOSAL
    )
    rebuttal = await client.post_as(
        reviewer, thread_id, "counter", kind=PostKind.RISK, reply_to=proposal.id
    )
    assert rebuttal.reply_to == proposal.id


# --------------------------------------------------------------------------- #
# Not believing a success
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_unpostable_kind_is_rejected_before_spawning(client):
    """
    `CLAIM` is a real kind that appears in threads, but `post --kind` will not
    publish it. h5i would accept it, exit 0, and drop it.
    """
    thread_id, proposer, _ = await _debate(client)
    with pytest.raises(InvalidKind):
        await client.post_as(proposer, thread_id, "mine", kind=PostKind.CLAIM)

    thread = await client.read_thread(thread_id)
    assert [p.kind for p in thread.posts] == [PostKind.TASK]


@pytest.mark.anyio
async def test_a_silently_dropped_post_is_caught_by_confirmation(client, monkeypatch):
    """
    The second line of defence, with the first one removed.

    If h5i ever drops a post for a reason `POSTABLE_KINDS` does not model, the
    caller must hear about it. Widening the postable set lets a kind through that
    the CLI accepts, reports success for, and discards — exactly the shape of the
    failure this guards.
    """
    thread_id, proposer, _ = await _debate(client)
    monkeypatch.setattr(
        h5i_forum,
        "POSTABLE_KINDS",
        h5i_forum.POSTABLE_KINDS | {PostKind.CLAIM},
    )

    with pytest.raises(PostNotConfirmed):
        await client.post_as(proposer, thread_id, "mine", kind=PostKind.CLAIM)


# --------------------------------------------------------------------------- #
# Votes
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_vote_positions_skip_earlier_votes(client):
    """
    The regression that motivated reading h5i's `write_view`.

    Votes are posts but are not numbered, so a thread carrying a vote makes the
    raw post index and the vote position disagree. Indexing the raw array would
    vote on the wrong post — or, here, on no post at all.
    """
    thread_id, proposer, reviewer = await _debate(client)
    proposal = await client.post_as(
        proposer, thread_id, "first", kind=PostKind.PROPOSAL
    )
    await client.vote(reviewer, thread_id, proposal.id)  # a vote lands mid-thread
    finding = await client.post_as(proposer, thread_id, "second", kind=PostKind.FINDING)

    await client.vote(reviewer, thread_id, finding.id)

    thread = await client.read_thread(thread_id)
    votes = [p for p in thread.posts if p.is_vote]
    assert {v.reply_to for v in votes} == {proposal.id, finding.id}
    assert thread.tally(finding.id) == 1


@pytest.mark.anyio
async def test_downvote_and_tally(client):
    thread_id, proposer, reviewer = await _debate(client)
    proposal = await client.post_as(
        proposer, thread_id, "claim", kind=PostKind.PROPOSAL
    )
    await client.vote(reviewer, thread_id, proposal.id, up=False)
    thread = await client.read_thread(thread_id)
    assert thread.tally(proposal.id) == -1


@pytest.mark.anyio
async def test_content_posts_hide_votes(client):
    thread_id, proposer, reviewer = await _debate(client)
    proposal = await client.post_as(
        proposer, thread_id, "claim", kind=PostKind.PROPOSAL
    )
    await client.vote(reviewer, thread_id, proposal.id)
    thread = await client.read_thread(thread_id)
    assert [p.kind for p in thread.content_posts()] == [
        PostKind.TASK,
        PostKind.PROPOSAL,
    ]


# --------------------------------------------------------------------------- #
# Closure
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_closing_ends_the_debate_for_the_agents(client):
    """The human's stop button. Enforced by h5i, not by the orchestrator."""
    thread_id, proposer, _ = await _debate(client)
    await client.close_thread(thread_id)

    with pytest.raises(ThreadClosed):
        await client.post_as(proposer, thread_id, "one more", kind=PostKind.FINDING)


@pytest.mark.anyio
async def test_closed_threads_are_still_readable_host_side(client):
    thread_id, _, _ = await _debate(client)
    await client.close_thread(thread_id)

    thread = await client.read_thread(thread_id)
    assert thread.is_closed
    assert PostKind.CLOSED in [p.kind for p in thread.posts]
    assert thread_id in [t.id for t in await client.list_threads(include_closed=True)]
    assert thread_id not in [t.id for t in await client.list_threads()]


@pytest.mark.anyio
async def test_voting_on_a_closed_thread_raises_thread_closed(client):
    thread_id, proposer, reviewer = await _debate(client)
    proposal = await client.post_as(
        proposer, thread_id, "claim", kind=PostKind.PROPOSAL
    )
    await client.close_thread(thread_id)
    with pytest.raises(ThreadClosed):
        await client.vote(reviewer, thread_id, proposal.id)


# --------------------------------------------------------------------------- #
# Attachments
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_attachment_staged_in_the_box_work_dir(client):
    """Citations and browser receipts ride along this path."""
    thread_id, proposer, _ = await _debate(client)

    name = await client.stage_attachment(proposer, "cite.txt", "source: example.org\n")
    assert (
        client.work_dir(proposer) / "cite.txt"
    ).read_text() == "source: example.org\n"

    post = await client.post_as(
        proposer, thread_id, "cited", kind=PostKind.FINDING, attachment=name
    )
    assert post.attachments and post.attachments[0]["name"] == "cite.txt"


@pytest.mark.anyio
async def test_host_path_attachment_is_refused(client, tmp_path):
    """
    The box's FS policy denies host paths, so an absolute path fails with EPERM.
    Staging exists precisely to keep callers off this path.
    """
    thread_id, proposer, _ = await _debate(client)
    outside = tmp_path / "outside.txt"
    outside.write_text("nope")

    with pytest.raises(h5i_forum.ForumCommandError):
        await client.post_as(
            proposer,
            thread_id,
            "cited",
            kind=PostKind.FINDING,
            attachment=str(outside),
        )


@pytest.mark.anyio
async def test_attachment_name_must_be_a_plain_basename(client):
    _, proposer, _ = await _debate(client)
    with pytest.raises(ValueError):
        await client.stage_attachment(proposer, ".hidden", "x")


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #


def test_vouch_is_parsed_beside_the_posts_not_onto_them():
    """
    h5i never merges engine-claimed with host-observed. Neither does the model:
    the lane is asked for, not read off a post.
    """
    thread = Thread.from_json(
        {
            "header": {
                "id": "t1",
                "title": "t",
                "created_at": "2026-08-27T00:00:00Z",
                "created_by": "human",
            },
            "status": "open",
            "posts": [
                {
                    "id": "p1",
                    "thread": "t1",
                    "kind": "FINDING",
                    "body": "b",
                    "sender": "vista-proposer",
                    "role": "worker",
                    "ts": "2026-08-27T00:00:00Z",
                }
            ],
            "vouch": [{"id": "p1", "lane": "host-observed"}],
        }
    )
    assert thread.lane("p1") == "host-observed"
    assert thread.lane("nonexistent") is None
    assert not hasattr(thread.posts[0], "lane")


def test_host_generated_posts_are_not_agent_authored():
    thread = Thread.from_json(
        {
            "header": {
                "id": "t1",
                "title": "t",
                "created_at": "2026-08-27T00:00:00Z",
                "created_by": "human",
            },
            "status": "closed",
            "posts": [
                {
                    "id": "p1",
                    "thread": "t1",
                    "kind": "TASK",
                    "body": "framing",
                    "sender": "human",
                    "role": "human",
                    "ts": "2026-08-27T00:00:00Z",
                },
                {
                    "id": "p2",
                    "thread": "t1",
                    "kind": "PROPOSAL",
                    "body": "claim",
                    "sender": "vista-proposer",
                    "role": "worker",
                    "ts": "2026-08-27T00:00:00Z",
                },
            ],
            "vouch": [],
        }
    )
    assert not thread.posts[0].agent_authored
    assert thread.posts[1].agent_authored


# --------------------------------------------------------------------------- #
# Federation
#
# The shapes here were measured against real h5i with two hosts and a shared
# bare repo; see docs/h5i-forum-contract.md §8.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_setting_a_remote_defaults_to_protectable_refs(client):
    """
    `--branch-refs` is on by default because the alternative cannot be protected:
    forge branch rules only reach `refs/heads/**`, so under a custom namespace
    anyone with push access can delete or force-push a thread and nothing refuses.
    """
    await client.set_remote("git@github.com:org/forum.git")
    described = await client.remote()
    assert "git@github.com:org/forum.git" in described
    assert "h5i-forum" in described, "threads published where a forge can protect them"


@pytest.mark.anyio
async def test_custom_refs_can_be_asked_for_explicitly(client):
    await client.set_remote("git@github.com:org/forum.git", branch_refs=False)
    assert "h5i-forum" not in await client.remote()


@pytest.mark.anyio
async def test_sync_reports_what_moved(client):
    result = await client.sync()
    assert result.pulled == 0 and result.pushed == 0


@pytest.mark.anyio
async def test_the_default_vote_policy_is_per_machine(client):
    assert await client.vote_policy() == VotePolicy.ORIGIN


@pytest.mark.anyio
async def test_the_vote_policy_can_be_set_to_principal(client):
    await client.set_vote_policy(VotePolicy.PRINCIPAL)
    assert await client.vote_policy() == VotePolicy.PRINCIPAL


@pytest.mark.anyio
async def test_no_enrollments_means_principal_would_count_nothing(client):
    """
    The trap worth surfacing: `principal` counts one vote per enrolled account
    and nothing at all from an unenrolled machine. Setting it on a forum where
    nobody has enrolled silently zeroes every vote, our own agents' included.
    """
    assert await client.enrollments() == []


@pytest.mark.anyio
async def test_enrollment_parses_h5i_field_names(client, forum_root):
    """
    h5i calls the login `display_name`, not `name`. Every field on `Enrollment`
    is optional, so a mismatch does not raise — it silently yields `None`, which
    is why this went unnoticed until a real enrollment existed. The payload below
    is the shape v0.3.8 actually emits, keys and all.
    """
    import json

    (forum_root / ".fake-forum.json").write_text(
        json.dumps(
            {
                "threads": {},
                "boxes": {},
                "participants": {},
                "views": {},
                "seq": 0,
                "enrollments": [
                    {
                        "version": 1,
                        "principal": "github.com/user/19734876",
                        "display_name": "jqyin",
                        "origin": "host-504de42f20b4dd28",
                        "ssh_pubkey": "ssh-rsa AAAAB3Nza...",
                        "enrolled_at": "2026-08-29T19:47:58.642194Z",
                        "signature": "-----BEGIN SSH SIGNATURE-----\n...\n",
                    }
                ],
            }
        )
    )

    (enrolled,) = await client.enrollments()
    assert enrolled.name == "jqyin"
    assert enrolled.principal == "github.com/user/19734876"
    assert enrolled.origin == "host-504de42f20b4dd28"
    assert not hasattr(enrolled, "verified"), (
        "`--json` emits the same object with and without `--verify`, so a "
        "`verified` field could only ever read as False when it means unasked"
    )


def test_a_failure_keeps_the_line_that_says_why():
    """
    Git puts the cause first and the boilerplate after it.

    Taking the last line reported a firewalled SSH port as "and the repository
    exists." — the tail of the advice paragraph, with `Repository not found` four
    lines above it and discarded. Whoever read that warning went looking for a
    missing repo that was there all along.
    """
    err = (
        "ERROR: Repository not found.\n"
        "fatal: Could not read from remote repository.\n"
        "\n"
        "Please make sure you have the correct access rights\n"
        "and the repository exists.\n"
    )
    message = str(h5i_forum.ForumCommandError(["h5i", "forum", "sync"], 1, "", err))
    assert "Repository not found" in message
    assert "`h5i forum sync` exited 1" in message
    assert "\n" not in message, "a log line must stay one line"


def test_a_failure_falls_back_to_stdout_and_then_to_nothing():
    """Some h5i commands report on stdout, and some report nothing at all."""
    on_stdout = str(
        h5i_forum.ForumCommandError(["h5i", "box", "ls"], 2, "no such box", "")
    )
    assert "no such box" in on_stdout

    silent = str(h5i_forum.ForumCommandError(["h5i", "box", "ls"], 2, "  \n ", ""))
    assert "no output" in silent


def test_a_runaway_stream_is_truncated_not_dropped():
    """
    The whole stream is on the exception; the message is bounded.

    A command that dumps a corpus should not put all of it on one log line, but
    it must not lose the first line either — that is where the cause is.
    """
    err = "fatal: the actual cause\n" + "\n".join(f"noise {i}" for i in range(500))
    exc = h5i_forum.ForumCommandError(["h5i", "forum", "sync"], 1, "", err)
    message = str(exc)
    assert "fatal: the actual cause" in message
    assert len(message) < 500
    assert message.endswith("\u2026")
    assert "noise 499" in exc.stderr, "the full stream survives on the exception"
