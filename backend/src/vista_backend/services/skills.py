"""
Skills service.
"""

# TODO: scope skills to a project; they are global entities for now.
import asyncio
import logging
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Literal

from fastapi import HTTPException, UploadFile
from pydantic import BaseModel, ValidationError
from pydantic_ai.messages import ModelMessage
from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..agents.skill_authoring import SkillDraft, generate_skill_draft
from ..agents.skill_import import (
    SkillImportError,
    import_skill_from_dir,
    import_skill_from_files,
    plain_files,
    read_plain_file,
)
from ..agents import skill_repo
from ..agents.skill_repo import (
    RepoSkill,
    SkillRepoAuthError,
    SkillRepoError,
    SkillRepoUnreachableError,
)
from ..agents.skills import (
    ParseError,
    Skill,
    SkillError,
    find_skill_md,
    parse_skill,
    read_skill,
    skill_hpc_jobs,
    skill_to_markdown,
    write_skill,
)
from ..config import settings
from ..db.schemas import ProjectTable, SkillTable, SkillUpdate, UserPublicWithConfig
from ..utils.misc import now_iso
from ._helpers import ServiceUser, new_storage_path
from .repo_tokens import user_repo_token


logger = logging.getLogger(__name__)


def build_skill_row(
    skill: Skill,
    *,
    path: str,
    author: str | None,
    repo_url: str | None,
    is_public: bool,
    now: str,
) -> SkillTable:
    """Build a SkillTable row, mirroring spec metadata from the parsed skill."""
    return SkillTable(
        name=skill.name,
        path=path,
        description=skill.description,
        license=skill.license,
        compatibility=skill.compatibility,
        allowed_tools=skill.allowed_tools,
        skill_metadata=skill.metadata,
        author=author,
        repo_url=repo_url,
        is_public=is_public,
        created_at=now,
        updated_at=now,
    )


async def list_skills(session: AsyncSession) -> list[SkillTable]:
    return list((await session.exec(select(SkillTable))).all())


async def get_skill_optional(session: AsyncSession, name: str) -> SkillTable | None:
    return (
        await session.exec(select(SkillTable).where(SkillTable.name == name))
    ).first()


async def get_skill(session: AsyncSession, name: str) -> SkillTable:
    skill = await get_skill_optional(session, name)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Skill not found: {name}")
    return skill


async def get_skill_detail(session: AsyncSession, name: str) -> tuple[SkillTable, str]:
    """Return the row plus its SKILL.md body (read from disk)."""
    skill = await get_skill(session, name)
    return skill, read_skill(settings.data_dir / skill.path).body


def _load_into(session: AsyncSession, project: ProjectTable, name: str) -> None:
    """
    Add skill `name` to `project.skills` in the pending transaction, and rebuild
    the project's agents once it commits, so their next turn sees the skill.
    """
    # Imported here: `project_agent` reaches `db.seed`, which imports this module.
    from .project_agent import invalidate_agents

    if name in project.skills:
        return
    project.skills = [
        *project.skills,
        name,
    ]  # a new list, so the JSON column is marked dirty
    session.add(project)
    invalidate_agents(session, project_id=project.id)


async def _insert_skill_row(
    session: AsyncSession, row: SkillTable, project: ProjectTable | None = None
) -> None:
    """
    Insert and commit a freshly built skill row, loading it into `project` in
    the same commit, and remove its just-written folder if the commit fails.
    The uuid folder belongs solely to this row, so cleanup can never clobber
    another skill's files.
    """
    session.add(row)
    if project is not None:
        _load_into(session, project, row.name)
    try:
        await session.commit()
    except Exception as e:
        await session.rollback()
        shutil.rmtree(settings.data_dir / row.path, ignore_errors=True)
        if isinstance(e, IntegrityError):
            raise HTTPException(
                status_code=409, detail=f"Name already in use: {row.name}"
            )
        raise
    await session.refresh(row)


