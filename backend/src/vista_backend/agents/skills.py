"""
AgentSkills spec implementation

Basically a port of https://github.com/agentskills/agentskills/blob/main/skills-ref
"""

import html
from typing import Annotated as A
from pathlib import Path
from pydantic import BaseModel, Field
from typing import Iterable
import logging
import textwrap
import yaml
from ..utils.types import StrippedStr


class SkillError(Exception):
    pass


class ParseError(SkillError):
    pass


class SkillMetadata(BaseModel):
    """
    AgentSkills-spec properties parsed from a skill's SKILL.md frontmatter.

    Attributes:
        name: Skill name in kebab-case (required)
        description: What the skill does and when the model should use it (required)
        license: License for the skill (optional)
        compatibility: Compatibility information for the skill (optional)
        allowed_tools: Tool patterns the skill requires (optional, experimental)
        metadata: Key-value pairs for client-specific properties (optional).
    """

    name: A[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]
    description: StrippedStr
    license: str | None = None
    compatibility: str | None = None
    allowed_tools: str | None = None
    metadata: dict[str, str | list[str]] | None = None


class Skill(SkillMetadata):
    body: str


def find_skill_md(skill_dir: Path | str) -> Path | None:
    """
    Find the SKILL.md file in a skill directory.

    Prefers SKILL.md (uppercase) but accepts skill.md (lowercase).

    Args:
        skill_dir: Path to the skill directory

    Returns:
        Path to the SKILL.md file, or None if not found
    """
    for name in ("SKILL.md", "skill.md"):
        path = Path(skill_dir) / name
        if path.exists():
            return path
    return None


def find_skills(search_paths: Iterable[Path | str]) -> list[Path]:
    """
    Find all skills in the skill directories.
    """
    results: list[Path] = []
    for search_path in search_paths:
        search_path = Path(search_path).resolve()
        if not search_path.is_dir():  # Ignore search_path dirs if they don't exist
            continue
        for entry in sorted(search_path.iterdir()):
            if not entry.is_dir():  # Ignore non dirs under a skills folder
                continue
            if find_skill_md(entry) is not None:
                results.append(entry)
    return results


def parse_skill(content: str) -> Skill:
    """
    Parse SKILL.md text (YAML frontmatter + markdown body) into a `Skill`.

    Raises:
        ParseError: If the frontmatter is missing, malformed, or not a mapping.
        ValidationError: If required fields (name, description) are missing.
    """
    if not content.startswith("---"):
        raise ParseError("SKILL.md must start with YAML frontmatter (---)")
    parts = content.split("---", 2)
    if len(parts) < 3:
        raise ParseError("SKILL.md frontmatter not properly closed with ---")
    frontmatter_str = parts[1]
    body = parts[2].strip()

    try:
        frontmatter_dict = yaml.safe_load(frontmatter_str)
    except yaml.YAMLError as e:
        raise ParseError(f"Invalid YAML in frontmatter: {e}") from e
    if not isinstance(frontmatter_dict, dict):
        raise ParseError("SKILL.md frontmatter must be a YAML mapping")

    frontmatter_dict["body"] = body
    return Skill.model_validate(frontmatter_dict)


def read_skill(skill_dir: Path | str) -> Skill:
    """
    Read skill properties from a directory's SKILL.md.

    Args:
        skill_dir: Path to the skill directory

    Returns:
        Parsed `Skill`

    Raises:
        ParseError: If SKILL.md is missing or has invalid YAML
        ValidationError: If required fields (name, description) are missing
    """
    skill_dir = Path(skill_dir).resolve()
    skill_md = find_skill_md(skill_dir)
    if skill_md is None:
        raise ParseError(f"SKILL.md not found in {skill_dir}")
    return parse_skill(skill_md.read_text())


def skill_to_markdown(skill: Skill) -> str:
    """
    Serialize a `Skill` to a SKILL.md document: generated YAML frontmatter (every
    spec field that has a value, in spec order) followed by the markdown body.
    """
    fm: dict[str, object] = {"name": skill.name, "description": skill.description}
    if skill.license is not None:
        fm["license"] = skill.license
    if skill.compatibility is not None:
        fm["compatibility"] = skill.compatibility
    if skill.allowed_tools is not None:
        fm["allowed_tools"] = skill.allowed_tools
    if skill.metadata:
        fm["metadata"] = skill.metadata

    frontmatter_text = yaml.safe_dump(
        fm, sort_keys=False, allow_unicode=True, width=1000
    )
    return f"---\n{frontmatter_text}---\n\n{skill.body.strip()}\n"


def write_skill(skill_dir: Path | str, skill: Skill) -> Skill:
    """
    Create a new skill on disk at `skill_dir/SKILL.md`, generating the
    frontmatter from `skill`'s spec fields.

    The directory must not already exist.

    Returns the parsed `Skill` after writing (round-trip validates the result).

    Raises:
        FileExistsError: If `skill_dir` already exists.
    """
    skill_dir = Path(skill_dir).resolve()
    skill_dir.mkdir(parents=True, exist_ok=False)
    (skill_dir / "SKILL.md").write_text(skill_to_markdown(skill))
    return read_skill(skill_dir)


def to_prompt(
    skill_dirs: list[Path | str],
    path_mapping: dict[Path | str, Path | str] | None = None,
) -> str:
    """
    Generate the <available_skills> XML block for inclusion in agent prompts.

    This XML format is what Anthropic uses and recommends for Claude models.
    Skill Clients may format skill information differently to suit their
    models or preferences.

    Args:
        skill_dirs: List of paths to skill directories
        path_mapping: Show a different path in the prompt than the skill_dirs passed in (so the prompt so the path in the container)

    Returns:
        XML string with <available_skills> block containing each skill's
        name, description, and location.

    Example output:
        <available_skills>
        <skill>
        <name>pdf-reader</name>
        <description>Read and extract text from PDF files</description>
        <location>/path/to/pdf-reader/SKILL.md</location>
        </skill>
        </available_skills>
    """
    if not skill_dirs:
        return ""
    path_mapping = {
        Path(k).resolve(): Path(v).resolve() for k, v in (path_mapping or {}).items()
    }

    # instructions for how to use skills for models not pretrained with them
    SKILL_INSTRUCTIONS = textwrap.dedent("""
        The `<available_skills>` block lists skills you can use. When a user's request matches a 
        skill's description, read the SKILL.md file first and follow the instructions inside it. 
        SKILL.md may reference other potentially relevant documentation and scripts to use. If 
        multiple skills are relevant use all of them.
    """).strip()

    lines = [SKILL_INSTRUCTIONS, "<available_skills>"]
    for skill_dir in skill_dirs:
        try:
            skill = read_skill(skill_dir)
        except SkillError as e:
            logging.warning(f"Failed to parse skill {skill_dir}: {e}")
            continue
        skill_md = find_skill_md(skill_dir)
        if not skill_md:
            logging.warning(f"No skill {skill_dir} found")
            continue
        skill_md = skill_md.resolve()
        for prefix, replacement in (path_mapping or {}).items():
            if skill_md.is_relative_to(prefix):
                skill_md = replacement / skill_md.relative_to(prefix)
                break
        lines.extend(
            [
                "<skill>",
                "<name>",
                html.escape(skill.name),
                "</name>",
                "<description>",
                html.escape(skill.description),
                "</description>",
                "<location>",
                html.escape(str(skill_md)),
                "</location>",
                "</skill>",
            ]
        )
    lines.append("</available_skills>")

    return "\n".join(lines)
