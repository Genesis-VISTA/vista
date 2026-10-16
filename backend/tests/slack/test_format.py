"""Summary-first Slack replies in mrkdwn that never exceed Slack's limits."""

import json

import pytest

from vista_backend.slack import format as fmt


def test_markdown_becomes_mrkdwn():
    md = "# Queue\n**Frontier** is *busy*, see [status](https://example.org/s).\n- one\n* two\n~~old~~"
    out = fmt.to_mrkdwn(md)
    assert out.splitlines()[0] == "*Queue*"
    assert "*Frontier* is _busy_" in out
    assert "<https://example.org/s|status>" in out
    assert "• one" in out and "• two" in out
    assert "~old~" in out


def test_code_is_left_alone_and_specials_are_escaped():
    md = "Use `a < b` here: x < y & z\n```\nif a < b and **c**:\n    pass\n```"
    out = fmt.to_mrkdwn(md)
    assert "`a < b`" in out
    assert "x &lt; y &amp; z" in out
    assert "if a < b and **c**:" in out


def test_tables_become_monospaced():
    md = "| Job | State |\n|---|---|\n| 123 | RUNNING |\n| 4567 | DONE |"
    out = fmt.to_mrkdwn(md)
    assert out.startswith("```") and out.endswith("```")
    assert "Job   State" in out and "4567  DONE" in out


def test_summary_is_the_first_paragraph_cut_at_a_sentence():
    long = "Frontier is up. " * 40
    summary, rest = fmt.split_summary(long.strip() + "\n\nDetails follow.")
    assert len(summary) <= fmt.SUMMARY_LIMIT
    assert summary.endswith(".")
    assert rest.endswith("Details follow.")


def test_a_summary_never_opens_code():
    summary, rest = fmt.split_summary("```\nlog line\n```\n\nThat was the log.")
    assert summary == "" and rest.startswith("```")
    summary, rest = fmt.split_summary("Here it is: ```\nx\n```")
    assert summary == "Here it is:" and rest.startswith("```")


def test_a_short_answer_has_no_show_more():
    reply = fmt.show_more_button(fmt.answer("Job 123 is running on Lux."), "d1")
    assert reply.detail is None
    assert len(reply.blocks) == 1
    assert reply.text == "Job 123 is running on Lux."


def test_a_long_answer_leads_with_a_summary_and_keeps_the_rest():
    md = "Your job finished.\n\n" + "\n\n".join(
        f"Paragraph {i}: " + "x" * 500 for i in range(200)
    )
    reply = fmt.show_more_button(fmt.answer(md, notes=["Job 42 on Lux"]), "d1")

    assert reply.blocks[0]["text"]["text"] == "Your job finished."
    assert reply.blocks[-1]["elements"][0]["action_id"] == fmt.SHOW_MORE
    assert reply.blocks[-1]["elements"][0]["value"] == "d1"
    assert "Paragraph 199" in reply.detail
    _within_limits(reply)


@pytest.mark.parametrize(
    "detail",
    [
        "y" * 100_000,  # no breaks at all
        "word " * 30_000,
        "```\n" + "\n".join(f"line {i}" for i in range(20_000)) + "\n```",
        "\n\n".join("p" * 4000 for _ in range(30)),
    ],
)
def test_show_more_fits_any_length(detail):
    pieces = fmt.detail_messages(detail)
    assert pieces
    for piece in pieces:
        _within_limits(piece)
    joined = "".join(p.text for p in pieces)
    assert joined.count("```") % 2 == 0


def test_a_split_code_block_is_closed_and_reopened():
    code = "```\n" + "\n".join(f"line {i}" for i in range(2000)) + "\n```"
    pieces = fmt.chunks(code)
    assert len(pieces) > 1
    for piece in pieces:
        assert piece.count("```") % 2 == 0
        assert len(piece) <= fmt.CHUNK_LIMIT


def test_truncate_points_to_the_rest():
    out = fmt.truncate("a " * 5000, 200, "_ask for the rest_")
    assert len(out) <= 200 and out.endswith("_ask for the rest_")


def test_the_plain_fallback_fits():
    reply = fmt.plain("z" * 1_000_000)
    assert len(reply.text) <= fmt.FALLBACK_LIMIT and reply.blocks == []


def test_an_empty_answer_still_posts():
    reply = fmt.answer("")
    assert reply.text and reply.blocks


def _within_limits(reply: fmt.Reply) -> None:
    assert len(reply.text) <= 40_000
    assert len(reply.blocks) <= 50
    for block in reply.blocks:
        if block["type"] == "section":
            assert len(block["text"]["text"]) <= 3000
    assert len(json.dumps(reply.blocks)) < 50_000