async def create_skill(
    session: AsyncSession,
    *,
    spec: Skill,
    author: str | None = None,
    repo_url: str | None = None,
    is_public: bool = False,
    project: ProjectTable | None = None,
) -> SkillTable:
    """
    Create a skill from a structured `spec` (the server generates the SKILL.md
    frontmatter from its fields). `spec.body` is markdown only. Given a
    `project`, the skill is loaded into it.
    """
    if await get_skill_optional(session, spec.name) is not None:
        raise HTTPException(status_code=409, detail=f"Name already in use: {spec.name}")

    path = new_storage_path()
    skill = write_skill(settings.data_dir / path, spec)
    row = build_skill_row(
        skill,
        path=path,
        author=author,
        repo_url=repo_url,
        is_public=is_public,
        now=now_iso(),
    )
    await _insert_skill_row(session, row, project)
    return row


class _SentToken(BaseModel):
    value: str
    from_request: bool
    """ Given with this import (never stored), rather than from Settings. """


async def _import_token(
    session: AsyncSession, url: str, user_id: uuid.UUID, token: str | None
) -> _SentToken | None:
    """
    The token a repository import sends: the one given with the request, for
    this import only, else the user's Settings token for the URL's host (never
    a project's forum token). Either goes only to the URL's own host.
    """
    if token and token.strip():
        return _SentToken(value=token.strip(), from_request=True)
    saved = await user_repo_token(session, url, user_id)
    return _SentToken(value=saved.value, from_request=False) if saved else None


def _repo_error(e: SkillRepoError, token: _SentToken | None) -> HTTPException:
    """The HTTP error for a failed repository fetch, saying where a token comes from."""
    if isinstance(e, SkillRepoAuthError):
        if token is None:
            hint = " Add a token for it in the import dialog" + (
                ", or save a GitHub token in Settings → Repositories."
                if e.host == "github.com"
                else ", or for gitlab.com save one in Settings → Repositories."
                if e.host == "gitlab.com"
                else "."
            )
        elif token.from_request:
            hint = " (The token given in the import dialog was sent.)"
        else:
            hint = " (Your token in Settings → Repositories was sent.)"
        return HTTPException(status_code=403, detail=f"{e}{hint}")
    if isinstance(e, SkillRepoUnreachableError):
        return HTTPException(status_code=502, detail=str(e))
    return HTTPException(status_code=400, detail=str(e))


class ScannedSkill(BaseModel):
    """A skill found in a repository, as `POST /skills/import/scan` lists it."""

    path: str
    """ Its folder in the repository; "" for the root. """
    url: str
    """ A link to that folder, which imports just this skill. """
    name: str | None
    description: str | None
    error: str | None = None
    warnings: list[str] = []
    included: list[str] = []
    hpc_jobs: list[str] = []
    files: int = 0
    exists: bool = False
    """ A skill with this name is already in the library. """
    replaceable: bool = False
    """ It exists and is private, so it may be overwritten. """


class RepoScan(BaseModel):
    url: str
    repository: str
    ref: str
    commit: str
    """ What an install of these skills downloads, so it copies what was listed. """
    path: str
    skills: list[ScannedSkill]


def _scan(
    url: str, token: str | None, commit: str | None, work: Path
) -> tuple[skill_repo.FetchedRepo, list[tuple[RepoSkill, Path]]]:
    """Fetch and stage every skill under the URL's folder into `work` (blocking)."""
    fetched = skill_repo.fetch(url, work / "repo", token=token, commit=commit)
    staged: list[tuple[RepoSkill, Path]] = []
    for i, path in enumerate(skill_repo.discover(fetched)):
        dest = work / "staged" / str(i)
        staged.append((skill_repo.stage(fetched, path, dest), dest))
    return fetched, staged


