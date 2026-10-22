"""
Find and install the skills in a Git repository on GitHub or any GitLab.

A repository may hold one skill (SKILL.md at its root) or many, each in its own
folder with its own SKILL.md, at any depth (`skills/<group>/<skill>/`). A URL
may name the repository, or a folder in it:

  https://github.com/<owner>/<repo>[/tree/<ref>[/<path>]]
  https://<gitlab-host>/<group>[/<subgroup>...]/<repo>[/-/tree/<ref>[/<path>]]

A `/blob/` URL to a SKILL.md names its folder. Any host other than github.com is
asked whether it is a GitLab (`/api/v4/projects/<path>`); nothing else is
supported. The ref (or default branch) is pinned to the commit it points at, so
the skills a scan lists are the ones an install copies.

Which skills: the folder the URL names, if it holds a SKILL.md (exactly as a
one-skill repository always imported); otherwise those a
`.claude-plugin/marketplace.json` lists under it; otherwise every folder under
it with a SKILL.md, not looking inside a skill or into `.git`, `node_modules`
and other clutter.

Each skill is its whole folder. What it reaches outside that folder is either
brought in or flagged (`RepoSkill.warnings`): a symlink to a file elsewhere in
the repository is copied as that file, one leaving the repository is dropped,
a `../` path in its Markdown that leaves the folder is flagged, and a LICENSE,
NOTICE or ATTRIBUTION file in a folder above it (the nearest of each) is copied
in, since a group folder's license covers the skills beneath it.

The caller passes the token to send, if any. It goes only to the URL's own
host, over https.
"""

import io
import json
import os
import posixpath
import re
import shutil
import socket
import tarfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from pydantic import ValidationError

from .skill_import import (
    _IGNORED_DIRS as IGNORED_DIRS,
    _IGNORED_FILES as IGNORED_FILES,
    MAX_UPLOAD_BYTES as MAX_SKILL_BYTES,
    MAX_UPLOAD_FILES as MAX_SKILL_FILES,
)
from .skills import SkillError, find_skill_md, read_skill, skill_hpc_jobs


class SkillRepoError(Exception):
    """A user-facing problem: bad URL, unreachable host, refused token, no skills."""


class SkillRepoAuthError(SkillRepoError):
    """
    The host refused the request, or hid a private repository behind a 404.
    The message says what happened; where to get a token is the caller's to
    add, since only it knows where this user's tokens come from.
    """

    def __init__(self, message: str, *, host: str, had_token: bool):
        super().__init__(message)
        self.host = host
        self.had_token = had_token


class SkillRepoUnreachableError(SkillRepoError):
    """The host could not be reached, or did not answer in time."""


MAX_ARCHIVE_BYTES = 200 * 1024 * 1024
MAX_UNPACKED_BYTES = 500 * 1024 * 1024
MANIFEST = ".claude-plugin/marketplace.json"
_INHERITED = re.compile(r"(LICEN[CS]E|NOTICE|ATTRIBUTION|COPYING)([.-][\w.-]*)?", re.I)
_UP_REF = re.compile(r"(?<![\w/.])((?:\.\./)+[\w.-][\w./-]*)")


# --------------------------------------------------------------------------- #
# URLs
# --------------------------------------------------------------------------- #


@dataclass
class RepoUrl:
    kind: str
    """ "github", or "gitlab" once the host has answered as one (see `fetch`). """
    host: str
    project: str
    """ `owner/repo` on GitHub; the full `group/.../repo` path on GitLab. """
    ref: str | None
    path: str
    """ The folder the URL names inside the repository; "" for its root. """
    tail: list[str] = field(default_factory=list)
    """ The URL's segments after `tree/`, kept to re-split a ref containing `/`. """

    @property
    def web_url(self) -> str:
        return f"https://{self.host}/{self.project}"

    def tree_url(self, ref: str, path: str) -> str:
        """A link to `path` at `ref`, as the host's web UI writes it."""
        if not path:
            return self.web_url
        sep = "/tree/" if self.kind == "github" else "/-/tree/"
        return f"{self.web_url}{sep}{ref}/{path}"


_SEGMENT = re.compile(r"[A-Za-z0-9_.~][A-Za-z0-9_.~-]*")


def _clean_path(parts: list[str], url: str) -> str:
    if any(p in ("", ".", "..") for p in parts):
        raise SkillRepoError(f"Invalid path in {url!r}.")
    if parts and parts[-1].lower() == "skill.md":
        parts = parts[:-1]
    return "/".join(parts)


