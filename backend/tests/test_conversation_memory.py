import uuid

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from vista_backend.db.schemas import ChatSessionTable
from vista_backend.services import conversation_memory


def _row(**updates) -> ChatSessionTable:
    values = {
        "project_id": uuid.uuid4(),
        "memory_enabled": True,
        "memory_context_size": "compact",
        "memory_summary": None,
        "memory_archive": [],
        "memory_archived_until": 0,
    }
    values.update(updates)
    return ChatSessionTable(**values)


def _turn(prompt: str, answer: str) -> list:
    return [
        ModelRequest(parts=[UserPromptPart(content=prompt)]),
        ModelResponse(parts=[TextPart(content=answer)]),
    ]


@pytest.mark.anyio
async def test_disabled_memory_sends_no_prior_history():
    history = _turn("earlier question", "earlier answer")

    context = await conversation_memory.model_history(
        _row(memory_enabled=False), history, "new question"
    )

    assert context == []


@pytest.mark.anyio
async def test_compression_archives_only_complete_old_turns():
    history = [
        *_turn("first " + "x" * 17_000, "first result"),
        *_turn("second", "second result"),
        *_turn("third", "third result"),
    ]

    async def summarize(source: str) -> str:
        assert "first result" in source
        assert "second result" not in source
        return "The first result was retained."

    update = await conversation_memory.compact_history(_row(), history, summarize)

    assert update is not None
    assert update.archived_until == 2
    assert len(update.archive) == 1
    assert "first result" in update.archive[0]["text"]
    assert update.summary == "The first result was retained."


@pytest.mark.anyio
async def test_context_combines_summary_retrieval_and_recent_exact_messages(
    monkeypatch,
):
    history = [
        *_turn("old", "old result"),
        *_turn("recent", "recent result"),
    ]
    row = _row(
        memory_summary="The objective is a salt analysis.",
        memory_archive=[{"id": "0:2", "text": "density was 2.1"}],
        memory_archived_until=2,
    )

    async def retrieve(_row, query, limit):
        assert _row.id == row.id
        assert query == "what was the density?"
        assert limit == 2
        return ["density was 2.1"]

    monkeypatch.setattr(conversation_memory, "_retrieve", retrieve)
    context = await conversation_memory.model_history(
        row, history, "what was the density?"
    )

    assert len(context) == 4
    memory_text = context[0].parts[0].content
    assert "salt analysis" in memory_text
    assert "density was 2.1" in memory_text
    assert context[2:] == history[2:]