async def scan_repository(
    session: AsyncSession, url: str, user_id: uuid.UUID, token: str | None = None
) -> RepoScan:
    """List the skills a repository URL holds, without importing any."""
    sent = await _import_token(session, url, user_id, token)
    with tempfile.TemporaryDirectory() as tmp:
        try:
            fetched, staged = await asyncio.to_thread(
                _scan, url, sent.value if sent else None, None, Path(tmp)
            )
        except SkillRepoError as e:
            raise _repo_error(e, sent)
    library = {row.name: row for row in await list_skills(session)}
    skills = []
    for info, _ in staged:
        existing = library.get(info.name) if info.name else None
        skills.append(
            ScannedSkill(
                path=info.path,
                url=fetched.url.tree_url(fetched.ref, info.path),
                name=info.name,
                description=info.description,
                error=info.error,
                warnings=info.warnings,
                included=info.included,
                hpc_jobs=info.hpc_jobs,
                files=info.files,
                exists=existing is not None,
                replaceable=existing is not None and not existing.is_public,
            )
        )
    return RepoScan(
        url=url,
        repository=fetched.url.web_url,
        ref=fetched.ref,
        commit=fetched.commit,
        path=fetched.url.path,
        skills=skills,
    )


OnConflict = Literal["skip", "overwrite", "rename"]


class SkillChoice(BaseModel):
    path: str
    """ The skill's folder in the repository, as the scan listed it. """
    on_conflict: OnConflict | None = None
    """ What to do if its name is taken; without one, a taken name is an error. """
    rename_to: str | None = None


class SkillImportResult(BaseModel):
    path: str
    name: str | None
    status: Literal["imported", "overwritten", "renamed", "skipped", "failed"]
    detail: str | None = None
    skill: SkillTable | None = None


async def _install_staged(
    session: AsyncSession,
    info: RepoSkill,
    staged: Path,
    choice: SkillChoice,
    *,
    repo_url: str,
    project: ProjectTable | None,
) -> SkillImportResult:
    """Install one staged skill, applying the choice for a taken name. Commits."""
    from .project_agent import invalidate_agents

    def result(status, detail=None, skill=None, name=None) -> SkillImportResult:
        return SkillImportResult(
            path=info.path,
            name=name or info.name,
            status=status,
            detail=detail,
            skill=skill,
        )

    if info.error:
        return result("failed", info.error)
    name = info.name
    assert name is not None
    existing = await get_skill_optional(session, name)
    status: Literal["imported", "overwritten", "renamed"] = "imported"
    if existing is not None:
        if choice.on_conflict == "skip":
            return result("skipped", f"A skill named {name} is already installed.")
        if choice.on_conflict == "rename":
            new_name = (choice.rename_to or "").strip()
            if not new_name:
                return result("failed", "Give a new name to rename it to.")
            if await get_skill_optional(session, new_name) is not None:
                return result("failed", f"{new_name} is taken too.", name=new_name)
            try:
                skill_repo.rename_skill(staged, new_name)
            except SkillRepoError as e:
                return result("failed", str(e), name=new_name)
            existing, name, status = None, new_name, "renamed"
        elif choice.on_conflict == "overwrite":
            if existing.is_public:
                return result(
                    "failed",
                    f"{name} is a published skill and cannot be overwritten; "
                    "skip it or rename this one.",
                )
            status = "overwritten"
        else:
            return result(
                "failed",
                f"A skill named {name} is already installed: skip, overwrite or rename it.",
            )

    skill = read_skill(staged)
    path = new_storage_path()
    dest = settings.data_dir / path
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(staged, dest)

    old_path: str | None = None
    if existing is None:
        row = build_skill_row(
            skill,
            path=path,
            author=None,
            repo_url=repo_url,
            is_public=False,
            now=now_iso(),
        )
    else:
        row, old_path = existing, existing.path
        row.path = path
        row.description = skill.description
        row.license = skill.license
        row.compatibility = skill.compatibility
        row.allowed_tools = skill.allowed_tools
        row.skill_metadata = skill.metadata
        row.repo_url = repo_url
        row.updated_at = now_iso()
    session.add(row)
    if project is not None:
        _load_into(session, project, skill.name)
    if old_path is not None:
        # Projects that load it hold the old files in their skills volume.
        for loaded in (await session.exec(select(ProjectTable))).all():
            if skill.name in loaded.skills:
                invalidate_agents(session, project_id=loaded.id)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        shutil.rmtree(dest, ignore_errors=True)
        return result("failed", f"Name already in use: {skill.name}", name=skill.name)
    except Exception:
        await session.rollback()
        shutil.rmtree(dest, ignore_errors=True)
        raise
    if old_path is not None:
        shutil.rmtree(settings.data_dir / old_path, ignore_errors=True)
    await session.refresh(row)
    if project is not None:
        await session.refresh(project)
    return result(status, skill=row, name=skill.name)