def parse_repo_url(url: str) -> RepoUrl:
    """
    Split a repository or folder URL into host, project, ref and path. Says
    nothing yet about whether a non-GitHub host is a GitLab.
    """
    raw = url.strip()
    parts = urllib.parse.urlsplit(raw)
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise SkillRepoError(
            f"{raw!r} is not a repository URL. Expected https://github.com/<owner>/<repo> "
            "or https://<gitlab-host>/<group>/<repo>, optionally with /tree/<ref>/<path>."
        )
    if parts.scheme != "https":
        raise SkillRepoError(f"Use an https:// URL, not {raw!r}.")
    host = parts.hostname.lower()
    if parts.port:
        host = f"{host}:{parts.port}"
    segs = [urllib.parse.unquote(s) for s in parts.path.strip("/").split("/") if s]
    if segs and segs[-1].endswith(".git"):
        segs[-1] = segs[-1][: -len(".git")]

    if host in ("github.com", "www.github.com"):
        if len(segs) < 2:
            raise SkillRepoError(
                f"{raw!r} does not name a repository (github.com/<owner>/<repo>)."
            )
        project, rest = segs[:2], segs[2:]
        kind = "github"
        if rest and rest[0] not in ("tree", "blob"):
            raise SkillRepoError(f"{raw!r} is not a repository or folder URL.")
    else:
        kind = "gitlab"
        if "-" in segs:
            i = segs.index("-")
            project, rest = segs[:i], segs[i + 1 :]
            if rest and rest[0] not in ("tree", "blob"):
                raise SkillRepoError(f"{raw!r} is not a repository or folder URL.")
        else:
            project, rest = segs, []
        if len(project) < 2:
            raise SkillRepoError(
                f"{raw!r} does not name a repository (<host>/<group>/<repo>)."
            )
    if not all(_SEGMENT.fullmatch(p) for p in project):
        raise SkillRepoError(f"{raw!r} does not name a repository.")
    tail = rest[1:]
    if rest and not tail:
        raise SkillRepoError(f"{raw!r} names no branch after /{rest[0]}/.")
    ref = tail[0] if tail else None
    path = _clean_path(tail[1:], raw)
    return RepoUrl(kind, host, "/".join(project), ref, path, tail)


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #


