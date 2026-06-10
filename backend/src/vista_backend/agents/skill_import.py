"""
Import a skill from a GitHub repository.

Two URL forms are accepted:

  https://github.com/<owner>/<repo>
      The repo's default branch is downloaded and SKILL.md is expected at the
      repo root.

  https://github.com/<owner>/<repo>/tree/<ref>/<subpath>
      The given ref (branch / tag / sha) is downloaded and SKILL.md is expected
      at <subpath>/ inside the repo. This covers monorepos like
      anthropic-skills where each skill is its own subdirectory.

The tarball is fetched from the GitHub API (no `git` binary needed). If
`settings.github_token` is set it is passed as a bearer token, enabling private
repos.

After download we copy the entire skill directory (SKILL.md plus any sibling
scripts / references / assets) to the caller-provided destination directory.
`repo_url` is filled in from the source URL when missing, and `is_public` is
forced to `false` so imports land private (the user can publish them via the
/skills tab).
"""
import io
import re
import shutil
import tarfile
import tempfile
import urllib.request
import urllib.error
from dataclasses import dataclass
from pathlib import Path
from pydantic import ValidationError

from .skills import (
    Skill,
    SkillError,
    find_skill_md,
    read_skill,
)
from ..config import settings


class SkillImportError(Exception):
    """Raised for any user-facing problem during import (parse / fetch / layout)."""


@dataclass
class ParsedGithubUrl:
    owner: str
    repo: str
    ref: str | None
    """ Branch/tag/sha. None means "default branch". """
    subpath: str
    """ Directory inside the repo containing SKILL.md. "" for repo root. """


_GITHUB_TREE_RE = re.compile(
    r"^https?://github\.com/(?P<owner>[^/\s]+)/(?P<repo>[^/\s]+?)(?:\.git)?"
    r"(?:/tree/(?P<ref>[^/\s]+)(?:/(?P<subpath>.+))?)?/?$"
)


def parse_github_url(url: str) -> ParsedGithubUrl:
    """
    Parse a GitHub web URL into (owner, repo, ref?, subpath).

    Raises:
        SkillImportError: If the URL isn't a recognized github.com form.
    """
    url = url.strip()
    m = _GITHUB_TREE_RE.match(url)
    if not m:
        raise SkillImportError(
            f"URL {url!r} is not a GitHub repo URL. Expected "
            "https://github.com/<owner>/<repo> or "
            "https://github.com/<owner>/<repo>/tree/<ref>/<subpath>."
        )
    subpath = m.group("subpath") or ""
    # Strip a leading/trailing slash and any "./" segments; reject ".." to
    # prevent escaping the repo via crafted URLs.
    subpath = subpath.strip("/")
    if any(part in ("..", "") for part in subpath.split("/") if subpath):
        raise SkillImportError(f"Invalid subpath in URL: {m.group('subpath')!r}")
    return ParsedGithubUrl(
        owner=m.group("owner"),
        repo=m.group("repo"),
        ref=m.group("ref"),
        subpath=subpath,
    )


def _api_request(url: str, accept: str = "application/json") -> bytes:
    """Send a GET to api.github.com with the optional auth token."""
    req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "vista-backend"})
    if settings.github_token:
        req.add_header("Authorization", f"Bearer {settings.github_token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise SkillImportError(f"GitHub returned 404 for {url}.") from e
        if e.code in (401, 403):
            raise SkillImportError(
                f"GitHub returned {e.code} for {url}. "
                "Set VISTA_BACKEND_GITHUB_TOKEN for private repos or to raise rate limits."
            ) from e
        raise SkillImportError(f"GitHub request failed ({e.code}): {url}") from e
    except urllib.error.URLError as e:
        raise SkillImportError(f"Failed to reach github.com: {e.reason}") from e


def _resolve_default_branch(owner: str, repo: str) -> str:
    import json
    payload = json.loads(
        _api_request(f"https://api.github.com/repos/{owner}/{repo}")
    )
    return payload.get("default_branch") or "main"


def _download_tarball(parsed: ParsedGithubUrl, dest_root: Path) -> Path:
    """
    Download the repo as a tarball and extract it under `dest_root`.

    Returns the path to the extracted repo root (which contains the subpath, if any).
    """
    ref = parsed.ref or _resolve_default_branch(parsed.owner, parsed.repo)
    tar_bytes = _api_request(
        f"https://api.github.com/repos/{parsed.owner}/{parsed.repo}/tarball/{ref}",
        accept="application/vnd.github+json",
    )
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz") as tar:
        # tarballs from GitHub wrap everything in a single top-level directory
        # named like "<owner>-<repo>-<shortsha>"; figure out what it's called
        # so we can return its path.
        top: str | None = None
        for member in tar.getmembers():
            head = member.name.split("/", 1)[0]
            if top is None:
                top = head
            elif head != top:
                raise SkillImportError("Unexpected tarball layout (multiple top-level dirs).")
        if top is None:
            raise SkillImportError("Empty tarball.")
        # Python 3.12+ requires an explicit filter argument; "data" disallows
        # absolute paths, links escaping the dest, and setuid bits.
        tar.extractall(dest_root, filter="data")
        return dest_root / top


def import_skill_from_github(url: str, dest_dir: Path | str) -> Skill:
    """
    Import the skill at `url` into `dest_dir` (which must not already exist).

    Returns the imported `Skill`. Raises `SkillImportError` for any
    user-facing problem.
    """
    parsed = parse_github_url(url)
    dest_dir = Path(dest_dir).resolve()

    with tempfile.TemporaryDirectory() as tmp:
        extracted = _download_tarball(parsed, Path(tmp))
        source_dir = extracted / parsed.subpath if parsed.subpath else extracted
        if not source_dir.exists() or not source_dir.is_dir():
            raise SkillImportError(
                f"Subpath {parsed.subpath!r} does not exist in {parsed.owner}/{parsed.repo}."
            )
        if find_skill_md(source_dir) is None:
            raise SkillImportError(
                f"No SKILL.md found at {parsed.subpath or '<repo root>'} in {parsed.owner}/{parsed.repo}."
            )
        try:
            skill = read_skill(source_dir)
        except (SkillError, ValidationError) as e:
            raise SkillImportError(f"Imported SKILL.md is invalid: {e}") from e

        shutil.copytree(source_dir, dest_dir)
        return skill
