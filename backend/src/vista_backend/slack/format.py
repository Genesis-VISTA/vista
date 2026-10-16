"""
The agent's Markdown as Slack messages that read well on a phone.

Slack has its own markup (mrkdwn) and hard limits: 3,000 characters in a
section, 40,000 in a message's text, 50 blocks. We stay well under them
(`SECTION_LIMIT`, `CHUNK_LIMIT`, `MAX_BLOCKS`), so a reply never fails for its
length. Every reply leads with a summary of a line or two; the rest is kept
for a "Show more" button that posts it in the thread, in as many messages as
it needs.
"""

import re
from dataclasses import dataclass
from typing import Any

SECTION_LIMIT = 2900
CHUNK_LIMIT = 3800
MAX_BLOCKS = 40
SUMMARY_LIMIT = 300
FALLBACK_LIMIT = 3500

SHOW_MORE = "vista_show_more"

_FENCE = re.compile(r"^```.*?$\n?(.*?)^```[ \t]*$", re.M | re.S)
_INLINE_CODE = re.compile(r"`[^`\n]+`")
_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)")
_BARE_URL = re.compile(r"<(https?://[^>\s]+)>")
_BOLD = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1")
_ITALIC_STAR = re.compile(r"(?<![*\w])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![*\w])")
_STRIKE = re.compile(r"~~(?=\S)(.+?)(?<=\S)~~")
_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$", re.M)
_BULLET = re.compile(r"^(\s*)[-*+]\s+", re.M)
_HR = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$", re.M)
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
_SENTENCE_END = re.compile(r"[.!?](?=\s|$)")


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _tables_to_code(text: str) -> str:
    """Markdown tables become monospaced blocks: Slack has no tables."""
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        if (
            _TABLE_ROW.match(lines[i])
            and i + 1 < len(lines)
            and _TABLE_RULE.match(lines[i + 1])
        ):
            rows: list[list[str]] = []
            while i < len(lines) and _TABLE_ROW.match(lines[i]):
                if not _TABLE_RULE.match(lines[i]):
                    rows.append(
                        [c.strip() for c in lines[i].strip().strip("|").split("|")]
                    )
                i += 1
            width = max(len(r) for r in rows)
            rows = [r + [""] * (width - len(r)) for r in rows]
            sizes = [max(len(r[c]) for r in rows) for c in range(width)]
            rendered = [
                "  ".join(cell.ljust(sizes[c]) for c, cell in enumerate(r)).rstrip()
                for r in rows
            ]
            out.append("```\n" + "\n".join(rendered) + "\n```")
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


def _prose(text: str) -> str:
    """mrkdwn for text outside code."""
    keep: list[str] = []

    def hold(value: str) -> str:
        keep.append(value)
        return f"\x00{len(keep) - 1}\x00"

    text = _INLINE_CODE.sub(lambda m: hold(m.group(0)), text)
    text = _LINK.sub(lambda m: hold(f"<{m.group(2)}|{_escape(m.group(1))}>"), text)
    text = _BARE_URL.sub(lambda m: hold(f"<{m.group(1)}>"), text)
    text = _escape(text)
    text = _HR.sub("───", text)
    text = _HEADING.sub(lambda m: hold(f"*{m.group(1).strip('*')}*"), text)
    text = _BULLET.sub(lambda m: f"{m.group(1)}• ", text)
    text = _ITALIC_STAR.sub(lambda m: hold(f"_{m.group(1)}_"), text)
    text = _BOLD.sub(lambda m: hold(f"*{m.group(2)}*"), text)
    text = _STRIKE.sub(lambda m: f"~{m.group(1)}~", text)
    while "\x00" in text:
        text = re.sub(r"\x00(\d+)\x00", lambda m: keep[int(m.group(1))], text)
    return text


def to_mrkdwn(markdown: str) -> str:
    """Slack mrkdwn for the agent's Markdown. Code is left exactly as it is."""
    text = _tables_to_code(markdown.replace("\r\n", "\n"))
    out: list[str] = []
    last = 0
    for match in _FENCE.finditer(text):
        out.append(_prose(text[last : match.start()]))
        out.append("```\n" + match.group(1).rstrip("\n") + "\n```")
        last = match.end()
    out.append(_prose(text[last:]))
    return "".join(out).strip()


def _cut(text: str, limit: int) -> int:
    """Where to cut `text` at or before `limit`: a paragraph, line, sentence or word end."""
    if len(text) <= limit:
        return len(text)
    window = text[:limit]
    for sep in ("\n\n", "\n"):
        at = window.rfind(sep)
        if at > limit // 2:
            return at
    ends = [m.end() for m in _SENTENCE_END.finditer(window)]
    if ends and ends[-1] > limit // 2:
        return ends[-1]
    at = window.rfind(" ")
    return at if at > limit // 2 else limit


