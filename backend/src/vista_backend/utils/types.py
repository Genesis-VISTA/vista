import json
from pathlib import Path
from typing import Annotated as A, Literal, TypeVar
from pydantic import AfterValidator, BeforeValidator
from pydantic_settings import NoDecode
import httpx


def _validate_resolved_path(path: str | Path):
    path = Path(path).expanduser()
    return (Path.cwd() / path).resolve()


ResolvedPath = A[Path, AfterValidator(_validate_resolved_path)]
StrippedStr = A[str, AfterValidator(lambda s: s.strip())]

LogLevel = A[
    Literal["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"],
    BeforeValidator(lambda s: s.upper() if isinstance(s, str) else s),
]


def _split(data: str) -> list[str]:
    return [item.strip() for item in data.split(",") if item.strip()]


def _validate_comma_separated_list(data):
    if isinstance(data, list):
        items = []
        for item in data:
            if isinstance(item, str):
                items.extend(_split(item))
            else:
                items.append(item)
        return items
    if isinstance(data, str) and data.strip().startswith("["):
        return json.loads(data)
    if isinstance(data, str):
        return _split(data)
    return data


T = TypeVar("T")

CommaSeparatedList = A[
    list[T],
    BeforeValidator(_validate_comma_separated_list),
    NoDecode,  # so env vars aren't parsed as JSON before the validator sees them
]
"""
A list read from `a,b` or `["a", "b"]`. The same parsing as the MCP server's
type of this name, so a `VISTA_MCP_*` list setting means the same in both.
"""


def _validate_http_url(v: str) -> str:
    """
    An `http(s)://` address with a host, without a trailing slash so paths join
    onto it one way. Parsed by httpx, which is what will send to it: an address
    `urlparse` accepts can still be one httpx refuses (`http://host:abc`).
    """
    v = v.strip().rstrip("/")
    try:
        url = httpx.URL(v)
    except httpx.InvalidURL:
        url = None
    if url is None or url.scheme not in ("http", "https") or not url.host:
        raise ValueError("enter the server's address, like http://127.0.0.1:8002")
    return v


HttpUrl = A[str, AfterValidator(_validate_http_url)]
"""
A server address: the collaboration server's, in Settings and in the
configuration alike, so both are held to the same rule.
"""

OptionalHttpUrl = A[
    HttpUrl | None,
    BeforeValidator(lambda v: None if isinstance(v, str) and not v.strip() else v),
]
""" An `HttpUrl` or nothing: blank, or only whitespace, means nothing. """
