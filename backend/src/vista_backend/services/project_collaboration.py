"""
Projects on the collaboration server: publishing one with the skills it loads,
listing the server's, and importing one with the skill versions it names.

A project is an item of kind `project` whose versions have a manifest and no
blob. The manifest is built field by field from `ProjectPublic` (never by
serializing the table row, which carries ids and collaboration state), and
names each skill the project loads by reference: an item and version on the
same server, or `bundled` for a skill every installation ships with. Each
knowledge base it searches is a reference too, an item and version.

Publishing publishes every skill and knowledge base the project uses that is not
on the server in effect or has changed since (`skill_collaboration.publish_skill`,
`knowledge_base_collaboration.publish_knowledge_base`), then the project, as a
compare-and-set on `remote_version` like a skill. Importing imports the skill and
knowledge base versions that are not here, then creates the project, with an id
of its own, or updates the copy already here, and sets up its Hypothesis Lab
without letting an unreachable forum fail the import.
"""

import re
import uuid
from typing import Annotated, Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field, ValidationError, model_validator
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..agents.forum.project_forum import ForumSetupError, ensure_forum
from ..agents.skills import SKILL_NAME_PATTERN
from ..db.schemas import (
    SLUG_PATTERN,
    KnowledgeBaseTable,
    LocalSettingsPublic,
    ProjectCreate,
    ProjectPublic,
    ProjectTable,
    SkillTable,
)
from . import local_settings
from .collaboration import (
    CollaborationError,
    CollaborationRefused,
    RemoteVersion,
    SharingConflict,
    VersionConflict,
    client_for,
    list_with_heads,
    required_url,
    server_url,
)
from .project import (
    get_project_by_name,
    get_project_by_name_optional,
    project_content,
)
from .knowledge_base import get_kb_optional
from .knowledge_base_collaboration import (
    KnowledgeBaseConflict,
    check_importable,
    check_space,
    import_knowledge_base,
    linked_knowledge_base,
    manifest_of,
    publish_knowledge_base,
    require_idle,
)
from .project_agent import invalidate_agents
from .skill_collaboration import (
    LocalCopy,
    SkillConflict,
    import_skill,
    linked_skill,
    publish_skill,
    reread,
)
from .skills import get_skill, get_skill_optional

PROJECT_KIND = "project"
MANIFEST_FORMAT = 2
"""
Bumped when a project manifest changes meaning; another format is not imported.
2: knowledge bases are references (`KnowledgeBaseRef`), not slugs.
"""


class ProjectConflict(SharingConflict):
    """
    A project publish or import that needs the researcher's choice first.
    `skill_conflict`: a skill the project loads could not be published or
    imported, and is resolved on the Skills page or the Skill Hub; `skill` and
    `skill_code` say which and why. `knowledge_base_conflict`: the same for a
    knowledge base it searches (`knowledge_base`, `knowledge_base_code`),
    resolved on the Knowledge Bases page.
    """

    def __init__(
        self,
        detail: str,
        code: Literal[
            "version_conflict",
            "remote_missing",
            "modified",
            "name_taken",
            "name_invalid",
            "skill_conflict",
            "knowledge_base_conflict",
        ],
        **extra: Any,
    ):
        super().__init__(detail, code, **extra)


class SkillRef(BaseModel):
    """
    A skill a published project loads: a version of a `skill` item on the same
    server, or a skill bundled with VISTA, by name.
    """

    name: Annotated[str, Field(pattern=SKILL_NAME_PATTERN)]
    """
    Its name in the publisher's project: for a bundled skill, which one; for
    any other, only a label, as the item and version say which. A skill name,
    so never a path: a project may keep it, and it names a folder.
    """
    bundled: bool = False
    remote_id: uuid.UUID | None = None
    version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _one_kind(self) -> "SkillRef":
        if self.bundled != (self.remote_id is None or self.version is None):
            raise ValueError(
                "a skill reference is either bundled or an item and version"
            )
        return self


class KnowledgeBaseRef(BaseModel):
    """A knowledge base a published project searches: a version of a `knowledge-base` item."""

    slug: Annotated[str, Field(pattern=SLUG_PATTERN)]
    """
    Its slug in the publisher's project, a label for display and the slug an
    import starts from; the item and version say which it is.
    """
    remote_id: uuid.UUID
    version: int = Field(ge=1)