def _open(
    url: str, token: str | None, accept: str | None
) -> tuple[int, dict[str, str], bytes]:
    """One GET, HTTP errors included: `(status, lower-cased headers, body)`."""
    if not url.startswith("https://"):
        raise SkillRepoError(f"Refusing to fetch non-HTTPS URL: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "vista-backend"})
    if accept:
        req.add_header("Accept", accept)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    host = urllib.parse.urlsplit(url).netloc
    try:
        # https only, by the guard above.
        with urllib.request.urlopen(req, timeout=60) as resp:  # nosec B310
            headers = {k.lower(): v for k, v in resp.headers.items()}
            return resp.status, headers, resp.read(MAX_ARCHIVE_BYTES + 1)
    except urllib.error.HTTPError as e:
        headers = {k.lower(): v for k, v in (e.headers or {}).items()}
        return e.code, headers, b""
    except urllib.error.URLError as e:
        if isinstance(e.reason, (socket.timeout, TimeoutError)):
            raise SkillRepoUnreachableError(f"{host} did not answer in time.") from e
        raise SkillRepoUnreachableError(f"Could not reach {host}: {e.reason}") from e
    except (socket.timeout, TimeoutError) as e:
        raise SkillRepoUnreachableError(f"{host} did not answer in time.") from e


def _json(data: bytes, label: str) -> dict:
    try:
        value = json.loads(data)
    except ValueError as e:
        raise SkillRepoError(
            f"{label} did not answer like a GitHub or GitLab API."
        ) from e
    if not isinstance(value, dict):
        raise SkillRepoError(f"{label} did not answer like a GitHub or GitLab API.")
    return value


def _not_found(repo: RepoUrl, token: str | None) -> SkillRepoError:
    """A repository the host will not show: missing, or private (both are 404s)."""
    if token:
        return SkillRepoAuthError(
            f"{repo.host} has no repository {repo.project!r} that the token can "
            "read (404). Check the URL, and that the token has read access to it.",
            host=repo.host,
            had_token=True,
        )
    return SkillRepoAuthError(
        f"{repo.host} has no public repository {repo.project!r} (404). Check the "
        "URL; if the repository is private, it needs a token.",
        host=repo.host,
        had_token=False,
    )


def _refused(repo: RepoUrl, status: int, token: str | None) -> SkillRepoAuthError:
    return SkillRepoAuthError(
        f"{repo.host} refused access ({status}). "
        + (
            "The token may be wrong, expired, or lack read access to this repository."
            if token
            else "The repository may be private: it needs a token with read access."
        ),
        host=repo.host,
        had_token=bool(token),
    )


def _api(
    repo: RepoUrl,
    url: str,
    token: str | None,
    *,
    accept: str | None = None,
    limit: int = 10 * 1024 * 1024,
) -> bytes | None:
    """GET from the repository's API; None for a 404 (or GitHub's 422 for a bad ref)."""
    status, _, body = _open(url, token, accept)
    if status in (401, 403):
        raise _refused(repo, status, token)
    if status in (404, 422):
        return None
    if status >= 400:
        raise SkillRepoError(f"{repo.host} request failed ({status}): {url}")
    if len(body) > limit:
        raise SkillRepoError(
            f"The download from {repo.host} is larger than {limit // 1024 // 1024} MB."
        )
    return body


def _api_base(repo: RepoUrl) -> str:
    if repo.kind == "github":
        return f"https://api.github.com/repos/{repo.project}"
    return f"https://{repo.host}/api/v4/projects/{urllib.parse.quote(repo.project, safe='')}"


def _project_info(repo: RepoUrl, token: str | None) -> dict:
    """The repository's API record; for a non-GitHub host, also proves it is a GitLab."""
    url = _api_base(repo)
    if repo.kind == "github":
        body = _api(repo, url, token)
        if body is None:
            raise _not_found(repo, token)
        return _json(body, repo.host)
    status, headers, body = _open(url, token, "application/json")
    is_gitlab = any(k.startswith("x-gitlab") for k in headers) or (
        status == 200 and b"path_with_namespace" in body
    )
    if not is_gitlab:
        raise SkillRepoError(
            f"{repo.host} is not GitHub, and did not answer as a GitLab server. "
            "Only github.com and GitLab hosts are supported."
        )
    if status in (401, 403):
        raise _refused(repo, status, token)
    if status == 404:
        raise _not_found(repo, token)
    if status >= 400:
        raise SkillRepoError(f"{repo.host} request failed ({status}): {url}")
    return _json(body, repo.host)


def _commit(repo: RepoUrl, ref: str, token: str | None) -> str | None:
    """The commit `ref` points at now, or None if there is no such ref."""
    ref_q = urllib.parse.quote(ref, safe="")
    if repo.kind == "github":
        body = _api(
            repo,
            f"{_api_base(repo)}/commits/{ref_q}",
            token,
            accept="application/vnd.github.sha",
        )
        return body.decode("ascii", "replace").strip() if body else None
    body = _api(repo, f"{_api_base(repo)}/repository/commits/{ref_q}", token)
    if body is None:
        return None
    return str(_json(body, repo.host).get("id") or "") or None


def _resolve_ref(repo: RepoUrl, info: dict, token: str | None) -> tuple[str, str]:
    """
    `(ref, commit)`. A ref containing `/` cannot be told from the path in a tree
    URL, so the shortest leading run of the URL's segments that names a commit
    is the ref, and the rest is the path.
    """
    if not repo.tail:
        ref = str(info.get("default_branch") or "main")
        commit = _commit(repo, ref, token)
        if commit is None:
            raise SkillRepoError(f"{repo.web_url} has no branch {ref!r}; is it empty?")
        return ref, commit
    for n in range(1, min(len(repo.tail), 6) + 1):
        ref = "/".join(repo.tail[:n])
        commit = _commit(repo, ref, token)
        if commit:
            repo.ref, repo.path = ref, _clean_path(repo.tail[n:], repo.web_url)
            return ref, commit
    raise SkillRepoError(
        f"{repo.web_url} has no branch, tag or commit {repo.tail[0]!r}."
    )


# --------------------------------------------------------------------------- #
# The archive
# --------------------------------------------------------------------------- #


@dataclass
class FetchedRepo:
    """A repository unpacked at one commit: regular files on disk, links recorded."""

    url: RepoUrl
    ref: str
    commit: str
    root: Path
    links: dict[str, str]
    """ Each symlink's repository path, and its target as written. """


def _unpack(tar_bytes: bytes, dest: Path) -> dict[str, str]:
    """
    Unpack a host's archive (one top-level folder) into `dest`. Regular files
    are written; symlinks are returned, not created, so nothing on disk points
    anywhere; anything else is dropped.
    """
    links: dict[str, str] = {}
    total = 0
    try:
        tar = tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:*")
    except tarfile.TarError as e:
        raise SkillRepoError("The repository archive could not be read.") from e
    with tar:
        for member in tar.getmembers():
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or "\\" in member.name:
                raise SkillRepoError(f"Unsafe path in the archive: {member.name!r}")
            rel = PurePosixPath(*path.parts[1:])  # the host's top-level folder
            if not rel.parts:
                continue
            if member.issym():
                links[rel.as_posix()] = member.linkname
                continue
            if not member.isfile():
                continue
            total += member.size
            if total > MAX_UNPACKED_BYTES:
                raise SkillRepoError(
                    f"The repository is larger than {MAX_UNPACKED_BYTES // 1024 // 1024} MB unpacked."
                )
            target = dest.joinpath(*rel.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            src = tar.extractfile(member)
            if src is None:
                continue
            with src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
            target.chmod(0o755 if member.mode & 0o111 else 0o644)
    return links


def fetch(
    url: str, dest: Path, *, token: str | None = None, commit: str | None = None
) -> FetchedRepo:
    """
    Download the repository `url` names into `dest` (an empty folder). With
    `commit`, that commit is downloaded, as a scan pinned it; otherwise what
    the URL's ref (or the default branch) points at now.
    """
    repo = parse_repo_url(url)
    info = _project_info(repo, token)
    ref, current = _resolve_ref(repo, info, token)
    if commit is not None and not re.fullmatch(r"[0-9a-f]{7,64}", commit):
        raise SkillRepoError(f"Invalid commit {commit!r}.")
    commit = commit or current
    if repo.kind == "github":
        archive = f"{_api_base(repo)}/tarball/{commit}"
    else:
        archive = f"{_api_base(repo)}/repository/archive.tar.gz?sha={commit}"
    body = _api(repo, archive, token, limit=MAX_ARCHIVE_BYTES)
    if body is None:
        raise SkillRepoError(f"{repo.web_url} has no commit {commit}.")
    dest.mkdir(parents=True, exist_ok=True)
    links = _unpack(body, dest)
    if repo.path and not (dest / repo.path).is_dir():
        raise SkillRepoError(f"{repo.web_url} has no folder {repo.path!r} at {ref}.")
    return FetchedRepo(repo, ref, commit, dest, links)


# --------------------------------------------------------------------------- #
# Finding the skills
# --------------------------------------------------------------------------- #

MAX_SKILLS = 500


def _manifest_skills(fetched: FetchedRepo) -> list[str]:
    """The skill folders `.claude-plugin/marketplace.json` lists, if it lists any."""
    manifest = fetched.root / MANIFEST
    if not manifest.is_file():
        return []
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except ValueError, UnicodeDecodeError:
        return []
    found: list[str] = []
    plugins = data.get("plugins") if isinstance(data, dict) else None
    for plugin in plugins if isinstance(plugins, list) else []:
        if not isinstance(plugin, dict):
            continue
        base = plugin.get("source") or "."
        if not isinstance(base, str):
            continue  # hosted in another repository: none of its skills are here
        skills = plugin.get("skills")
        entries = (
            [s for s in skills if isinstance(s, str)]
            if isinstance(skills, list)
            else []
        )
        for entry in entries or ["."]:
            path = posixpath.normpath(posixpath.join(base, entry)).lstrip("/")
            if path == ".":
                path = ""
            if path.startswith(".."):
                continue
            if find_skill_md(fetched.root / path) is not None:
                found.append(path)
            else:  # a folder of skills (or the plugin's own, with no list)
                found.extend(_search(fetched.root, path))
    return list(dict.fromkeys(found))


def _search(root: Path, start: str) -> list[str]:
    """Every folder under `start` with a SKILL.md, not looking inside a skill."""
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root / start):
        here = Path(dirpath)
        if "SKILL.md" in filenames or "skill.md" in filenames:
            found.append(here.relative_to(root).as_posix())
            dirnames[:] = []
            continue
        dirnames[:] = sorted(
            d for d in dirnames if d not in IGNORED_DIRS and not d.startswith(".")
        )
        if len(found) > MAX_SKILLS:
            break
    return sorted(found)


def discover(fetched: FetchedRepo) -> list[str]:
    """The repository paths of the skills under the folder the URL names."""
    start = fetched.url.path
    if find_skill_md(fetched.root / start) is not None:
        return [start]
    prefix = f"{start}/" if start else ""
    listed = [
        p for p in _manifest_skills(fetched) if p == start or p.startswith(prefix)
    ]
    found = listed or _search(fetched.root, start)
    if not found:
        where = f"the folder {start!r}" if start else "the repository"
        raise SkillRepoError(
            f"No SKILL.md found in {where} ({fetched.url.web_url} at {fetched.ref}), "
            "at the top or in any folder below it."
        )
    if len(found) > MAX_SKILLS:
        raise SkillRepoError(
            f"More than {MAX_SKILLS} skills found; link a folder of fewer."
        )
    return found


# --------------------------------------------------------------------------- #
# One skill's folder
# --------------------------------------------------------------------------- #


@dataclass
class RepoSkill:
    """A skill found in a repository, as staged from it."""

    path: str
    """ Its folder in the repository; "" for the root. """
    name: str | None = None
    description: str | None = None
    error: str | None = None
    """ Why it cannot be imported: invalid SKILL.md, or over the size limits. """
    warnings: list[str] = field(default_factory=list)
    """ What it reaches outside its folder that the import could not bring along. """
    included: list[str] = field(default_factory=list)
    """ Files from outside its folder that the import copies in, and from where. """
    hpc_jobs: list[str] = field(default_factory=list)
    files: int = 0


def _validation_message(e: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(p) for p in err['loc']) or 'frontmatter'}: {err['msg']}"
        for err in e.errors()
    )


