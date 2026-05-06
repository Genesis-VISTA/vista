from pathlib import Path
from typing import Annotated as A, Literal
from pydantic import AfterValidator, BeforeValidator

def _validate_resolved_path(path: str | Path):
    path = Path(path).expanduser()
    return (Path.cwd() / path).resolve()

ResolvedPath = A[Path, AfterValidator(_validate_resolved_path)]
StrippedStr = A[str, AfterValidator(lambda s: s.strip())]

LogLevel = A[
    Literal["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"],
    BeforeValidator(lambda s: s.upper() if isinstance(s, str) else s),
]
