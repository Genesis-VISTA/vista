from pathlib import Path

def write_file_unique(path: Path|str, bytes: bytes) -> Path:
    """
    Writes a file. If `path` already exists, renames it with -{i} suffix to ensure a unique file
    """
    path = Path(path)
    stem = path.stem
    i = 1
    while path.exists():
        path = path.with_stem(f"{stem}-{i}")
        i += 1
    path.write_bytes(bytes)
    return path


def path_is_under(base: Path | str, target: Path | str) -> bool:
    base = Path(base).resolve()
    target = Path(target).resolve()
    return target.is_relative_to(base) and target != base
