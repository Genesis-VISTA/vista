"""The UI's tour finds the default project and KB through `ui/lib/default-project.ts`.

The UI can't import the seed, so a rename on either side would silently break the
tour. This reads the TypeScript constants and compares them with the seed's.
"""

import re
from pathlib import Path

import pytest

from vista_backend.db.seed import HELLO_OLCF_PROJECT_NAME, OLCF_DOCS_SLUG

UI_CONSTANTS = Path(__file__).resolve().parents[2] / "ui" / "lib" / "default-project.ts"


def ui_constants(source: str) -> dict[str, str]:
    """Exported string constants: `export const NAME = "value";`."""
    return dict(
        re.findall(r'^export const (\w+)\s*=\s*"([^"]*)"\s*;', source, re.MULTILINE)
    )


def mismatches(source: str) -> list[str]:
    expected = {
        "HELLO_OLCF_PROJECT_NAME": HELLO_OLCF_PROJECT_NAME,
        "OLCF_DOCS_SLUG": OLCF_DOCS_SLUG,
    }
    found = ui_constants(source)
    return [
        f"{name}: ui has {found.get(name)!r}, seed has {value!r}"
        for name, value in expected.items()
        if found.get(name) != value
    ]


def test_ui_default_project_constants_match_the_seed() -> None:
    source = UI_CONSTANTS.read_text(encoding="utf-8")
    assert mismatches(source) == []


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (f'"{HELLO_OLCF_PROJECT_NAME}"', '"hello-renamed"'),
        (f'"{OLCF_DOCS_SLUG}"', '"docs-renamed"'),
    ],
)
def test_a_rename_on_the_ui_side_is_caught(old: str, new: str) -> None:
    source = UI_CONSTANTS.read_text(encoding="utf-8")
    assert old in source
    assert mismatches(source.replace(old, new)) != []
