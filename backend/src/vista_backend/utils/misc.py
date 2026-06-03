from typing import Any
from pydantic import TypeAdapter
from pathlib import Path
import fnmatch

def write_file_unique(path: Path | str, data: bytes) -> Path:
    """
    Write a new file, making sure the filename is unique. If it already exists, suffix it with -1,
    -2, etc.
    """
    path = Path(path)
    suffixes = "".join(path.suffixes)
    stem = path.name.removesuffix(suffixes)
    i = 0
    while True:
        candidate = path if i == 0 else path.with_name(f"{stem}-{i}{suffixes}")
        try:
            with open(candidate, "xb") as f:
                f.write(data)
            return candidate
        except FileExistsError:
            i += 1

def path_is_under(base: Path | str, target: Path | str) -> bool:
    base = Path(base).resolve()
    target = Path(target).resolve()
    return target.is_relative_to(base) and target != base


def json_dump_if(data: Any) -> str:
    """ If data is a string, just return it. Otherwise jsonize it """
    if isinstance(data, str):
        return data
    else:
        return TypeAdapter(Any).dump_json(data).decode()

def tool_allowed(name: str, patterns: list[str]) -> bool:
    """
    Match `name` against fnmatch-style `patterns`.

    Entries beginning with `!` are deny patterns; everything else is an
    allow pattern. A tool is allowed iff at least one allow pattern matches
    and no deny pattern matches. With no allow patterns, allow `*`.
    """
    allow_patterns = [p for p in patterns if not p.startswith("!")]
    if not allow_patterns:
        allow_patterns = ['*']
    deny_patterns = [p[1:] for p in patterns if p.startswith("!")]

    if not any(fnmatch.fnmatchcase(name, p) for p in allow_patterns):
        return False
    if any(fnmatch.fnmatchcase(name, p) for p in deny_patterns):
        return False
    return True