def split_summary(text: str, limit: int = SUMMARY_LIMIT) -> tuple[str, str]:
    """
    The first paragraph, no longer than `limit`, and everything else.

    A summary never opens a code block it does not close: an answer that
    starts with code is summarised by the line before it, or by nothing.
    """
    text = text.strip()
    if not text:
        return "", ""
    first, _, rest = text.partition("\n\n")
    if first.startswith("```"):
        return "", text
    if "```" in first:
        first, fence, tail = first.partition("```")
        rest = (fence + tail + ("\n\n" + rest if rest else "")).strip()
        first = first.strip()
    if len(first) > limit:
        at = _cut(first, limit)
        rest = (first[at:].strip() + ("\n\n" + rest if rest else "")).strip()
        first = first[:at].strip()
    return first, rest.strip()


def chunks(text: str, limit: int = CHUNK_LIMIT) -> list[str]:
    """
    `text` in pieces no longer than `limit`, cut at natural ends. A code block
    split across pieces is closed at the end of one and reopened in the next.
    """
    pieces: list[str] = []
    rest = text.strip()
    while rest:
        at = _cut(rest, limit - 8)
        piece, rest = rest[:at].rstrip(), rest[at:].lstrip("\n")
        if piece.count("```") % 2 == 1:
            piece += "\n```"
            rest = "```\n" + rest
        pieces.append(piece)
    return pieces


def truncate(text: str, limit: int, pointer: str) -> str:
    """`text`, or as much of it as fits with `pointer` after it."""
    if len(text) <= limit:
        return text
    at = _cut(text, max(limit - len(pointer) - 8, 1))
    head = text[:at].rstrip()
    if head.count("```") % 2 == 1:
        head += "\n```"
    return f"{head}\n…\n{pointer}"


def section(text: str) -> dict[str, Any]:
    return {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": truncate(text, SECTION_LIMIT, "_(cut short)_"),
        },
    }


def context(text: str) -> dict[str, Any]:
    return {
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": truncate(text, SECTION_LIMIT, "…")}],
    }


def button(
    text: str, action_id: str, value: str, style: str | None = None
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "type": "button",
        "text": {"type": "plain_text", "text": text[:75], "emoji": True},
        "action_id": action_id,
        "value": value[:2000],
    }
    if style:
        out["style"] = style
    return out


@dataclass(frozen=True)
class Reply:
    """A message ready to post: `text` is the notification and fallback text."""

    text: str
    blocks: list[dict[str, Any]]
    detail: str | None = None
    """ What "Show more" posts; the caller stores it and puts its id on the button. """


def answer(markdown: str, *, notes: list[str] | None = None) -> Reply:
    """
    The agent's final answer as a summary-first reply.

    `notes` are short lines shown under the answer (warnings, the job id). When
    there is more than the summary, `detail` holds it and the reply carries a
    "Show more" button whose value the caller fills in with `show_more_button`.
    """
    text = to_mrkdwn(markdown) or "_(no answer)_"
    summary, detail = split_summary(text)
    if not summary:
        summary = "Here's the answer." if detail else text
    blocks = [section(summary)]
    for note in (notes or [])[:3]:
        blocks.append(context(note))
    return Reply(
        text=truncate(summary, FALLBACK_LIMIT, ""),
        blocks=blocks,
        detail=detail or None,
    )


def show_more_button(reply: Reply, detail_id: str) -> Reply:
    if reply.detail is None:
        return reply
    actions = {
        "type": "actions",
        "elements": [button("Show more", SHOW_MORE, detail_id)],
    }
    return Reply(text=reply.text, blocks=[*reply.blocks, actions], detail=reply.detail)


def detail_messages(detail: str) -> list[Reply]:
    """The text behind "Show more", as messages that each fit Slack's limits."""
    replies: list[Reply] = []
    for piece in chunks(detail):
        sections = [section(p) for p in chunks(piece, SECTION_LIMIT)][:MAX_BLOCKS]
        replies.append(
            Reply(text=truncate(piece, FALLBACK_LIMIT, "…"), blocks=sections)
        )
    return replies


def plain(text: str) -> Reply:
    """A reply with no blocks, truncated to fit: the fallback when blocks are refused."""
    return Reply(
        text=truncate(text, FALLBACK_LIMIT, "_(cut short; ask me for the rest)_"),
        blocks=[],
    )
