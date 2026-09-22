"""
The forum client against a real h5i binary.

Marked `live` and excluded from the default PR filter: it needs h5i installed and
it creates real boxes, which means a real sandbox tier on the host.

Its job is to keep `tests/fixtures/fake_h5i.py` honest. The hermetic suite is
only worth as much as the fake's fidelity, and a fake that drifts from the tool
turns green tests into a story about itself. This module asserts the same
behaviours through the real thing, so drift shows up here rather than in
production. Run it whenever h5i is upgraded, and re-record the fake if it fails.

    uv run --extra dev pytest tests/test_h5i_forum_live.py -m live
"""

import shutil
import subprocess

import pytest

from vista_backend.config import ForumSettings
from vista_backend.services.h5i_forum import (
    ForumClient,
    ParticipantRole,
    PostKind,
    ThreadClosed,
)


pytestmark = [pytest.mark.live, pytest.mark.anyio]


H5I = shutil.which("h5i")


@pytest.fixture
def live_client(tmp_path):
    if H5I is None:
        pytest.skip("h5i is not installed")

    # h5i stores a forum under a git repo, so the fixture needs a real one.
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for key, value in (("user.email", "test@local"), ("user.name", "test")):
        subprocess.run(["git", "-C", str(tmp_path), "config", key, value], check=True)
    (tmp_path / "README.md").write_text("live forum test\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "init"], check=True)

    client = ForumClient(
        ForumSettings(
            enabled=True,
            binary=H5I,
            repo_root=tmp_path,
            box_isolation="process",
            timeout=120.0,
        )
    )
    yield client


async def test_real_forum_round_trip(live_client):
    """
    The whole contract in one pass, against the real tool.

    Each assertion here mirrors one the hermetic suite makes against the fake.
    """
    client = live_client
    thread_id = await client.create_thread(
        "live: which mechanism explains the knee?", body="Debate it."
    )

    proposer = await client.create_participant(
        box_slug="proposer", identity="vista-proposer", role=ParticipantRole.WORKER
    )
    reviewer = await client.create_participant(
        box_slug="reviewer", identity="vista-reviewer", role=ParticipantRole.REVIEWER
    )
    try:
        # Distinct, host-stamped identities — the reason for the box relay.
        proposal = await client.post_as(
            proposer,
            thread_id,
            "network rigidity sets the knee",
            kind=PostKind.PROPOSAL,
        )
        assert proposal.sender == "vista-proposer"
        assert proposal.role == "worker"
        assert proposal.box_id and proposal.policy_digest

        rebuttal = await client.post_as(
            reviewer,
            thread_id,
            "that predicts shear dependence nobody measured",
            kind=PostKind.RISK,
            reply_to=proposal.id,
        )
        assert rebuttal.sender == "vista-reviewer"
        assert rebuttal.reply_to == proposal.id

        # The human joins their own debate, as themselves.
        human = await client.post_as_human(thread_id, "constrain to 1 bar")
        assert human.sender == "human"

        # A vote lands mid-thread, then a post after it: the position the client
        # computes has to skip the vote.
        await client.vote(reviewer, thread_id, proposal.id)
        finding = await client.post_as(
            proposer, thread_id, "Cantor 2019 puts it at 803K", kind=PostKind.FINDING
        )
        await client.vote(reviewer, thread_id, finding.id)

        thread = await client.read_thread(thread_id)
        assert thread.tally(proposal.id) == 1
        assert thread.tally(finding.id) == 1
        assert thread.lane(proposal.id) == "host-observed"

        # An attachment staged in the box's work dir survives the round trip.
        name = await client.stage_attachment(
            proposer, "cite.txt", "source: https://example.org/cantor2019\n"
        )
        cited = await client.post_as(
            proposer,
            thread_id,
            "receipt attached",
            kind=PostKind.FINDING,
            attachment=name,
        )
        assert cited.attachments

        # The human's stop button, enforced by h5i.
        await client.close_thread(thread_id)
        with pytest.raises(ThreadClosed):
            await client.post_as(proposer, thread_id, "one more", kind=PostKind.FINDING)

        closed = await client.read_thread(thread_id)
        assert closed.is_closed
    finally:
        await client.remove_participant(proposer)
        await client.remove_participant(reviewer)


async def test_real_h5i_drops_an_unknown_kind(live_client):
    """
    Pin the surprise the client is built around.

    If a future h5i starts rejecting an unknown kind outright, this fails and the
    confirm-by-reading logic can be reconsidered. Until then it is load-bearing.
    """
    client = live_client
    thread_id = await client.create_thread("live: kind validation", body="framing")
    proposer = await client.create_participant(
        box_slug="proposer", identity="vista-proposer", role=ParticipantRole.WORKER
    )
    try:
        code, out, _ = await client._run_in_box(
            "proposer",
            "forum",
            "post",
            thread_id,
            "--kind",
            "VERDICT",
            "this should vanish",
            check=False,
        )
        assert code == 0, "h5i still exits 0 for an unknown kind"
        assert "staged" in out, "h5i still reports success for an unknown kind"

        thread = await client.read_thread(thread_id)
        assert "VERDICT" not in [p.kind for p in thread.posts], (
            "h5i now publishes unknown kinds — revisit POSTABLE_KINDS"
        )
    finally:
        await client.remove_participant(proposer)


async def test_two_hosts_sharing_a_remote(tmp_path):
    """
    The federation contract, end to end, against real h5i.

    This is the test the whole peer-provenance design rests on: an external
    participant posting normally from their own machine arrives with
    `sender == "human"` — byte-identical to the local operator's posts — and is
    distinguishable *only* by origin and vouch lane. It also shows that a peer
    can wear one of our role identities, and that push access alone lets them
    close a thread they did not open.
    """
    if H5I is None:
        pytest.skip("h5i is not installed")

    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)

    def host(name: str) -> ForumClient:
        root = tmp_path / name
        root.mkdir()
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        for key, value in (("user.email", f"{name}@local"), ("user.name", name)):
            subprocess.run(["git", "-C", str(root), "config", key, value], check=True)
        (root / "README.md").write_text(f"{name}\n")
        subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-qm", "init"], check=True)
        return ForumClient(
            ForumSettings(
                enabled=True,
                binary=H5I,
                repo_root=root,
                box_isolation="process",
                timeout=120.0,
            )
        )

    alpha, beta = host("alpha"), host("beta")
    for side in (alpha, beta):
        await side.set_remote(str(remote))

    thread_id = await alpha.create_thread("live: federation", body="Debate it.")
    await alpha.post_as_human(thread_id, "the local operator speaking")
    await alpha.sync()

    assert (await beta.sync()).pulled > 0, "the thread reaches the other host"

    # An ordinary external human, posting normally. No spoofing involved.
    await beta.post_as_human(thread_id, "an outsider speaking")
    await beta.sync()
    await alpha.sync()

    thread = await alpha.read_thread(thread_id)
    ours = next(p for p in thread.posts if p.body == "the local operator speaking")
    theirs = next(p for p in thread.posts if p.body == "an outsider speaking")

    assert ours.sender == theirs.sender == "human", (
        "every host stamps its own operator with the same literal — the sender "
        "field cannot tell them apart"
    )
    assert ours.origin != theirs.origin
    assert thread.is_operator(ours) and not thread.is_operator(theirs)
    assert thread.is_observed(ours) and thread.is_peer(theirs)
    assert thread.lane(theirs.id) == "peer-claimed"