class ProjectManifest(BaseModel):
    """What a version of a project is: its fields, and what it loads by reference."""

    format: int = MANIFEST_FORMAT
    name: str
    """ The publisher's name for it; an import names a new project after the item. """
    description: str | None = None
    system_prompt: str | None = None
    tools: list[str] = []
    usage_limits: dict[str, Any] = {}
    forum_repo_url: str | None = None
    skills: list[SkillRef] = []
    knowledge_bases: list[KnowledgeBaseRef] = []


class RemoteProject(BaseModel):
    """A project on the collaboration server, as the import list shows it."""

    id: uuid.UUID
    name: str
    head_version: int
    created_by_name: str | None
    created_at: str
    published_by_name: str | None = None
    published_at: str | None = None
    manifest: ProjectManifest | None = None
    """ The head's manifest; None when it was not fetched or is not one this VISTA reads. """
    local: LocalCopy | None
    """ This installation's copy of it, if any. """
    name_taken: bool
    """ A different project here has its name, so importing it needs a new one. """


PublishStatus = Literal["published", "unchanged"]


class ProjectPublishResult(BaseModel):
    status: PublishStatus
    """ `unchanged`: what would be published is the version already there. """
    project: ProjectPublic
    skills_published: list[str]
    """ The skills published first, because they were not on the server or had changed. """
    knowledge_bases_published: list[str] = []
    """ The same for its knowledge bases, by slug. """


ImportStatus = Literal["imported", "updated", "unchanged"]
SkillOutcomeStatus = Literal["imported", "updated", "unchanged", "kept", "missing"]


class SkillOutcome(BaseModel):
    """What an import did about one skill the project loads."""

    name: str
    """ The skill's name here, or the publisher's name for one left out. """
    status: SkillOutcomeStatus
    """
    `unchanged`: the version (or bundled skill) was here already. `kept`: a
    copy here at another version, or with changes of its own, was used as it is.
    `missing`: a bundled skill this VISTA does not have; `detail` says whether
    the project still names it.
    """
    detail: str | None = None


KnowledgeBaseOutcomeStatus = Literal["imported", "updated", "unchanged", "kept"]


class KnowledgeBaseOutcome(BaseModel):
    """What an import did about one knowledge base the project searches."""

    slug: str
    """ Its slug here. """
    status: KnowledgeBaseOutcomeStatus
    """
    `unchanged`: the version was here already. `kept`: a copy here at another
    version, or with changes of its own, was used as it is; `detail` says which.
    """
    detail: str | None = None


class ProjectImportResult(BaseModel):
    status: ImportStatus
    project: ProjectPublic
    skills: list[SkillOutcome] = []
    knowledge_bases: list[KnowledgeBaseOutcome] = []
    forum_error: str | None = None
    """ Why the project's Hypothesis Lab could not be set up; the import stands. """


def _project_manifest(version: RemoteVersion) -> ProjectManifest | None:
    try:
        manifest = ProjectManifest.model_validate(version.manifest)
    except ValidationError:
        return None
    return manifest if manifest.format == MANIFEST_FORMAT else None


async def _linked_project(
    session: AsyncSession, url: str | None, item_id: uuid.UUID
) -> ProjectTable | None:
    return (
        await session.exec(
            select(ProjectTable).where(
                ProjectTable.origin_server == url, ProjectTable.remote_id == item_id
            )
        )
    ).first()


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


async def list_remote_projects(
    session: AsyncSession, local: LocalSettingsPublic, *, manifests: bool = True
) -> list[RemoteProject]:
    """The server's projects, each with any copy here and, with `manifests`, its head."""
    url, _ = server_url(local)
    listed_items = await list_with_heads(local, PROJECT_KIND, heads=manifests)
    rows = list((await session.exec(select(ProjectTable))).all())
    linked = {r.remote_id: r for r in rows if r.remote_id and r.origin_server == url}
    names = {r.name for r in rows}
    listed = []
    for item, fetched in listed_items:
        copy = linked.get(item.id)
        listed.append(
            RemoteProject(
                id=item.id,
                name=item.name,
                head_version=item.head_version,
                created_by_name=item.created_by_name,
                created_at=item.created_at,
                published_by_name=fetched.created_by_name if fetched else None,
                published_at=fetched.created_at if fetched else None,
                manifest=_project_manifest(fetched) if fetched else None,
                local=LocalCopy(
                    name=copy.name,
                    remote_version=copy.remote_version,
                    modified=copy.modified,
                )
                if copy
                else None,
                name_taken=copy is None and item.name in names,
            )
        )
    return listed


