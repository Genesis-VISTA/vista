"""
AgentSkills spec implementation

Basically a port of https://github.com/agentskills/agentskills/blob/main/skills-ref
"""
import html
import re
from pathlib import Path
from pydantic import BaseModel
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
    Properties parsed from a skill's SKILL.md frontmatter.

    Attributes:
        path: Full path to the skill.md file.
        name: Skill name in kebab-case (required)
        description: What the skill does and when the model should use it (required)
        license: License for the skill (optional)
        compatibility: Compatibility information for the skill (optional)
        allowed_tools: Tool patterns the skill requires (optional, experimental)
        metadata: Key-value pairs for client-specific properties (defaults to
            empty dict; omitted from to_dict() output when empty)
        author: Display name (and optional contact) of the skill author.
        repo_url: External URL where the skill's source lives (GitHub repo, etc.).
        is_public: If true the skill is listed on the Skill Hub; private skills
            are still installed locally but hidden from hub browsing.
    """
    name: StrippedStr
    description: StrippedStr
    license: str | None = None
    compatibility: str | None = None
    allowed_tools: str | None = None
    metadata: dict[str, str | list[str]] | None = None
    tags: list[str] = []
    author: str | None = None
    repo_url: str | None = None
    is_public: bool = False

class Skill(SkillMetadata):
    body: str


def find_skill_md(skill_dir: Path|str) -> Path | None:
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


def find_skills(search_paths: Iterable[Path|str]) -> list[Path]:
    """
    Find all skills in the skill directories.
    """
    results: list[Path] = []
    for search_path in search_paths:
        search_path = Path(search_path).resolve()
        if not search_path.is_dir(): # Ignore search_path dirs if they don't exist
            continue
        for entry in sorted(search_path.iterdir()):
            if not entry.is_dir(): # Ignore non dirs under a skills folder
                continue
            if find_skill_md(entry) is not None:
                results.append(entry)
    return results


def read_skill(skill_dir: Path|str) -> Skill:
    """
    Read skill properties from SKILL.md frontmatter.

    Args:
        skill_dir: Path to the skill directory

    Returns:
        SkillProperties with parsed metadata

    Raises:
        ParseError: If SKILL.md is missing or has invalid YAML
        ValidationError: If required fields (name, description) are missing
    """
    skill_dir = Path(skill_dir).resolve()
    skill_md = find_skill_md(skill_dir)
    if skill_md is None:
        raise ParseError(f"SKILL.md not found in {skill_dir}")
    
    content = skill_md.read_text()
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
    
    frontmatter_dict['path'] = skill_md
    frontmatter_dict['body'] = body
    skill_parsed = Skill.model_validate(frontmatter_dict)
    return skill_parsed


_FRONTMATTER_RE = re.compile(r"\A---\n(.*?\n)---\n?(.*)\Z", re.DOTALL)


def update_skill_frontmatter(skill_dir: Path | str, updates: dict[str, object]) -> Skill:
    """
    Patch top-level keys in an existing SKILL.md's frontmatter, preserving the
    body and the formatting of unrelated frontmatter keys.

    For each key in `updates`: if a top-level `key: ...` line already exists in
    the frontmatter, its value is rewritten; otherwise the key is appended just
    before the closing `---`. Values are YAML-emitted in flow style so booleans
    serialize as `true`/`false` and strings get quoted as needed.

    Args:
        skill_dir: Path to the skill directory.
        updates: Mapping of frontmatter key -> new value.

    Returns:
        The freshly parsed `Skill` after the rewrite.

    Raises:
        ParseError: If SKILL.md is missing or its frontmatter can't be located.
    """
    skill_dir = Path(skill_dir).resolve()
    skill_md = find_skill_md(skill_dir)
    if skill_md is None:
        raise ParseError(f"SKILL.md not found in {skill_dir}")

    content = skill_md.read_text()
    match = _FRONTMATTER_RE.match(content)
    if not match:
        raise ParseError("SKILL.md frontmatter not properly delimited with ---")
    frontmatter_text = match.group(1)
    rest = match.group(2)

    for key, value in updates.items():
        serialized = yaml.safe_dump({key: value}, default_flow_style=True).strip()
        # safe_dump emits e.g. "{is_public: true}" in flow style; strip the braces.
        if serialized.startswith("{") and serialized.endswith("}"):
            serialized = serialized[1:-1].strip()
        key_line_re = re.compile(rf"(?m)^{re.escape(key)}\s*:.*\n?")
        if key_line_re.search(frontmatter_text):
            frontmatter_text = key_line_re.sub(serialized + "\n", frontmatter_text, count=1)
        else:
            if not frontmatter_text.endswith("\n"):
                frontmatter_text += "\n"
            frontmatter_text += serialized + "\n"

    # The regex consumed the newline directly after the closing `---`; put it
    # back so the body's leading whitespace is preserved verbatim.
    new_content = f"---\n{frontmatter_text}---\n{rest}"
    if not new_content.endswith("\n"):
        new_content += "\n"
    skill_md.write_text(new_content)
    return read_skill(skill_dir)


def to_prompt(skill_dirs: list[Path|str], path_mapping: dict[Path|str, Path|str]|None = None) -> str:
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
    path_mapping = {Path(k).resolve(): Path(v).resolve() for k, v in (path_mapping or {}).items()}

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
        lines.extend([
            "<skill>",
            "<name>", html.escape(skill.name), "</name>",
            "<description>", html.escape(skill.description), "</description>",
            "<location>", html.escape(str(skill_md)), "</location>",
            "</skill>",
        ])
    lines.append("</available_skills>")

    return "\n".join(lines)
