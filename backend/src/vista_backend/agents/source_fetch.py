"""
Fetch a repository for the agent to wrap as an HPC skill, from the backend.

The agent's sandbox is the wrong place to reach a code host: it holds no
credentials (and must not, since the agent can read anything in it), and on a
network that re-signs HTTPS, such as ORNL's, it does not trust the proxy's
certificate authority. The backend has both, as it does for
skill import (`agents/skill_repo`). So the agent asks for a repository by URL
(`fetch_repository`), and the backend downloads it at a pinned commit into the
agent's own workspace, where the sandbox reads it offline.

Two hosts, over their APIs only:

  https://github.com/<owner>/<repo>[/tree/<ref>[/<subpath>]]
  https://gitlab.com/<group>[/<subgroup>...]/<repo>[/-/tree/<ref>[/<subpath>]]

The ref (or the default branch) is resolved to the commit it points at now and
the archive is downloaded by that commit, so what the skill wraps cannot move.
The caller passes the user's token for the URL's host, from Settings (see
`services/repo_tokens`); it is sent as a bearer token and makes that token's
private repositories readable. The token itself never reaches the sandbox.
"""

import io
import json
import re
import shutil
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


class SourceFetchError(Exception):
    """A user-facing problem: bad URL, not found, too large, unsafe archive."""


MAX_ARCHIVE_BYTES = 200 * 1024 * 1024
MAX_UNPACKED_BYTES = 500 * 1024 * 1024
""" A skill ships or clones code, not data; refuse anything larger. """

API_PREFIXES = ("https://api.github.com/", "https://gitlab.com/api/v4/")

_GITHUB = re.compile(
    r"^https?://github\.com/(?P<owner>[^/\s]+)/(?P<repo>[^/\s]+?)(?:\.git)?"
    r"(?:/tree/(?P<ref>[^/\s]+)(?:/(?P<subpath>.+?))?)?/?$"
)
# GitLab projects can sit in nested groups, so the project path is everything
# before `/-/` (or the whole path). A branch name containing `/` cannot be told
# apart from a subpath in a tree URL; such a ref is passed separately.
_GITLAB = re.compile(
    r"^https?://gitlab\.com/(?P<project>[^\s?#]+?)(?:\.git)?"
    r"(?:/-/tree/(?P<ref>[^/\s?#]+)(?:/(?P<subpath>[^\s?#]+?))?)?/?(?:[?#]\S*)?$"
)
_PATH_PART = re.compile(r"[A-Za-z0-9_.][A-Za-z0-9_.-]*")


@dataclass
class Remote:
    host: str
    """ "github" or "gitlab". """
    project: str
    """ `owner/repo` on GitHub; the full `group/.../repo` path on GitLab. """
    ref: str | None
    subpath: str

    @property
    def web_url(self) -> str:
        return f"https://{self.host}.com/{self.project}"

    @property
    def name(self) -> str:
        """A folder name for it: the repository's, plus the subpath's last part."""
        last = self.project.rsplit("/", 1)[-1]
        if self.subpath:
            last = f"{last}-{self.subpath.rsplit('/', 1)[-1]}"
        return re.sub(r"[^A-Za-z0-9._-]", "_", last).lstrip(".") or "source"


def _subpath(raw: str | None, url: str) -> str:
    subpath = (raw or "").strip("/")
    if any(part in ("", ".", "..") for part in subpath.split("/") if subpath):
        raise SourceFetchError(f"Invalid path in {url!r}.")
    return subpath


def parse_remote(url: str, ref: str | None = None) -> Remote:
    """The repository a github.com or gitlab.com URL names; `ref` overrides the URL's."""
    url = url.strip()
    m = _GITHUB.match(url)
    if m:
        remote = Remote(
            "github",
            f"{m.group('owner')}/{m.group('repo')}",
            m.group("ref"),
            _subpath(m.group("subpath"), url),
        )
    elif m := _GITLAB.match(url):
        project = m.group("project")
        parts = project.split("/")
        if (
            len(parts) < 2
            or "-" in parts
            or not all(_PATH_PART.fullmatch(p) and p != ".." for p in parts)
        ):
            raise SourceFetchError(
                f"{url!r} is not a GitLab project URL (https://gitlab.com/<group>/<repo>)."
            )
        remote = Remote(
            "gitlab", project, m.group("ref"), _subpath(m.group("subpath"), url)
        )
    else:
        raise SourceFetchError(
            f"{url!r} is not a github.com or gitlab.com repository URL. For code "
            "anywhere else, ask the user to upload an archive of it."
        )
    if ref:
        remote.ref = ref.strip()
    return remote