def _resolve_link(fetched: FetchedRepo, link: str) -> str | None:
    """Where a symlink in the repository ends up, or None if outside it (or a loop)."""
    for _ in range(20):
        target = fetched.links[link]
        if target.startswith("/"):
            return None
        resolved = posixpath.normpath(posixpath.join(posixpath.dirname(link), target))
        if resolved == ".." or resolved.startswith("../"):
            return None
        if resolved not in fetched.links:
            return "" if resolved == "." else resolved
        link = resolved
    return None


def _inside(path: str, folder: str) -> bool:
    return not folder or path == folder or path.startswith(f"{folder}/")


def _copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    dst.chmod(src.stat().st_mode & 0o777)


def _ignored(rel: PurePosixPath) -> bool:
    return rel.name in IGNORED_FILES or any(p in IGNORED_DIRS for p in rel.parts[:-1])


def stage(fetched: FetchedRepo, path: str, dest: Path) -> RepoSkill:
    """
    Copy the skill at `path` to `dest` (which must not exist), bringing in what
    it links to or inherits from above it, and describe it. A `RepoSkill` with
    `error` set must not be installed; `dest` is then removed.
    """
    info = RepoSkill(path=path)
    src = fetched.root / path if path else fetched.root
    dest.mkdir(parents=True)
    brought: set[str] = set()
    """ Repository paths from outside the folder that are copied in. """

    for dirpath, dirnames, filenames in os.walk(src):
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS]
        for name in filenames:
            file = Path(dirpath) / name
            rel = PurePosixPath(file.relative_to(src).as_posix())
            if not _ignored(rel):
                _copy_file(file, dest.joinpath(*rel.parts))

    for link in sorted(fetched.links):
        if not _inside(link, path) or link == path:
            continue
        rel = PurePosixPath(link[len(path) :].lstrip("/"))
        if _ignored(rel):
            continue
        target = _resolve_link(fetched, link)
        where = target if target is not None else fetched.links[link]
        if target is None or not (fetched.root / target).exists():
            info.warnings.append(
                f"{rel} is a link to {fetched.links[link]}, which is not in the "
                "repository; it was left out."
            )
            continue
        brought.add(target)
        if (fetched.root / target).is_file():
            _copy_file(fetched.root / target, dest.joinpath(*rel.parts))
            count = 1
        else:
            count = 0
            for dirpath, dirnames, filenames in os.walk(fetched.root / target):
                dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS]
                for name in filenames:
                    file = Path(dirpath) / name
                    sub = file.relative_to(fetched.root / target).as_posix()
                    _copy_file(
                        file, dest.joinpath(*rel.parts, *PurePosixPath(sub).parts)
                    )
                    count += 1
        if not _inside(where, path):
            info.included.append(
                f"{rel} (a link to {where}, copied in"
                + (f" with {count} files" if count != 1 else "")
                + ")"
            )

    _inherit(fetched, path, dest, info, brought)
    _flag_up_references(path, dest, info, brought)

    files = [p for p in dest.rglob("*") if p.is_file()]
    info.files = len(files)
    size = sum(p.stat().st_size for p in files)
    try:
        skill = read_skill(dest)
        info.name, info.description = skill.name, skill.description
    except ValidationError as e:
        info.error = f"SKILL.md in {path or 'the repository root'} is invalid: {_validation_message(e)}"
    except (SkillError, UnicodeDecodeError) as e:
        info.error = f"SKILL.md in {path or 'the repository root'} is invalid: {e}"
    if info.error is None and len(files) > MAX_SKILL_FILES:
        info.error = (
            f"It has {len(files)} files; a skill may have at most {MAX_SKILL_FILES}."
        )
    if info.error is None and size > MAX_SKILL_BYTES:
        info.error = (
            f"It is {size / 1024 / 1024:.0f} MB; a skill may be at most "
            f"{MAX_SKILL_BYTES // 1024 // 1024} MB."
        )
    info.hpc_jobs = skill_hpc_jobs(dest)
    if info.error is not None:
        shutil.rmtree(dest, ignore_errors=True)
    return info


