"""No bundled skill or project system prompt may claim that printing a path displays a file.

`display_file` is the only way a file is shown to the user; instructions that say
the UI auto-displays plots would lead the agent to skip it.
"""

import re
from pathlib import Path

import pytest

import vista_backend.db as db_pkg

DB_DIR = Path(db_pkg.__file__).parent

AUTO_DISPLAY = re.compile(
    r"auto-?display|automatically\s+display(?:ed|s)?|so\s+the\s+UI\b[^.\n]*display"
    r"|do\s+not\s+call\s+any\s+display",
    re.IGNORECASE,
)


def _instruction_files():
    return sorted(
        [*DB_DIR.glob("skills/*/SKILL.md"), *DB_DIR.glob("system_prompts/*.md")]
    )


def test_bundled_instructions_found():
    files = _instruction_files()
    assert any(p.name == "SKILL.md" for p in files)
    assert any(p.parent.name == "system_prompts" for p in files)


@pytest.mark.parametrize(
    "path", _instruction_files(), ids=lambda p: f"{p.parent.name}/{p.name}"
)
def test_instructions_do_not_claim_auto_display(path):
    text = path.read_text(encoding="utf-8")
    assert not AUTO_DISPLAY.search(text), f"{path} claims auto-display"
