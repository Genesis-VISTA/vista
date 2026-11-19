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


def test_code_keeps_its_markup_and_specials_are_escaped():
    md = "Use `a < b` here: x < y & z\n```\nif a < b and **c**:\n    pass\n```"
    out = fmt.to_mrkdwn(md)
    assert "`a &lt; b`" in out, "escaped in code too, as Slack requires"
    assert "x &lt; y &amp; z" in out
    assert "if a &lt; b and **c**:" in out, "no bold inside code"


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


def test_the_project_picker_is_buttons_then_a_menu_past_five():
    few = fmt.project_picker("Which?", "ask1", [("p1", "salts"), ("p2", "runs")])
    buttons = few.blocks[-1]["elements"]
    assert [b["type"] for b in buttons] == ["button", "button"]
    assert len({b["action_id"] for b in buttons}) == 2, "unique within the block"
    assert all(b["action_id"].startswith(fmt.PICK_PROJECT) for b in buttons)
    assert buttons[1]["value"] == "ask1:p2"

    many = fmt.project_picker("Which?", "ask1", [(f"p{i}", f"n{i}") for i in range(7)])
    (menu,) = many.blocks[-1]["elements"]
    assert menu["type"] == "static_select"
    assert menu["action_id"].startswith(fmt.PICK_PROJECT)
    assert [o["value"] for o in menu["options"]][-1] == "ask1:p6"


# ---------------------------------------------------------------------------
# 7.1: inputs that used to hang, and code that went to Slack unescaped
# ---------------------------------------------------------------------------


def _finishes(fn, *args, seconds: float = 2.0):
    """Run `fn` in a thread; fail instead of hanging the suite if it never returns."""
    import threading

    out: list = []
    worker = threading.Thread(target=lambda: out.append(fn(*args)), daemon=True)
    worker.start()
    worker.join(seconds)
    assert not worker.is_alive(), f"{fn.__name__} did not finish"
    return out[0]


def test_chunks_finish_on_an_unclosed_code_fence():
    text = "Here is the script:\n\n```bash\n" + "echo step\n" * 2000

    pieces = _finishes(fmt.chunks, text)

    assert pieces and all(len(p) <= fmt.CHUNK_LIMIT for p in pieces)
    assert all(p.count("```") % 2 == 0 for p in pieces), "each piece's code is closed"
    assert _finishes(fmt.chunks, "```") == []
    assert _finishes(fmt.chunks, "text\n```") == ["text\n```\n```"]


def test_a_stray_nul_does_not_hang_the_conversion():
    out = _finishes(fmt.to_mrkdwn, "**bold**\x00 and \x00 `code`\x00")
    assert "\x00" not in out and "*bold*" in out and "`code`" in out


def test_code_is_escaped_too():
    out = fmt.to_mrkdwn(
        "Run `a && b <in >out`:\n\n```\nif x < 1 && y > 2: <@U123>\n```"
    )
    assert "`a &amp;&amp; b &lt;in &gt;out`" in out
    assert "if x &lt; 1 &amp;&amp; y &gt; 2: &lt;@U123&gt;" in out
    assert "<@U123>" not in out, "code cannot mention anyone"