# ---------------------------------------------------------------------------
# Publishing
# ---------------------------------------------------------------------------


async def _publish_skills(
    session: AsyncSession,
    local: LocalSettingsPublic,
    project: str,
    names: list[str],
    carried: set[str],
) -> tuple[list[SkillRef], list[str]]:
    """
    A reference to each skill in `names`, publishing first each one that is not
    on the server in effect or has changed since. Returns the references and
    the names published.

    `carried` are the names the version built on referenced as bundled: one
    that is not here (bundled with another VISTA, and kept by the import) is
    referenced as bundled again. Any other skill that is not here, or one that
    needs the researcher's choice, stops the project's publish before it is
    sent.
    """
    here = {name: await get_skill_optional(session, name) is not None for name in names}
    absent = [name for name in names if not here[name] and name not in carried]
    if absent:
        raise HTTPException(
            status_code=400,
            detail=f"{project} loads {', '.join(absent)}, which "
            f"{'is' if len(absent) == 1 else 'are'} not on this computer. Remove "
            "them from the project before publishing it.",
        )
    refs: list[SkillRef] = []
    published: list[str] = []
    for name in names:
        if not here[name]:
            refs.append(SkillRef(name=name, bundled=True))
            continue
        # Read in the loop: each publish ends the transaction, expiring rows.
        row = await get_skill(session, name)
        if row.bundled:
            refs.append(SkillRef(name=name, bundled=True))
            continue
        was = (row.origin_server, row.remote_id, row.remote_version)
        try:
            row = await publish_skill(session, local, name)
        except SkillConflict as e:
            raise ProjectConflict(
                f"{project} was not published: its skill {name} needs a choice "
                f"first. {e.detail} Resolve it on the Skills page, then publish "
                "the project again.",
                "skill_conflict",
                skill=name,
                skill_code=e.code,
            ) from e
        if (row.origin_server, row.remote_id, row.remote_version) != was:
            published.append(name)
        assert row.remote_id is not None and row.remote_version is not None
        refs.append(
            SkillRef(name=name, remote_id=row.remote_id, version=row.remote_version)
        )
    return refs, published


async def _check_knowledge_bases(
    session: AsyncSession,
    project: str,
    slugs: list[str],
    carried: dict[str, KnowledgeBaseRef],
) -> None:
    """
    Refuse, before anything is published, a project that searches a knowledge
    base that is not here and that the version built on did not name, or one
    that is indexing.
    """
    absent = []
    for slug in slugs:
        kb = await get_kb_optional(session, slug)
        if kb is None:
            if slug not in carried:
                absent.append(slug)
        else:
            require_idle(kb, f"publish {project}")
    if absent:
        raise HTTPException(
            status_code=400,
            detail=f"{project} searches the knowledge "
            f"{'base' if len(absent) == 1 else 'bases'} {', '.join(absent)}, "
            f"which {'is' if len(absent) == 1 else 'are'} not on this computer. "
            "Remove them from the project before publishing it.",
        )


async def _publish_knowledge_bases(
    session: AsyncSession,
    local: LocalSettingsPublic,
    project: str,
    slugs: list[str],
    carried: dict[str, KnowledgeBaseRef],
) -> tuple[list[KnowledgeBaseRef], list[str]]:
    """
    A reference to each knowledge base in `slugs`, publishing first each one
    that is not on the server in effect or has changed since. Returns the
    references and the slugs published. One that is not here is referenced
    as the version built on referenced it (`carried`, checked by
    `_check_knowledge_bases`); one that needs the researcher's choice stops
    the project's publish before it is sent.
    """
    refs: list[KnowledgeBaseRef] = []
    published: list[str] = []
    for slug in slugs:
        if await get_kb_optional(session, slug) is None:
            refs.append(carried[slug])
            continue
        try:
            result = await publish_knowledge_base(session, local, slug)
        except KnowledgeBaseConflict as e:
            raise ProjectConflict(
                f"{project} was not published: its knowledge base {slug} needs a "
                f"choice first. {e.detail} Resolve it on the Knowledge Bases page, "
                "then publish the project again.",
                "knowledge_base_conflict",
                knowledge_base=slug,
                knowledge_base_code=e.code,
            ) from e
        kb = result.knowledge_base
        if result.status == "published":
            published.append(slug)
        assert kb.remote_id is not None and kb.remote_version is not None
        refs.append(
            KnowledgeBaseRef(
                slug=slug, remote_id=kb.remote_id, version=kb.remote_version
            )
        )
    return refs, published