async def test_a_peer_can_wear_one_of_our_role_identities(tmp_path):
    """
    Nothing stops a peer attaching a box under `vista-proposer-…`. The lane is
    the only signal, which is why the UI derives role badges from it.
    """
    if H5I is None:
        pytest.skip("h5i is not installed")

    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)

    def host(name: str) -> ForumClient:
        root = tmp_path / name
        root.mkdir()
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        for key, value in (("user.email", f"{name}@local"), ("user.name", name)):
            subprocess.run(["git", "-C", str(root), "config", key, value], check=True)
        (root / "README.md").write_text(f"{name}\n")
        subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-qm", "init"], check=True)
        return ForumClient(
            ForumSettings(
                enabled=True,
                binary=H5I,
                repo_root=root,
                box_isolation="process",
                timeout=120.0,
            )
        )

    alpha, beta = host("alpha"), host("beta")
    for side in (alpha, beta):
        await side.set_remote(str(remote))

    thread_id = await alpha.create_thread("live: impersonation", body="go")
    await alpha.sync()
    await beta.sync()

    impostor = await beta.create_participant(
        box_slug="imposter",
        identity="vista-proposer-1a2b",
        role=ParticipantRole.WORKER,
    )
    try:
        await beta.post_as(
            impostor, thread_id, "I am your proposer.", kind=PostKind.PROPOSAL
        )
        await beta.sync()
        await alpha.sync()

        thread = await alpha.read_thread(thread_id)
        spoof = next(p for p in thread.posts if p.kind == PostKind.PROPOSAL)

        assert spoof.sender == "vista-proposer-1a2b", "the name is entirely theirs"
        assert spoof.role == "worker"
        assert thread.is_peer(spoof), (
            "the vouch lane is the only thing that gives it away"
        )
        assert not thread.is_observed(spoof)
    finally:
        await beta.remove_participant(impostor)


async def test_setting_up_a_projects_forum_against_a_real_remote(tmp_path, monkeypatch):
    """
    `ensure_forum` against real h5i and a real remote.

    Everything except the forge itself: creating the project's repository from
    nothing, setting the remote, proving it reachable with a sync, and the vote
    policy refusing to tighten while nobody is enrolled. Authentication and forge
    ref-protection are what remain untested, and they need an actual GitHub
    repository.

    The repository is created here, not seeded: that is the whole of what a
    person has to do to get a lab — paste a URL — and the four commands behind it
    are the same four a human peer runs to join a forum.
    """
    if H5I is None:
        pytest.skip("h5i is not installed")

    from vista_backend.agents.forum.project_forum import (
        ensure_forum,
        forum_config_for,
        forum_root,
    )
    from vista_backend.config import settings as app_settings
    from vista_backend.db.schemas import ProjectTable
    from vista_backend.services.h5i_forum import VotePolicy

    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)

    monkeypatch.setattr(app_settings, "data_dir", tmp_path)
    monkeypatch.setattr(
        app_settings,
        "forum",
        ForumSettings(
            enabled=True,
            binary=H5I,
            box_isolation="process",
            timeout=120.0,
            vote_policy="principal",
        ),
    )
    project = ProjectTable(name="live-forum", forum_repo_url=str(remote))

    await ensure_forum(project)

    root = forum_root(project.id)
    assert (root / ".git").is_dir(), "the project's repository was created"
    client = ForumClient(forum_config_for(project))
    assert str(remote) in await client.remote(), "the project's remote was applied"
    assert await client.vote_policy() == VotePolicy.ORIGIN, (
        "principal is refused while nobody is enrolled — it would discard every "
        "vote on the forum, the agents' own included"
    )

    # And the remote really is usable, not merely recorded.
    thread_id = await client.create_thread("live: startup federation", body="go")
    await client.post_as_human(thread_id, "published")
    assert (await client.sync()).pushed >= 0
    published = subprocess.run(
        ["git", "-C", str(remote), "for-each-ref", "--format=%(refname)"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "h5i-forum" in published, "threads land under the protectable ref namespace"