def _inherit(
    fetched: FetchedRepo, path: str, dest: Path, info: RepoSkill, brought: set[str]
) -> None:
    """Copy in the nearest LICENSE / NOTICE / ATTRIBUTION from the folders above the skill."""
    if not path:
        return
    have = {p.name.lower() for p in dest.iterdir() if p.is_file()}
    parts = PurePosixPath(path).parts
    for depth in range(len(parts) - 1, -1, -1):
        folder = "/".join(parts[:depth])
        here = fetched.root / folder if folder else fetched.root
        for file in sorted(here.iterdir()):
            if not file.is_file() or not _INHERITED.fullmatch(file.name):
                continue
            if file.name.lower() in have:
                continue
            have.add(file.name.lower())
            brought.add(f"{folder}/{file.name}" if folder else file.name)
            _copy_file(file, dest / file.name)
            info.included.append(
                f"{file.name} (from {folder + '/' if folder else 'the repository root'})"
            )


def _flag_up_references(
    path: str, dest: Path, info: RepoSkill, brought: set[str]
) -> None:
    """
    Flag `../` paths in the skill's Markdown that lead out of its folder,
    unless to a file the import copied in.
    """
    seen: set[str] = set()
    inherited = {PurePosixPath(b).name for b in brought if not _inside(b, path)}
    for md in sorted(dest.rglob("*.md")):
        if md.parent == dest and md.name in inherited:
            continue  # its links are relative to the folder it came from
        try:
            text = md.read_text(encoding="utf-8")
        except UnicodeDecodeError, OSError:
            continue
        base = md.parent.relative_to(dest).as_posix()
        for ref in _UP_REF.findall(text):
            ref = ref.rstrip(".,;:)")
            resolved = posixpath.normpath(posixpath.join(base, ref))
            if not (resolved == ".." or resolved.startswith("../")) or ref in seen:
                continue
            in_repo = posixpath.normpath(posixpath.join(path or ".", resolved))
            if in_repo in brought:
                continue
            if ref not in seen:
                seen.add(ref)
                info.warnings.append(
                    f"{md.relative_to(dest).as_posix()} refers to {ref}, outside the "
                    "skill's folder; it is not imported."
                )