async def import_repository_skills(
    session: AsyncSession,
    url: str,
    choices: list[SkillChoice],
    user_id: uuid.UUID,
    project: ProjectTable | None = None,
    *,
    token: str | None = None,
    commit: str | None = None,
) -> list[SkillImportResult]:
    """
    Import the chosen skills from a repository URL, each on its own: one that
    fails or is skipped does not stop the rest. With `commit` (from a scan),
    that commit is installed, so the skills are those that were listed.
    Imported skills are private and record their folder's URL as `repo_url`.
    """
    if not choices:
        raise HTTPException(
            status_code=400, detail="Choose at least one skill to import."
        )
    paths = [c.path for c in choices]
    if len(set(paths)) != len(paths):
        raise HTTPException(status_code=400, detail="A skill was chosen twice.")
    sent = await _import_token(session, url, user_id, token)
    with tempfile.TemporaryDirectory() as tmp:
        try:
            fetched, staged = await asyncio.to_thread(
                _scan, url, sent.value if sent else None, commit, Path(tmp)
            )
        except SkillRepoError as e:
            raise _repo_error(e, sent)
        by_path = {info.path: (info, dest) for info, dest in staged}
        missing = [p for p in paths if p not in by_path]
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"No skill at {missing[0]!r} in {fetched.url.web_url} at {fetched.ref}.",
            )
        # Two chosen skills that would end up with one name: refuse both up front.
        final: dict[str, list[str]] = {}
        for c in choices:
            info = by_path[c.path][0]
            if info.name:
                name = (
                    (c.rename_to or "").strip()
                    if c.on_conflict == "rename"
                    else info.name
                )
                final.setdefault(name or info.name, []).append(c.path)
        clashes = {n: ps for n, ps in final.items() if len(ps) > 1}
        if clashes:
            n, ps = next(iter(clashes.items()))
            raise HTTPException(
                status_code=409,
                detail=f"{', '.join(ps)} would all be named {n}; choose only one, or rename.",
            )
        results = []
        for c in choices:
            info, dest = by_path[c.path]
            results.append(
                await _install_staged(
                    session,
                    info,
                    dest,
                    c,
                    repo_url=fetched.url.tree_url(fetched.commit, info.path),
                    project=project,
                )
            )
    # Each skill committed on its own, which expired the rows installed before it.
    for r in results:
        if r.skill is not None:
            await session.refresh(r.skill)
    return results


async def import_skill(
    session: AsyncSession,
    url: str,
    user_id: uuid.UUID,
    project: ProjectTable | None = None,
    *,
    token: str | None = None,
    on_conflict: OnConflict | None = None,
    rename_to: str | None = None,
) -> SkillTable:
    """
    Import the one skill a repository URL names (SKILL.md at its root, or in the
    folder it links to). Private (`is_public=False`), recording the source `url`
    as its `repo_url`; given a `project`, loaded into it. A repository holding
    several skills is refused with their list: import those through
    `import_repository_skills`.
    """
    sent = await _import_token(session, url, user_id, token)
    with tempfile.TemporaryDirectory() as tmp:
        try:
            fetched, staged = await asyncio.to_thread(
                _scan, url, sent.value if sent else None, None, Path(tmp)
            )
        except SkillRepoError as e:
            raise _repo_error(e, sent)
        if len(staged) > 1:
            listed = ", ".join(info.name or info.path for info, _ in staged[:10])
            more = f" and {len(staged) - 10} more" if len(staged) > 10 else ""
            raise HTTPException(
                status_code=400,
                detail=f"{fetched.url.web_url} holds {len(staged)} skills ({listed}{more}). "
                "Choose which to import, or link one skill's folder.",
            )
        [(info, dest)] = staged
        if (
            on_conflict is None
            and info.name
            and await get_skill_optional(session, info.name)
        ):
            raise HTTPException(
                status_code=409, detail=f"Name already in use: {info.name}"
            )
        result = await _install_staged(
            session,
            info,
            dest,
            SkillChoice(path=info.path, on_conflict=on_conflict, rename_to=rename_to),
            repo_url=url,
            project=project,
        )
    if result.skill is None:
        code = 409 if result.status == "skipped" else 400
        raise HTTPException(status_code=code, detail=result.detail or "Import failed.")
    return result.skill


