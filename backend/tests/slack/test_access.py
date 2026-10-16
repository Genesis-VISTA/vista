"""
Who may do what from Slack: allowlisted workspaces and channels, linked users
only, and a polite, rate-limited refusal for everyone else.
"""

import pytest

from vista_backend.slack.access import (
    LINK_IN_DM,
    NOT_LINKED,
    WRONG_CHANNEL,
    UnlinkedReplies,
    decide,
)
from vista_backend.slack.events import Action, HomeOpened, Message

from .conftest import OTHER_TEAM, TEAM, link


def dm(user="U1", text="what's the queue like on Frontier?", team=TEAM):
    return Message(
        team_id=team,
        channel_id=f"D{user}",
        user_id=user,
        text=text,
        ts="1.0",
        is_dm=True,
    )


def mention(user="U1", channel="C1", text="<@UBOT> status of my last job?", team=TEAM):
    return Message(team_id=team, channel_id=channel, user_id=user, text=text, ts="2.0")


@pytest.mark.anyio
async def test_a_linked_user_is_allowed_as_their_vista_user(
    session, slack_config, alice
):
    await link(session, alice, "U1")

    for event in (dm(), mention()):
        decision = await decide(session, event, slack_config, UnlinkedReplies())
        assert decision.verdict == "allow"
        assert decision.linked.user.id == alice.id


@pytest.mark.anyio
async def test_an_unlinked_user_is_refused_politely_and_once(
    session, slack_config, alice
):
    await link(session, alice, "U1")
    replies = UnlinkedReplies()

    first = await decide(session, dm(user="U2"), slack_config, replies)
    assert first.verdict == "reply" and first.reply == NOT_LINKED
    assert first.linked is None

    again = await decide(session, mention(user="U2"), slack_config, replies)
    assert again.verdict == "drop"


@pytest.mark.anyio
async def test_the_refusal_comes_again_after_the_interval(
    session, slack_config, monkeypatch
):
    from vista_backend.config import settings

    monkeypatch.setattr(settings.slack, "unlinked_reply_interval", 0.0)
    replies = UnlinkedReplies()
    for _ in range(2):
        assert (
            await decide(session, dm(user="U2"), slack_config, replies)
        ).verdict == "reply"


@pytest.mark.anyio
async def test_an_unlinked_user_tapping_a_button_is_refused(
    session, slack_config, alice
):
    await link(session, alice, "U1")
    tap = Action(
        team_id=TEAM,
        channel_id="C1",
        user_id="U2",
        action_id="confirm",
        value="x",
        message_ts="3.0",
    )

    for _ in range(2):
        decision = await decide(session, tap, slack_config, UnlinkedReplies())
        assert decision.verdict == "reply" and decision.ephemeral


@pytest.mark.anyio
async def test_a_foreign_workspace_is_ignored_even_for_a_linked_id(
    session, slack_config, alice
):
    await link(session, alice, "U1", team_id=OTHER_TEAM)

    for event in (
        dm(team=OTHER_TEAM),
        mention(team=OTHER_TEAM),
        HomeOpened(OTHER_TEAM, "U1"),
    ):
        assert (
            await decide(session, event, slack_config, UnlinkedReplies())
        ).verdict == "drop"


@pytest.mark.anyio
async def test_the_link_is_per_workspace(session, slack_config, alice):
    slack_config.allowed_team_ids = [TEAM, OTHER_TEAM]
    await link(session, alice, "U1", team_id=OTHER_TEAM)

    decision = await decide(session, dm(), slack_config, UnlinkedReplies())
    assert decision.verdict == "reply" and decision.reply == NOT_LINKED


@pytest.mark.anyio
async def test_the_channel_allowlist_admits_its_channels_and_dms(
    session, slack_config, alice
):
    await link(session, alice, "U1")
    slack_config.allowed_channel_ids = ["C1"]
    replies = UnlinkedReplies()

    assert (
        await decide(session, mention(channel="C1"), slack_config, replies)
    ).verdict == "allow"
    assert (await decide(session, dm(), slack_config, replies)).verdict == "allow"

    elsewhere = await decide(session, mention(channel="C2"), slack_config, replies)
    assert elsewhere.verdict == "reply" and elsewhere.reply == WRONG_CHANNEL
    assert elsewhere.ephemeral


@pytest.mark.anyio
async def test_slack_off_drops_everything(session, slack_config, alice):
    await link(session, alice, "U1")
    slack_config.enabled = False

    assert (
        await decide(session, dm(), slack_config, UnlinkedReplies())
    ).verdict == "drop"


@pytest.mark.anyio
async def test_link_is_answered_for_anyone_but_only_in_a_dm(session, slack_config):
    assert (
        await decide(
            session, dm(user="U9", text="link"), slack_config, UnlinkedReplies()
        )
    ).verdict == "link"

    in_channel = await decide(
        session,
        mention(user="U9", text="<@UBOT> link"),
        slack_config,
        UnlinkedReplies(),
    )
    assert in_channel.verdict == "reply" and in_channel.reply == LINK_IN_DM


@pytest.mark.anyio
async def test_lock_from_an_unlinked_user_is_refused(session, slack_config):
    decision = await decide(
        session,
        mention(user="U9", text="<@UBOT> lock"),
        slack_config,
        UnlinkedReplies(),
    )
    assert decision.verdict == "reply" and decision.reply == NOT_LINKED


@pytest.mark.anyio
async def test_home_is_shown_only_to_a_linked_user(session, slack_config, alice):
    await link(session, alice, "U1")

    assert (
        await decide(session, HomeOpened(TEAM, "U1"), slack_config, UnlinkedReplies())
    ).verdict == "allow"
    assert (
        await decide(session, HomeOpened(TEAM, "U2"), slack_config, UnlinkedReplies())
    ).verdict == "drop"
