from pydantic_ai.messages import ModelMessage

_store: dict[str, list[ModelMessage]] = {}


def get_history(conversation_id: str) -> list[ModelMessage]:
    return _store.get(conversation_id, [])


def set_history(conversation_id: str, messages: list[ModelMessage]) -> None:
    _store[conversation_id] = messages