def _build_manifest(
    public: ProjectPublic,
    skills: list[SkillRef],
    knowledge_bases: list[KnowledgeBaseRef],
) -> ProjectManifest:
    """The manifest, field by field: nothing of the row goes that is not named here."""
    return ProjectManifest(
        name=public.name,
        description=public.description,
        system_prompt=public.system_prompt,
        tools=list(public.tools),
        usage_limits=dict(public.usage_limits),
        forum_repo_url=public.forum_repo_url,
        skills=skills,
        knowledge_bases=knowledge_bases,
    )


def _blank_to_none(value: Any) -> Any:
    return None if isinstance(value, str) and not value.strip() else value


def _references(manifest: ProjectManifest) -> list[tuple[Any, ...]]:
    """
    Which skills a manifest loads, and which knowledge bases it searches: a
    bundled skill by name, anything else by item and version (its name is a
    label, which an import may have changed here).
    """
    return [
        ("bundled", ref.name) if ref.bundled else (ref.remote_id, ref.version)
        for ref in manifest.skills
    ] + [
        ("knowledge-base", ref.remote_id, ref.version)
        for ref in manifest.knowledge_bases
    ]


def _stale(
    name: str, parent: int, head: int, remote_version: int | None
) -> ProjectConflict:
    return ProjectConflict(
        f"{name} has been published since version {parent}: the server's newest "
        f"is version {head}.",
        "version_conflict",
        head_version=head,
        remote_version=remote_version,
    )


def _gone(name: str, url: str) -> ProjectConflict:
    return ProjectConflict(
        f"{name} is no longer on the collaboration server at {url}; publish it "
        "as a new project.",
        "remote_missing",
    )