async def import_skill_upload(
    session: AsyncSession,
    files: list[UploadFile],
    paths: list[str],
    project: ProjectTable | None = None,
) -> SkillTable:
    """
    Import a skill from a folder uploaded by the browser. `paths[i]` is
    `files[i]`'s path relative to the picked folder. Private, like a GitHub
    import, but with no `repo_url`. Given a `project`, the skill is loaded into it.
    """
    if len(paths) != len(files):
        raise HTTPException(
            status_code=400,
            detail=f"Got {len(files)} files but {len(paths)} paths.",
        )
    path = new_storage_path()
    try:
        skill = import_skill_from_files(
            ((rel, upload.file) for rel, upload in zip(paths, files)),
            settings.data_dir / path,
        )
    except SkillImportError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return await _register_imported(
        session, skill, path, repo_url=None, project=project
    )


async def _register_imported(
    session: AsyncSession,
    skill: Skill,
    path: str,
    *,
    repo_url: str | None,
    project: ProjectTable | None,
) -> SkillTable:
    """Record a skill already copied to `path`; remove the copy on a name clash."""
    if await get_skill_optional(session, skill.name) is not None:
        shutil.rmtree(settings.data_dir / path, ignore_errors=True)
        raise HTTPException(
            status_code=409, detail=f"Name already in use: {skill.name}"
        )

    row = build_skill_row(
        skill, path=path, author=None, repo_url=repo_url, is_public=False, now=now_iso()
    )
    await _insert_skill_row(session, row, project)
    return row


class OutputSkill(BaseModel):
    """A folder in the researcher's output folder that holds a SKILL.md."""

    folder: str
    """ The folder's name under `/mnt/data/output`. """
    name: str | None
    """ The SKILL.md `name`; None when the SKILL.md does not parse (see `error`). """
    error: str | None = None
    hpc_jobs: list[str] = []
    exists: bool = False
    """ A skill with this name is already in the library. """
    replaceable: bool = False
    """ It exists and is private, so importing with `replace` swaps its files. """


_OUTPUT_FOLDER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


async def _project_output_dir(
    session: AsyncSession, project_name: str, user: ServiceUser
) -> tuple[ProjectTable, Path]:
    """The project, and the caller's own output folder in it (the sandbox's `/mnt/data/output`)."""
    # Imported here: the agent modules import the seed, which imports this module.
    from ..agents.agents import agent_volume_root
    from . import project as project_service

    if user == "system":
        raise HTTPException(
            status_code=403,
            detail="Importing from output requires an authenticated user",
        )
    project = await project_service.get_project_by_name(session, project_name, user)
    output = agent_volume_root(project.id, user.id) / "data" / "output"
    # The sandbox can replace either with a symlink to anywhere on this machine.
    if output.parent.is_symlink() or output.is_symlink():
        raise HTTPException(
            status_code=400, detail="This project's output folder is a symlink."
        )
    return project, output


