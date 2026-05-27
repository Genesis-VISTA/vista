from pathlib import Path
from typing import Annotated as A
from pydantic import AfterValidator

def _validate_resolved_path(path: str | Path):
    path = Path(path).expanduser()
    return (Path.cwd() / path).resolve()

ResolvedPath = A[Path, AfterValidator(_validate_resolved_path)]
""" Resolve a path, and expand ~ in the path string. """
