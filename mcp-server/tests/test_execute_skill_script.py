import pytest
from pathlib import Path
from vista.tools.execute_skill_script import process_command

HOME = Path.home()
CWD = Path.cwd()

@pytest.mark.parametrize("input,expected", [
    (
        f"{HOME}/.agents/skills/foo/scripts/foo.py",
        [f"{HOME}/.agents/skills/foo/scripts/foo.py"],
    ), (
        f"python .agents/skills/foo.py --arg 1",
        [f"{CWD}/.agents/skills/foo.py", '--arg', '1'],
    ), (
        f"python3 .agents/skills/foo.py --arg 1",
        [f"{CWD}/.agents/skills/foo.py", '--arg', '1'],
    ), (
        f".goose/skills/foo.py --arg='1 2'; 3",
        [f"{CWD}/.goose/skills/foo.py", '--arg=1 2;', '3'],
    ), (
        f"{HOME}/.agents/skills/foo/scripts/nested/foo.py",
        [f"{HOME}/.agents/skills/foo/scripts/nested/foo.py"],
    ),
])
def test_process_command(input, expected):
    assert process_command(input) == expected


@pytest.mark.parametrize("input", [
    "/my/script.py",
    f"{HOME}/.agents/skills/foo/../../../../foo.py",
    "python /my/script.py",
    ".agents/skills/script.py --name 'unclosed",
])
def test_process_command_error(input):
    with pytest.raises(ValueError):
        process_command(input)