async def list_output_skills(
    session: AsyncSession, project_name: str, user: ServiceUser
) -> list[OutputSkill]:
    """
    The top-level folders of the caller's output folder in `project_name` that
    hold a SKILL.md, i.e. the skills the agent wrote to `/mnt/data/output/<skill>/`.
    """
    _, output = await _project_output_dir(session, project_name, user)
    if not output.is_dir():
        return []
    library = {row.name: row for row in await list_skills(session)}
    found: list[OutputSkill] = []
    for folder in sorted(output.iterdir()):
        if (
            folder.is_symlink()
            or not folder.is_dir()
            or not _OUTPUT_FOLDER.fullmatch(folder.name)
        ):
            continue
        # Nothing in the folder is read until it has passed the import's own
        # checks: a symlinked SKILL.md would show a host file in `error`.
        try:
            files = dict(plain_files(folder))
        except SkillImportError as e:
            if find_skill_md(folder) is not None:
                found.append(OutputSkill(folder=folder.name, name=None, error=str(e)))
            continue
        skill_md = files.get("SKILL.md") or files.get("skill.md")
        if skill_md is None:
            continue
        name, error = None, None
        try:
            text = read_plain_file(skill_md, skill_md.name).decode("utf-8")
            name = parse_skill(text).name
        except (SkillError, ValidationError, SkillImportError) as e:
            error = f"SKILL.md is invalid: {e}"
        except UnicodeDecodeError:
            error = "SKILL.md is not UTF-8 text."
        existing = library.get(name) if name else None
        found.append(
            OutputSkill(
                folder=folder.name,
                name=name,
                error=error,
                hpc_jobs=skill_hpc_jobs(folder),
                exists=existing is not None,
                replaceable=existing is not None and not existing.is_public,
            )
        )
    return found


async def import_skill_from_output(
    session: AsyncSession,
    project_name: str,
    folder: str,
    user: ServiceUser,
    *,
    replace: bool = False,
) -> SkillTable:
    """
    Import `/mnt/data/output/<folder>/` from the caller's sandbox in
    `project_name` as a private skill, and load it into that project.

    Same validation and limits as a folder upload, plus no symlinks. Loading
    means adding it to `Project.skills`, the list the agent's skills volume is
    built from, and evicting the project's agents so the next message stages
    it. A name already in use is refused unless `replace` is set and that skill
    is private; then the row is kept and its files are swapped.
    """
    from .project_agent import invalidate_agents

    project, output = await _project_output_dir(session, project_name, user)
    source = output / folder
    if (
        # The name is one plain path part, so `source` is lexically inside
        # `output`; `plain_files` refuses it if it is a symlink.
        not _OUTPUT_FOLDER.fullmatch(folder) or not source.is_dir()
    ):
        raise HTTPException(
            status_code=404, detail=f"No folder {folder!r} in this project's output."
        )

    path = new_storage_path()
    try:
        skill = import_skill_from_dir(source, settings.data_dir / path)
    except SkillImportError as e:
        raise HTTPException(status_code=400, detail=str(e))

    old_path: str | None = None
    existing = await get_skill_optional(session, skill.name)
    if existing is None:
        row = build_skill_row(
            skill, path=path, author=None, repo_url=None, is_public=False, now=now_iso()
        )
    elif replace and not existing.is_public:
        row, old_path = existing, existing.path
        row.path = path
        row.description = skill.description
        row.license = skill.license
        row.compatibility = skill.compatibility
        row.allowed_tools = skill.allowed_tools
        row.skill_metadata = skill.metadata
        row.updated_at = now_iso()
    else:
        shutil.rmtree(settings.data_dir / path, ignore_errors=True)
        if existing.is_public:
            detail = f"Name already in use by a published skill: {skill.name}"
        else:
            detail = (
                f"Name already in use: {skill.name}. Import it with Replace to "
                "swap the existing private skill's files for these."
            )
        raise HTTPException(status_code=409, detail=detail)

    session.add(row)
    _load_into(session, project, skill.name)
    if old_path is not None:
        # Already loaded, so `_load_into` left the agents alone, but their
        # skills volume holds the old files: rebuild it on their next turn.
        invalidate_agents(session, project_id=project.id)
    try:
        await session.commit()
    except Exception as e:
        await session.rollback()
        shutil.rmtree(settings.data_dir / path, ignore_errors=True)
        if isinstance(e, IntegrityError):
            raise HTTPException(
                status_code=409, detail=f"Name already in use: {skill.name}"
            )
        raise
    if old_path is not None:
        shutil.rmtree(settings.data_dir / old_path, ignore_errors=True)
    await session.refresh(row)
    return row