async def publish_project(
    session: AsyncSession,
    local: LocalSettingsPublic,
    name: str,
    *,
    on_top_of: int | None = None,
    as_new: bool = False,
) -> ProjectPublishResult:
    """
    Publish the project as the next version of its item on the server in
    effect, or as a new item if it has none there (or `as_new`), after
    publishing the skills it loads and the knowledge bases it searches that
    are not there or have changed.

    The next version is built on `remote_version`, or on `on_top_of`, a head the
    researcher was shown. A head past it raises `ProjectConflict`
    (`version_conflict`): asked before any skill is published, and again by the
    compare-and-set, which a publish in between can still lose, leaving the
    skills published. A project whose manifest would equal the version it
    records is not published again, unless `on_top_of` or `as_new` asks: as
    the project is not modified, its own fields are that version's, so only
    the skill and knowledge base versions it uses can differ.
    A write to the project while it is published leaves it modified.
    """
    row = await get_project_by_name(session, name)
    url = required_url(local)
    public = ProjectPublic.model_validate(row)
    before = project_content(row)
    linked = row.remote_id is not None and row.origin_server == url and not as_new

    async with client_for(local) as client:
        parent: int | None = None
        # The version this one is built on, if this VISTA reads it.
        base: ProjectManifest | None = None
        if linked:
            assert public.remote_id is not None and public.remote_version is not None
            parent = on_top_of if on_top_of is not None else public.remote_version
            try:
                head = (await client.get_item(public.remote_id)).head_version
                if head == parent:
                    base = _project_manifest(
                        await client.get_version(public.remote_id, parent)
                    )
            except CollaborationRefused as e:
                if e.status_code != 404:
                    raise
                raise _gone(name, url) from e
            if head != parent:
                raise _stale(name, parent, head, public.remote_version)

        carried = {ref.name for ref in base.skills if ref.bundled} if base else set()
        carried_kbs = {ref.slug: ref for ref in base.knowledge_bases} if base else {}
        slugs = list(dict.fromkeys(public.knowledge_bases))
        await _check_knowledge_bases(session, name, slugs, carried_kbs)
        refs, published = await _publish_skills(
            session, local, name, list(dict.fromkeys(public.skills)), carried
        )
        kb_refs, kbs_published = await _publish_knowledge_bases(
            session, local, name, slugs, carried_kbs
        )
        new = _build_manifest(public, refs, kb_refs)
        manifest = new.model_dump(mode="json")

        if linked:
            assert public.remote_id is not None and parent is not None
            if on_top_of is None and not public.modified and base is not None:
                if _references(base) == _references(new):
                    await reread(session, row)
                    return ProjectPublishResult(
                        status="unchanged",
                        project=ProjectPublic.model_validate(row),
                        skills_published=published,
                        knowledge_bases_published=kbs_published,
                    )
            try:
                version = await client.publish_version(
                    public.remote_id, parent_version=parent, manifest=manifest
                )
            except VersionConflict as e:
                raise _stale(name, parent, e.head_version, public.remote_version) from e
            except CollaborationRefused as e:
                if e.status_code != 404:
                    raise
                raise _gone(name, url) from e
            remote_id, remote_version = public.remote_id, version.version
        else:
            item = await client.create_item(PROJECT_KIND, name, manifest=manifest)
            remote_id, remote_version = item.id, item.head_version

    await reread(session, row)
    row.origin_server = url
    row.remote_id = remote_id
    row.remote_version = remote_version
    row.modified = project_content(row) != before
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return ProjectPublishResult(
        status="published",
        project=ProjectPublic.model_validate(row),
        skills_published=published,
        knowledge_bases_published=kbs_published,
    )


# ---------------------------------------------------------------------------
# Importing
# ---------------------------------------------------------------------------


async def _free_skill_name(session: AsyncSession, base: str) -> str:
    """`base`, or the first `<base>-<n>` from 2 that no skill here has."""
    taken = set((await session.exec(select(SkillTable.name))).all())
    name, n = base, 2
    while name in taken:
        name, n = f"{base}-{n}", n + 1
    return name


def _skill_blocked(project: str, ref: SkillRef, e: SkillConflict) -> ProjectConflict:
    """A skill's own conflict, under the project's `skill_conflict`."""
    return ProjectConflict(
        f"{project} was not imported: its skill {ref.name} needs a choice first. "
        f"{e.detail} Resolve it on the Skill Hub, then import the project again.",
        "skill_conflict",
        skill=ref.name,
        skill_code=e.code,
    )


