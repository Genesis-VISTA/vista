import json
from pathlib import Path
from typing import Annotated as A, TypeVar
from pydantic import AfterValidator, BeforeValidator
from pydantic_settings.sources.types import NoDecode

def _validate_resolved_path(path: str | Path):
    path = Path(path).expanduser()
    return (Path.cwd() / path).resolve()

ResolvedPath = A[Path, AfterValidator(_validate_resolved_path)]
""" Resolve a path, and expand ~ in the path string. """


def smart_split(data: str, sep = ","):
    """ Split on string on sep (by default ","), ignoring trailing seperators and whitespace """
    return [item.strip() for item in data.split(sep) if item.strip()]


def _validate_comma_separated_list(data):
    if isinstance(data, list):
        items = []
        for item in data:
            if isinstance(item, str):
                items.extend(smart_split(item, ','))
            else:
                items.append(item)
        return items
    elif isinstance(data, str) and data.strip().startswith("["):
        return json.loads(data)
    elif isinstance(data, str):
        return smart_split(data, ',')
    else:
        return data

T = TypeVar("T")

CommaSeparatedList = A[
    list[T],
    BeforeValidator(_validate_comma_separated_list),
    NoDecode, # So env vars aren't parsed as JSON before calling our BeforeValidator
]