_NAME_LINE = re.compile(r"^name:[^\n]*$", re.M)


def rename_skill(skill_dir: Path, new_name: str) -> None:
    """
    Rewrite the `name:` of the SKILL.md in `skill_dir`, leaving the rest of the
    file as it was. Raises `SkillRepoError` if the result does not parse.
    """
    md = find_skill_md(skill_dir)
    if md is None:
        raise SkillRepoError("SKILL.md is missing.")
    text = md.read_text(encoding="utf-8")
    end = text.find("\n---", 3)
    if not text.startswith("---") or end < 0:
        raise SkillRepoError("SKILL.md has no frontmatter.")
    head, rest = text[:end], text[end:]
    if not _NAME_LINE.search(head):
        raise SkillRepoError("SKILL.md frontmatter has no name: line.")
    head = _NAME_LINE.sub(lambda _: f"name: {new_name}", head, count=1)
    md.write_text(head + rest, encoding="utf-8")
    try:
        skill = read_skill(skill_dir)
    except ValidationError as e:
        raise SkillRepoError(
            f"{new_name!r} is not a valid skill name: {_validation_message(e)}"
        ) from e
    except SkillError as e:
        raise SkillRepoError(str(e)) from e
    if skill.name != new_name:
        raise SkillRepoError(f"{new_name!r} is not a valid skill name.")