async def _import_skills(
    session: AsyncSession,
    local: LocalSettingsPublic,
    url: str,
    project: str,
    refs: list[SkillRef],
) -> tuple[list[str], list[SkillOutcome], bool]:
    """
    Make each referenced skill available here, importing the versions that are
    not; returns the names the project loads, what was done about each, and
    whether the project here then lost a reference the version has (`diverged`).

    A copy here of the same item is updated to the referenced version when it is
    older and unchanged here; a newer copy, or one with changes of its own, is
    kept as it is and said so, and the project loads it: like a skill edited
    after its project was published, that is the skill's own state, not the
    project's. A bundled skill this VISTA does not have keeps
    its name in the project, unless a different skill here has that name. A
    skill's own conflict stops the import as `skill_conflict`.
    """
    names: list[str] = []
    outcomes: list[SkillOutcome] = []
    # Only a reference the project here loses: kept copies are references too,
    # to the versions here, which a publish from here publishes first.
    diverged = False
    for ref in refs:
        if ref.bundled:
            row = await get_skill_optional(session, ref.name)
            if row is not None and row.bundled:
                names.append(row.name)
                outcomes.append(SkillOutcome(name=row.name, status="unchanged"))
            elif row is None:
                # Kept: dropping it would drop it from the next version too.
                names.append(ref.name)
                outcomes.append(
                    SkillOutcome(
                        name=ref.name,
                        status="missing",
                        detail="Bundled with the publisher's VISTA, not this one. "
                        "The project still names it, and loads it once a skill of "
                        "that name is here.",
                    )
                )
            else:
                diverged = True
                outcomes.append(
                    SkillOutcome(
                        name=ref.name,
                        status="missing",
                        detail="Bundled with the publisher's VISTA, not this one, "
                        "and a different skill here has its name, so the project "
                        "does not load it.",
                    )
                )
            continue
        assert ref.remote_id is not None and ref.version is not None
        copy = await linked_skill(session, url, ref.remote_id)
        try:
            if copy is None:
                row, _ = await import_skill(
                    session,
                    local,
                    ref.remote_id,
                    version=ref.version,
                    rename_to=await _free_skill_name(session, ref.name),
                )
                names.append(row.name)
                outcomes.append(SkillOutcome(name=row.name, status="imported"))
                continue
            # Read now: an import ends the transaction, which expires the row.
            name, here, modified = copy.name, copy.remote_version or 0, copy.modified
            if here == ref.version and not modified:
                outcome = SkillOutcome(name=name, status="unchanged")
            elif here < ref.version and not modified:
                await import_skill(session, local, ref.remote_id, version=ref.version)
                outcome = SkillOutcome(name=name, status="updated")
            else:
                why = (
                    f"has changes made here since version {here}"
                    if modified
                    else f"is version {here} here"
                )
                outcome = SkillOutcome(
                    name=name,
                    status="kept",
                    detail=f"The project uses version {ref.version}; the copy here "
                    f"{why}, and was kept.",
                )
        except SkillConflict as e:
            raise _skill_blocked(project, ref, e) from e
        names.append(outcome.name)
        outcomes.append(outcome)
    return list(dict.fromkeys(names)), outcomes, diverged


async def _free_slug(session: AsyncSession, base: str) -> str:
    """`base`, or the first `<base>-<n>` from 2 that no knowledge base here has."""
    taken = set((await session.exec(select(KnowledgeBaseTable.slug))).all())
    slug, n = base, 2
    while slug in taken:
        slug = f"{base[: 79 - len(str(n))]}-{n}"
        n += 1
    return slug


KbPlan = Literal["import", "update", "unchanged", "kept"]


async def _plan_knowledge_bases(
    session: AsyncSession,
    local: LocalSettingsPublic,
    url: str,
    project: str,
    refs: list[KnowledgeBaseRef],
) -> tuple[list[KbPlan], dict[uuid.UUID, RemoteVersion]]:
    """
    What the import will do about each knowledge base, checked before anything
    is imported: a version this VISTA would download must be one whose index
    it can search (else `409`), a copy it would update must not be indexing
    (`409`), and all it would download must fit on the disk (`507`), else the
    whole import is refused. Also returns each version to download, by item,
    so the import does not fetch it again.
    """
    plans: list[KbPlan] = []
    fetch: list[KnowledgeBaseRef] = []
    fetched: dict[uuid.UUID, RemoteVersion] = {}
    for ref in refs:
        copy = await linked_knowledge_base(session, url, ref.remote_id)
        if copy is None:
            plan: KbPlan = "import"
        elif copy.remote_version == ref.version and not copy.modified:
            plan = "unchanged"
        elif (copy.remote_version or 0) < ref.version and not copy.modified:
            plan = "update"
            require_idle(copy, f"import {project}")
        else:
            plan = "kept"
        plans.append(plan)
        if plan in ("import", "update"):
            fetch.append(ref)
    if fetch:
        download = 0
        async with client_for(local) as client:
            for ref in fetch:
                remote = await client.get_version(ref.remote_id, ref.version)
                fetched[ref.remote_id] = remote
                kb_manifest = manifest_of(remote)
                if kb_manifest is None:
                    raise CollaborationError(
                        f"{project} searches {ref.slug}, whose version {ref.version} "
                        "on the collaboration server is not a knowledge base this "
                        "VISTA can import."
                    )
                try:
                    await check_importable(kb_manifest)
                except HTTPException as e:
                    raise HTTPException(
                        status_code=e.status_code,
                        detail=f"{project} was not imported: {e.detail}",
                    ) from e
                download += kb_manifest.archive_size
        check_space(download, f"Importing {project}'s knowledge bases")
    return plans, fetched