async def generate_draft(
    message_history: list[ModelMessage],
    hint: str | None,
    user: UserPublicWithConfig | None = None,
) -> SkillDraft:
    """Draft a SKILL.md from a chat conversation. Persists nothing."""
    return await generate_skill_draft(message_history, hint, user)


async def update_skill(
    session: AsyncSession,
    name: str,
    updates: SkillUpdate,
    *,
    body: str | None = None,
) -> SkillTable:
    """
    Partial update.
    """
    skill = await get_skill(session, name)
    data = updates.model_dump(exclude_unset=True)

    # Reject disallowed transitions up front, before touching disk, so a
    # rejected PATCH never leaves a rewritten SKILL.md behind.
    for field in ("description", "is_public"):
        if field in data and data[field] is None:
            raise HTTPException(status_code=422, detail=f"{field} cannot be null")
    if data.get("is_public") is False and skill.is_public:
        # Publish is one-way: once listed on the hub a skill can't be hidden again.
        raise HTTPException(
            status_code=409, detail="Published skills cannot be unpublished."
        )

    spec_updates = {
        k: data[k]
        for k in (
            "description",
            "license",
            "compatibility",
            "allowed_tools",
            "metadata",
        )
        if k in data
    }
    if body is not None:
        spec_updates["body"] = body

    # Remember the SKILL.md we overwrite so we can restore it if the commit fails.
    skill_md_path: Path | None = None
    original_text: str | None = None
    if spec_updates:
        directory = settings.data_dir / skill.path
        skill_md_path = find_skill_md(directory)
        if skill_md_path is None:
            raise ParseError(f"SKILL.md not found in {directory}")
        original_text = skill_md_path.read_text(encoding="utf-8")
        current = parse_skill(original_text)
        # Re-validate the merged result (model_copy skips validators) so a PATCH
        # can't store a value that POST would reject, e.g. an unstripped
        # description. Note the regenerated document keeps only spec frontmatter
        # keys; any extra keys (e.g. in an imported skill) are dropped.
        try:
            merged = Skill.model_validate({**current.model_dump(), **spec_updates})
        except ValidationError as e:
            raise HTTPException(status_code=422, detail=str(e))
        skill_md_path.write_text(skill_to_markdown(merged), encoding="utf-8")
        # Re-sync mirrored spec metadata from the regenerated document.
        skill.description = merged.description
        skill.license = merged.license
        skill.compatibility = merged.compatibility
        skill.allowed_tools = merged.allowed_tools
        skill.skill_metadata = merged.metadata

    if "is_public" in data:
        skill.is_public = data["is_public"]
    if "author" in data:
        skill.author = data["author"]
    if "repo_url" in data:
        skill.repo_url = data["repo_url"]

    skill.updated_at = now_iso()
    session.add(skill)
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        if skill_md_path is not None and original_text is not None:
            skill_md_path.write_text(original_text, encoding="utf-8")
        raise
    await session.refresh(skill)
    return skill


async def delete_skill(session: AsyncSession, name: str) -> None:
    """
    Delete the skill row, and remove its on-disk directory. Published
    skills are protected (they're listed on the hub).
    """
    skill = await get_skill(session, name)
    if skill.is_public:
        raise HTTPException(
            status_code=409, detail="Published skills cannot be deleted."
        )
    directory = settings.data_dir / skill.path
    await session.delete(skill)
    await session.commit()

    try:
        shutil.rmtree(directory)
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.warning("Failed to delete skill directory %s: %s", directory, e)