def _request(
    url: str, host: str, *, token: str | None = None, limit: int = 10 * 1024 * 1024
) -> bytes:
    """GET from one of the two APIs, with the user's token for that host, if any."""
    if not url.startswith(API_PREFIXES):
        raise SourceFetchError(f"Refusing to fetch {url}.")
    name = "GitHub" if host == "github" else "GitLab"
    req = urllib.request.Request(url, headers={"User-Agent": "vista-backend"})
    if token:
        # Unredirected: urllib copies ordinary headers onto a redirect to any
        # host, even plain http, and the token is for this host alone.
        req.add_unredirected_header("Authorization", f"Bearer {token}")
    try:
        # The guard above restricts this to the GitHub and GitLab APIs over https.
        with urllib.request.urlopen(req, timeout=60) as resp:  # nosec B310
            data = resp.read(limit + 1)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 404):
            hint = (
                f"The repository may be private, and the user has no {name} token "
                "in Settings → Repositories: ask them to add one."
                if not token
                else f"The user's {name} token in Settings may not have access to it."
            )
            raise SourceFetchError(
                f"{name} returned {e.code} for {url}. {hint} The ref may also not "
                "exist. Otherwise ask the user to upload an archive of the code."
            ) from e
        raise SourceFetchError(f"{name} request failed ({e.code}): {url}") from e
    except urllib.error.URLError as e:
        raise SourceFetchError(f"Could not reach {name}: {e.reason}") from e
    if len(data) > limit:
        raise SourceFetchError(
            f"The download is larger than {limit // 1024 // 1024} MB; a skill should "
            "wrap code, not data. Ask the user for the folder that holds the job."
        )
    return data


def _resolve(remote: Remote, token: str | None) -> tuple[str, str, bool, bytes]:
    """`(ref, commit, private, tarball)`: the ref pinned to the commit it points at now."""
    quote = lambda s: urllib.parse.quote(s, safe="")  # noqa: E731
    if remote.host == "github":
        api = f"https://api.github.com/repos/{remote.project}"
        info = json.loads(_request(api, "github", token=token))
        ref = remote.ref or info.get("default_branch") or "main"
        commit = json.loads(
            _request(f"{api}/commits/{quote(ref)}", "github", token=token)
        )["sha"]
        private = bool(info.get("private"))
        tar = _request(
            f"{api}/tarball/{commit}", "github", token=token, limit=MAX_ARCHIVE_BYTES
        )
    else:
        api = f"https://gitlab.com/api/v4/projects/{quote(remote.project)}"
        info = json.loads(_request(api, "gitlab", token=token))
        ref = remote.ref or info.get("default_branch") or "main"
        commit = json.loads(
            _request(f"{api}/repository/commits/{quote(ref)}", "gitlab", token=token)
        )["id"]
        private = info.get("visibility", "public") != "public"
        tar = _request(
            f"{api}/repository/archive.tar.gz?sha={commit}",
            "gitlab",
            token=token,
            limit=MAX_ARCHIVE_BYTES,
        )
    return ref, commit, private, tar


def _unpack(tar_bytes: bytes, dest: Path, subpath: str) -> int:
    """
    Unpack a host's archive (one top-level folder) into `dest`, keeping only
    `subpath` if given. Regular files only: links, devices and anything that
    would land outside `dest` are dropped. Returns the number of files.
    """
    prefix = PurePosixPath(subpath) if subpath else None
    count = total = 0
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:*") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or "\\" in member.name:
                raise SourceFetchError(f"Unsafe path in the archive: {member.name!r}")
            path = PurePosixPath(*path.parts[1:])  # the host's top-level folder
            if prefix is not None:
                if not path.is_relative_to(prefix):
                    continue
                path = path.relative_to(prefix)
            if not path.parts:
                continue
            total += member.size
            if total > MAX_UNPACKED_BYTES:
                raise SourceFetchError(
                    f"The source is larger than {MAX_UNPACKED_BYTES // 1024 // 1024} MB unpacked."
                )
            target = dest.joinpath(*path.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            src = tar.extractfile(member)
            assert src is not None
            with src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
            if member.mode & 0o111:
                target.chmod(0o755)
            count += 1
    if count == 0:
        raise SourceFetchError(
            f"{subpath!r} has no files in the repository."
            if subpath
            else "The repository is empty."
        )
    return count


def fetch_repository(
    url: str, work_dir: Path, *, ref: str | None = None, token: str | None = None
) -> dict:
    """
    Download the repository `url` names into `work_dir/<name>/` (replacing a
    previous fetch of the same name) and return its SOURCE.json record, which
    is also written there.

    `token` is the user's token for `url`'s host, chosen by
    `services/repo_tokens`, and is sent only to that host's API.
    """
    remote = parse_remote(url, ref)
    ref_, commit, private, tar = _resolve(remote, token)
    dest = work_dir / remote.name
    work_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=work_dir) as tmp:
        staged = Path(tmp) / "src"
        files = _unpack(tar, staged, remote.subpath)
        record = {
            "kind": remote.host,
            "url": remote.web_url,
            "clone_url": f"{remote.web_url}.git",
            "ref": ref_,
            "commit": commit,
            "subpath": remote.subpath,
            "private": private,
            "files": files,
        }
        (staged / "SOURCE.json").write_text(
            json.dumps(record, indent=2) + "\n", encoding="utf-8"
        )
        if dest.exists():
            shutil.rmtree(dest)
        staged.rename(dest)
    record["folder"] = remote.name
    return record