async def _import_knowledge_bases(
    session: AsyncSession,
    local: LocalSettingsPublic,
    url: str,
    project: str,
    refs: list[KnowledgeBaseRef],
    plans: list[KbPlan],
    fetched: dict[uuid.UUID, RemoteVersion],
) -> tuple[list[str], list[KnowledgeBaseOutcome]]:
    """
    Make each referenced knowledge base available here, as `plans` says;
    returns the slugs the project searches and what was done about each. A new
    one takes the reference's slug, or the first free `<slug>-<n>`. A copy
    here newer, or with changes of its own, is kept and used, like a skill's.
    A knowledge base's own conflict stops the import as
    `knowledge_base_conflict`.
    """
    slugs: list[str] = []
    outcomes: list[KnowledgeBaseOutcome] = []
    for ref, plan in zip(refs, plans):
        try:
            if plan == "import":
                kb = (
                    await import_knowledge_base(
                        session,
                        local,
                        ref.remote_id,
                        version=ref.version,
                        rename_to=await _free_slug(session, ref.slug),
                        planned=fetched[ref.remote_id],
                    )
                ).knowledge_base
                outcome = KnowledgeBaseOutcome(slug=kb.slug, status="imported")
            else:
                copy = await linked_knowledge_base(session, url, ref.remote_id)
                assert copy is not None  # planned from it
                slug, here, modified = copy.slug, copy.remote_version, copy.modified
                if plan == "update":
                    await import_knowledge_base(
                        session,
                        local,
                        ref.remote_id,
                        version=ref.version,
                        planned=fetched[ref.remote_id],
                    )
                    outcome = KnowledgeBaseOutcome(slug=slug, status="updated")
                elif plan == "unchanged":
                    outcome = KnowledgeBaseOutcome(slug=slug, status="unchanged")
                else:
                    why = (
                        f"has changes made here since version {here}"
                        if modified
                        else f"is version {here} here"
                    )
                    outcome = KnowledgeBaseOutcome(
                        slug=slug,
                        status="kept",
                        detail=f"The project uses version {ref.version}; the copy "
                        f"here {why}, and was kept.",
                    )
        except KnowledgeBaseConflict as e:
            raise ProjectConflict(
                f"{project} was not imported: its knowledge base {ref.slug} needs a "
                f"choice first. {e.detail} Resolve it on the Knowledge Bases page, "
                "then import the project again.",
                "knowledge_base_conflict",
                knowledge_base=ref.slug,
                knowledge_base_code=e.code,
            ) from e
        slugs.append(outcome.slug)
        outcomes.append(outcome)
    return list(dict.fromkeys(slugs)), outcomes


def _name_taken(name: str) -> ProjectConflict:
    return ProjectConflict(
        f"A project named {name} is already here. Choose a new name to import "
        "this one as.",
        "name_taken",
        name=name,
    )


PROJECT_NAME = re.compile(r"[\w .-]{2,80}", re.ASCII)
""" What the projects page accepts as a name: it is the path segment of every project route. """


def _check_name(name: str, *, chosen: bool) -> None:
    """
    Refuse a name no project route could reach. `chosen`: the researcher typed
    it; otherwise it is the item's, and they are asked for one (`name_invalid`).
    """
    if PROJECT_NAME.fullmatch(name):
        return
    rule = "A project name is 2 to 80 letters, digits, spaces, '.', '_' or '-'."
    if chosen:
        raise HTTPException(status_code=400, detail=f"{name!r} cannot be used. {rule}")
    raise ProjectConflict(
        f"{name} cannot be a project name here. {rule} Choose a name to import it as.",
        "name_invalid",
        name=name,
    )


def _changed_here(row: ProjectTable, number: int) -> ProjectConflict:
    return ProjectConflict(
        f"{row.name} differs here from version {row.remote_version}: it was "
        "changed here, or does not load a skill that version names. "
        f"Importing version {number} replaces the project here.",
        "modified",
        name=row.name,
    )


