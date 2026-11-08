"""
The release workflow's data-compatibility step (`.github/scripts/data-compat.sh`).

    python -m vista_backend.db.release_schema older <version> <tag>...
    python -m vista_backend.db.release_schema check <database>
    python -m vista_backend.db.release_schema verdict <version> <tag>=<pass|fail>...

`older` prints the tags of releases before `<version>`, newest first, one per
line, leaving out any that no release could carry (see `is_dev_build`).
`check` runs the startup check (`schema_problems` as `init_db` runs it) against
an earlier release's empty database: exit 0 when this code would open it, 1
with the problems on stderr when it would ask for a reset. `verdict` turns the
walk's results, newest first and ending at the first refusal, into the
`update.json` body on stdout.
"""

import json
import sqlite3
import sys
from pathlib import Path

from ..services.update_check import _tag_version, is_dev_build, parse_version
from .schema_check import schema_problems


def older(version: str, tags: list[str]) -> list[str]:
    """The release tags before `version`, newest first."""
    current = parse_version(version)
    if current is None:
        raise ValueError(f"not a release version: {version}")
    found = []
    for tag in tags:
        name = _tag_version(tag)
        if is_dev_build(name):
            continue
        parsed = parse_version(name)
        if parsed is not None and parsed < current:
            found.append((parsed, tag))
    return [tag for _, tag in sorted(found, reverse=True)]


def problems(database: Path) -> list[str]:
    """What the startup check refuses in `database`; empty when it would open it."""
    if not database.is_file():
        raise FileNotFoundError(database)
    conn = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        return schema_problems(conn, assume_additive=True)
    finally:
        conn.close()


def verdict(version: str, results: list[tuple[str, bool]]) -> dict[str, str]:
    """
    `update.json` from the walk: `results` are earlier tags newest first, each
    with whether this code opens its database, ending at the first refusal.
    The oldest one accepted is `data_compatible_from`; with none accepted, this
    version itself, so every earlier install resets.
    """
    oldest = version
    for tag, accepted in results:
        if not accepted:
            break
        oldest = _tag_version(tag)
    return {"data_compatible_from": oldest}


def main(argv: list[str]) -> int:
    match argv:
        case ["older", version, *tags]:
            for tag in older(version, tags):
                print(tag)
            return 0
        case ["check", database]:
            found = problems(Path(database))
            for problem in found:
                print(problem, file=sys.stderr)
            return 1 if found else 0
        case ["verdict", version, *pairs]:
            results = []
            for pair in pairs:
                tag, _, outcome = pair.rpartition("=")
                if outcome not in ("pass", "fail") or not tag:
                    raise SystemExit(f"not <tag>=<pass|fail>: {pair}")
                results.append((tag, outcome == "pass"))
            print(json.dumps(verdict(version, results)))
            return 0
    print((__doc__ or "").strip(), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