async def import_project(
    session: AsyncSession,
    local: LocalSettingsPublic,
    item_id: uuid.UUID,
    *,
    version: int | None = None,
    rename_to: str | None = None,
    replace_modified: bool = False,
) -> ProjectImportResult:
    """
    Import a version (by default the head) of a project on the server in
    effect, with the skill and knowledge base versions it uses that are not
    here.

    The project already here from the same item is updated in place, keeping
    its id and name; with local changes, that needs `replace_modified`, else
    `ProjectConflict` (`modified`), as does a change made while importing.
    Otherwise a new project is made with an id of its own, named after the
    item or `rename_to`; a name in use is `name_taken`. Both are checked before
    any skill is imported.

    The project's fields are checked before any skill is imported too, and so
    is every knowledge base it would download (`_plan_knowledge_bases`). A
    project that ends up differing from the version, because a bundled skill
    here shadows one it names, is marked modified.

    Its Hypothesis Lab is set up as a save would, but a forum that cannot be
    reached does not fail the import: the reason is in `forum_error`.
    """
    url = required_url(local)
    async with client_for(local) as client:
        item = await client.get_item(item_id)
        if item.kind != PROJECT_KIND:
            raise HTTPException(
                status_code=400,
                detail=f"{item.name} on the collaboration server is a {item.kind}, "
                "not a project.",
            )
        number = version if version is not None else item.head_version
        remote = await client.get_version(item_id, number)
    manifest = _project_manifest(remote)
    if manifest is None:
        raise CollaborationError(
            f"Version {number} of {item.name} on the collaboration server is not "
            "a project this VISTA can import."
        )

    existing = await _linked_project(session, url, item_id)
    if existing is not None:
        if existing.remote_version == number and not existing.modified:
            return ProjectImportResult(
                status="unchanged", project=ProjectPublic.model_validate(existing)
            )
        if existing.modified and not replace_modified:
            raise _changed_here(existing, number)
        name, before = existing.name, project_content(existing)
    else:
        chosen = (rename_to or "").strip()
        name = chosen or item.name
        _check_name(name, chosen=bool(chosen))
        if await get_project_by_name_optional(session, name) is not None:
            raise _name_taken(name)

    try:
        # Checked now, before any skill: the skills are filled in once imported.
        fields = ProjectCreate(
            name=name,
            description=_blank_to_none(manifest.description),
            system_prompt=_blank_to_none(manifest.system_prompt),
            skills=[],
            knowledge_bases=[],
            tools=manifest.tools,
            usage_limits=manifest.usage_limits,
            forum_repo_url=_blank_to_none(manifest.forum_repo_url),
        )
    except ValidationError as e:
        raise CollaborationError(
            f"Version {number} of {item.name} on the collaboration server is not "
            f"a project this VISTA can import: {e.errors()[0]['msg']}."
        ) from e

    kb_refs = list({ref.remote_id: ref for ref in manifest.knowledge_bases}.values())
    kb_plans, kb_fetched = await _plan_knowledge_bases(
        session, local, url, name, kb_refs
    )
    skills, outcomes, diverged = await _import_skills(
        session, local, url, name, manifest.skills
    )
    fields.skills = skills
    fields.knowledge_bases, kb_outcomes = await _import_knowledge_bases(
        session, local, url, name, kb_refs, kb_plans, kb_fetched
    )

    if existing is None:
        # Asked again: the skills took a while, and project names are unique
        # only by this check.
        if await get_project_by_name_optional(session, name) is not None:
            raise _name_taken(name)
        # An id of its own, never one from elsewhere: the seeded projects share
        # fixed ids across installations.
        row = ProjectTable.model_validate(fields)
        row.origin_server = url
        row.remote_id = item_id
        status: ImportStatus = "imported"
    else:
        await reread(session, existing)
        if project_content(existing) != before:
            raise _changed_here(existing, number)
        row = existing
        for key, value in fields.model_dump().items():
            setattr(row, key, value)
        invalidate_agents(session, project_id=row.id)
        status = "updated"
    row.remote_version = number
    row.modified = diverged
    session.add(row)
    await session.commit()
    await session.refresh(row)

    forum_error = None
    try:
        await ensure_forum(row, await local_settings.get(session))
    except ForumSetupError as e:
        forum_error = str(e)
    await session.refresh(row)
    return ProjectImportResult(
        status=status,
        project=ProjectPublic.model_validate(row),
        skills=outcomes,
        knowledge_bases=kb_outcomes,
        forum_error=forum_error,
    )
