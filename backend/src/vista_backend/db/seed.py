import logging
import shutil
import uuid
from pathlib import Path
from contextlib import nullcontext
from urllib.parse import quote

import httpx
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import select, func
from sqlmodel.ext.asyncio.session import AsyncSession

from ..config import settings
from .schemas import (
    KnowledgeBaseTable,
    ProjectMemberTable,
    ProjectTable,
    SkillTable,
    UserTable,
)
from ..agents.skills import read_skill
from ..services._helpers import new_storage_path
from ..services.skills import build_skill_row
from ..utils.misc import now_iso


SYSTEM_PROMPTS = Path(__file__).parent / "system_prompts"
SKILLS_SRC = Path(__file__).parent / "skills"
REPO_ROOT = Path(__file__).parents[4]

SKILL_ASSETS = {
    "model-fine-tuning": {
        "assets/Molten_Salt_Thermophysical_Properties.csv": "mstdb/Molten_Salt_Thermophysical_Properties.csv"
    },
    "salt-analysis": {
        "assets/Molten_Salt_Thermophysical_Properties.json": "mstdb/Molten_Salt_Thermophysical_Properties.json"
    },
    "salt-prediction": {
        "assets/Molten_Salt_Thermophysical_Properties.json": "mstdb/Molten_Salt_Thermophysical_Properties.json",
        "assets/elemental-properties.csv": "mstdb/elemental-properties.csv",
    },
}
""" Bundled skills whose assets come from vista-data: `{skill: {dest in skill: src in vista-data}}`. """


class GitlabRepoClient:
    """A client scoped to the vista-data repository API, authed with the gitlab token."""

    def __init__(self, domain: str, repo: str, token: str | None = None) -> None:
        self._client = httpx.AsyncClient(
            base_url=f"https://{domain}/api/v4/projects/{quote(repo, safe='')}/repository",
            headers={"PRIVATE-TOKEN": token or ""},
            timeout=60,
        )

    async def __aenter__(self) -> "GitlabRepoClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self._client.aclose()

    async def _get(self, url: str, params: dict) -> httpx.Response:
        resp = await self._client.get(url, params=params)
        resp.raise_for_status()
        return resp

    async def download_file(self, repo_path: str, dest: Path) -> None:
        logging.info(f"Downloading {repo_path} to {dest}..")
        resp = await self._get(
            f"/files/{quote(repo_path, safe='')}/raw", params={"ref": "main"}
        )
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        tmp.write_bytes(resp.content)
        tmp.replace(dest)

    async def _list_tree(self, repo_dir: str) -> list[dict]:
        blobs: list[dict] = []
        page = 1
        while page:
            resp = await self._get(
                "/tree",
                params={
                    "path": repo_dir,
                    "ref": "main",
                    "recursive": "true",
                    "per_page": 100,
                    "page": page,
                },
            )
            blobs.extend(entry for entry in resp.json() if entry["type"] == "blob")
            next_page = resp.headers.get("x-next-page")
            page = int(next_page) if next_page else 0
        return blobs

    async def has(self, repo_path: str) -> bool:
        """Whether the directory `repo_path` exists in vista-data and is not empty."""
        try:
            resp = await self._get(
                "/tree", params={"path": repo_path, "ref": "main", "per_page": 1}
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return False
            raise
        return bool(resp.json())

    async def download_dir(self, repo_dir: str, dest: Path) -> None:
        # Gitlab's archive endpoint tends to time out so download files individually
        dest = dest.resolve()
        logging.info(f"Downloading {repo_dir} to {dest}..")
        blobs = sorted(await self._list_tree(repo_dir), key=lambda e: e["path"])
        for blob in blobs:
            rel = Path(blob["path"]).relative_to(repo_dir)
            out = (dest / rel).resolve()
            if not out.is_relative_to(dest):
                raise ValueError(f"Tree entry escapes destination: {blob['path']}")
            if out.exists():
                continue  # already fetched on a prior run
            await self.download_file(blob["path"], out)


class LocalRepoClient:
    """
    A stand-in for `GitlabRepoClient` reading an already-unpacked copy of the
    vista-data repository from disk.

    The prebuilt package ships the payload instead of a token, so seeding has to
    resolve the same repo-relative paths (`mstdb/...`, `molten-salt-papers/...`)
    without reaching `code.ornl.gov`. Deliberately the same two-method surface
    and the same skip-if-present behavior as its GitLab counterpart, so the four
    downstream truthiness gates in `seed_db` treat the two identically.

    A missing path raises rather than warns: unlike a failed network fetch, an
    absent file in a bundled payload is a packaging defect, and every caller
    here asks only for files the payload is supposed to contain.
    """

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    async def __aenter__(self) -> "LocalRepoClient":
        if not self._root.is_dir():
            raise FileNotFoundError(
                f"Bundled data payload directory does not exist: {self._root}"
            )
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    def _resolve(self, repo_path: str) -> Path:
        src = (self._root / repo_path).resolve()
        if not src.is_relative_to(self._root):
            raise ValueError(f"Payload path escapes the payload root: {repo_path}")
        return src

    async def has(self, repo_path: str) -> bool:
        """Whether `repo_path` exists in the payload, as a file or a directory."""
        return self._resolve(repo_path).exists()

    async def download_file(self, repo_path: str, dest: Path) -> None:
        src = self._resolve(repo_path)
        if not src.is_file():
            raise FileNotFoundError(f"Bundled data payload is missing {repo_path}")
        logging.info(f"Copying {repo_path} from the bundled payload to {dest}..")
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Written to a sibling and renamed, matching the GitLab client: seeding
        # is resumable, and a half-copied file left behind by an interrupted run
        # would be skipped as "already fetched" on the next one.
        tmp = dest.with_name(dest.name + ".part")
        shutil.copyfile(src, tmp)
        tmp.replace(dest)

    async def download_dir(self, repo_dir: str, dest: Path) -> None:
        src_dir = self._resolve(repo_dir)
        if not src_dir.is_dir():
            raise FileNotFoundError(
                f"Bundled data payload is missing the {repo_dir} directory"
            )
        dest = dest.resolve()
        logging.info(f"Copying {repo_dir} from the bundled payload to {dest}..")
        for src in sorted(p for p in src_dir.rglob("*") if p.is_file()):
            out = (dest / src.relative_to(src_dir)).resolve()
            if not out.is_relative_to(dest):
                raise ValueError(f"Payload entry escapes destination: {src}")
            if out.exists():
                continue  # already copied on a prior run
            await self.download_file(
                str(src.relative_to(self._root)).replace("\\", "/"), out
            )


async def _build_knowledge_base(kb_dir: Path):
    """
    Build the ChromaDB store under `kb_dir/rag_db` by embedding the PDFs in
    `kb_dir/pdfs`. Skips if already exists.
    """
    from ..utils import indexer

    pdfs_dir = kb_dir / "pdfs"
    rag_db = kb_dir / "rag_db"

    if (rag_db / "chroma.sqlite3").exists():
        return

    filenames = sorted(
        str(p.relative_to(pdfs_dir)).replace("\\", "/") for p in pdfs_dir.rglob("*.pdf")
    )
    logging.info(
        f"Embedding {len(filenames)} PDF(s) into the knowledge base at {rag_db}."
    )
    # No user exists yet at first-run seeding, so this resolves from
    # `Settings` alone. It matters for a plain checkout indexing the corpus
    # itself; the prebuilt package ships a store that already has citations,
    # so `_build_knowledge_base` returns before reaching this.
    from ..agents.inference import citation_credentials

    results = await indexer.index_publications(
        rag_db_path=str(rag_db),
        pdfs_dir=str(pdfs_dir),
        filenames=filenames,
        llm_credentials=citation_credentials(),
    )

    failed = [r.get("filename") for r in results if r.get("status") == "failed"]
    if failed:
        logging.warning(
            f"Knowledge base: {len(failed)} PDF(s) failed to index: {failed}"
        )
    logging.info(f"Knowledge base built at {rag_db}")


def _assert_knowledge_base_indexed(kb_dir: Path) -> None:
    """
    Refuse to record a knowledge base whose vector store holds nothing.

    The row below hardcodes `build_status="ready"`, so an absent or empty store
    produces a knowledge base that reports itself healthy and returns no
    passages -- the one failure in the seeding path a researcher could not
    diagnose from the interface. It matters most for the prebuilt package, where
    the store is copied in rather than built here (`_build_knowledge_base`
    returns early when `chroma.sqlite3` exists), so a packaging mistake would
    otherwise ship silently.

    Counted through Chroma's own API rather than by reading `chroma.sqlite3`,
    whose table layout is internal. `get_collection` attaches Chroma's default
    embedding function, but that only reaches the network inside `__call__` and
    `count()` never calls it -- the same invariant `build_rag.py` documents at
    its `get_or_create_collection` calls.
    """
    import chromadb
    from chromadb.config import Settings as ChromaSettings

    rag_db = kb_dir / "rag_db"
    corpus = f"{kb_dir.name} ({rag_db})"

    if not (rag_db / "chroma.sqlite3").is_file():
        raise RuntimeError(
            f"Knowledge base {corpus} has no vector store; refusing to seed it "
            "as ready. Expected either a prebuilt store or PDFs to index in "
            f"{kb_dir / 'pdfs'}."
        )

    client = chromadb.PersistentClient(
        path=str(rag_db), settings=ChromaSettings(anonymized_telemetry=False)
    )
    try:
        count = client.get_collection("text_chunks").count()
    except Exception as exc:  # noqa: BLE001 -- chroma raises several types here
        raise RuntimeError(
            f"Knowledge base {corpus} has an unreadable vector store: {exc}"
        ) from exc

    if count == 0:
        raise RuntimeError(
            f"Knowledge base {corpus} has an empty vector store; refusing to "
            "seed it as ready. Retrieval would return nothing while the "
            "interface reported the corpus as built."
        )
    logging.info(f"Knowledge base {kb_dir.name} holds {count} indexed chunk(s).")


def _from_payload() -> bool:
    """Whether `_vista_data_client()` reads a bundled payload rather than GitLab."""
    return bool(settings.vista_data_payload_dir)


def _vista_data_client():
    """
    The vista-data client for this deployment, as an async context manager, or
    `nullcontext()` when there is none.

    Payload before token: when both are configured the payload is already on
    disk, so fetching over the network could only produce the same files more
    slowly. Ordered explicitly rather than left to whichever happens to be
    checked first, because the prebuilt package sets the payload path while a
    developer's inherited `.env` may still carry a token.
    """
    payload = settings.vista_data_payload_dir
    if payload:
        return LocalRepoClient(payload)
    if settings.vista_data_token:
        return GitlabRepoClient(
            "code.ornl.gov", "v28/vista-data", token=settings.vista_data_token
        )
    return nullcontext()


async def _science_enabled(client) -> bool:
    """
    Whether `seed_db` seeds the science projects (`molten-salt`, `alloy-design`).

    A payload is seeded by what it contains and the setting is not consulted:
    the science projects need both the molten-salt corpus and MSTDB. From a
    token, the `seed_science_projects` setting decides. With no client, never.
    """
    if client is None:
        return False
    if _from_payload():
        return await client.has("molten-salt-papers") and await client.has("mstdb")
    return settings.seed_science_projects


async def seed_db(engine: AsyncEngine) -> None:
    """
    Seed the DB with default data on first run, and a no-op thereafter.
    """
    async with AsyncSession(engine) as session:
        count = (
            await session.exec(select(func.count()).select_from(ProjectTable))
        ).one()
        if count > 0:
            return  # Already seeded

    logging.info(
        "Seeding database and data dir with initial data (this can take a bit)..."
    )
    molten_salt_kb_dir = settings.knowledge_bases_dir / "molten-salt-papers"

    ctx_manager = _vista_data_client()
    if isinstance(ctx_manager, nullcontext):
        logging.warning(
            "Neither vista_data_payload_dir nor vista_data_token configured; "
            "skipping vista-data fetch: seeding only public data"
        )
    async with ctx_manager as vista_data_client, AsyncSession(engine) as session:
        science = await _science_enabled(vista_data_client)
        if science:
            assert vista_data_client is not None  # `science` implies a client
            # TODO This is not really where we should handle the hpc_jobs files, but it will work for now
            job_mstdb_file = (
                settings.hpc_jobs_dir or REPO_ROOT / "hpc_jobs"
            ) / "forge-tune/Molten_Salt_Thermophysical_Properties.csv"
            if not job_mstdb_file.exists():
                await vista_data_client.download_file(
                    "mstdb/Molten_Salt_Thermophysical_Properties.csv", job_mstdb_file
                )
            # download_dir is resumable (skips files already on disk)
            await vista_data_client.download_dir(
                "molten-salt-papers", molten_salt_kb_dir / "pdfs"
            )
            if not _from_payload():
                await _build_knowledge_base(molten_salt_kb_dir)

        skipped_skills = set()

        for src in _bundled_skill_dirs():
            path = new_storage_path()
            if science:
                assert vista_data_client is not None
                for asset_dest, asset_src in SKILL_ASSETS.get(src.name, {}).items():
                    asset_dest = settings.data_dir / path / asset_dest
                    asset_dest.parent.mkdir(exist_ok=True, parents=True)
                    await vista_data_client.download_file(asset_src, asset_dest)
            elif SKILL_ASSETS.get(src.name):
                skipped_skills.add(src.name)
                continue
            shutil.copytree(src, settings.data_dir / path, dirs_exist_ok=True)

            skill = read_skill(settings.data_dir / path)
            session.add(
                build_skill_row(
                    skill,
                    path=path,
                    author=skill.author or "VISTA Team",
                    repo_url=None,
                    is_public=True,
                    now=now_iso(),
                )
            )

        now = now_iso()

        projects = [
            ProjectTable(
                id=uuid.UUID("f855bdd8-c433-423e-ab5c-3a9a63b6e661"),
                name="alloy-design",
                description=(
                    "High Entropy Alloy Design — runs the alloy Tc campaign "
                    "(alloy-tc-planner): a multi-cycle, human-in-the-loop search for the "
                    "MoNbTaW composition with the highest order-disorder transition "
                    "temperature, evaluated by parallel-tempering Monte Carlo on HPC "
                    "(alloy-thermo-mc). Also runs DeepThermo Wang-Landau sampling with a "
                    "VAE-learned order parameter (deepthermo-wl, vae-orderparam)."
                ),
                # encoding kept explicit per the UTF-8 portability pass on main.
                system_prompt=(SYSTEM_PROMPTS / "alloy-design.md").read_text(
                    encoding="utf-8"
                ),
                skills=sorted(
                    {
                        "alloy-tc-planner",
                        "alloy-thermo-mc",
                        "deepthermo-wl",
                        "vae-orderparam",
                    }
                    - skipped_skills
                ),
                knowledge_bases=[],
                # The campaign dispatches + monitors HPC jobs through the standard HPC
                # toolchain, so those must be ALLOWED here (they used to be denied, back
                # when alloy-design went through the retired agenthpc_* SSH tools).
                tools=["*", "!agenthpc_*"],
                usage_limits=dict(request_limit=600),
            ),
            ProjectTable(
                id=uuid.UUID("282531e7-1e05-4369-a339-9d1b4f20aa89"),
                name="molten-salt",
                description=(
                    "Molten salt thermophysical properties assistant — querying the MSTDB-TP "
                    "database, plotting phase diagrams, and searching the literature corpus. Also "
                    "runs the SPLASH tritium-breeding campaign (splash-planner): a multi-agent, "
                    "human-in-the-loop optimization of a fusion molten-salt blanket composition "
                    "(maximize TBR via salt-neutronics-tbr, gated on density via salt-chemistry-md)."
                ),
                system_prompt=(SYSTEM_PROMPTS / "molten-salt.md").read_text(
                    encoding="utf-8"
                ),
                skills=sorted(
                    {
                        "salt-analysis",
                        "salt-chemistry-md",
                        "salt-neutronics-tbr",
                        "splash-planner",
                    }
                    - skipped_skills
                ),
                knowledge_bases=[molten_salt_kb_dir.name],
                # Deny the retired agenthpc_* SSH toolchain; the SPLASH campaign
                # dispatches + monitors HPC jobs through the standard HPC toolchain.
                tools=["*", "!agenthpc_*"],
                usage_limits=dict(request_limit=100),
            ),
        ]
        if not science:
            projects = []
        session.add_all(projects)

        if science:
            _assert_knowledge_base_indexed(molten_salt_kb_dir)
            session.add(
                KnowledgeBaseTable(
                    id=uuid.UUID("8b1d4f15-d2e9-4f2a-a5c1-7c4f2e9e8d3b"),
                    slug=molten_salt_kb_dir.name,
                    name="Molten Salt Papers",
                    description=(
                        "Peer-reviewed publications on molten salt thermophysical properties, "
                        "phase behavior, and tritium breeding. Shares its PDF folder and "
                        "ChromaDB index with the MCP server's rag_search tool."
                    ),
                    pdfs_dir=str(molten_salt_kb_dir / "pdfs"),
                    rag_db_path=str(molten_salt_kb_dir / "rag_db"),
                    shared_with_mcp=True,
                    publications=[],
                    build_status="ready",
                    last_built_at=now,
                    created_at=now,
                    updated_at=now,
                )
            )

        # Test users / memberships only outside prod.
        if settings.env != "prod":
            users = [
                UserTable(
                    id=uuid.UUID("2acb5d94-c542-42b5-a2fd-66f97cffd8d7"),
                    email="vista-test-admin@americansciencecloud.org",
                    is_admin=True,
                ),
                UserTable(
                    id=uuid.UUID("7b2a0d62-08bf-47e2-b698-904ff47aef5b"),
                    email="vista-test-user@americansciencecloud.org",
                    is_admin=False,
                ),
            ]
            session.add_all(users)
            # Flush so ProjectMemberTable sees ids. TODO: Should maybe use sqlmodel.Relationship to
            # make this automatic.
            await session.flush()
            # Add the non-admin test user to every default project.
            session.add_all(
                ProjectMemberTable(project_id=project.id, user_id=users[1].id)
                for project in projects
            )

        await session.commit()


AI_SAFETY_PROJECT_ID = uuid.UUID("5c0a4f3e-7b1d-4e8a-9a52-3d6f0c1b7e24")
AI_SAFETY_KB_ID = uuid.UUID("a3e7c915-42d8-4b6f-8f0e-9d1b5c7a2e46")
AI_SAFETY_SLUG = "ai-safety"
AI_SAFETY_PROJECT_NAME = "ai-safety-autonomous-labs"
TEST_USER_ID = uuid.UUID("7b2a0d62-08bf-47e2-b698-904ff47aef5b")


async def _acquire_ai_safety_corpus() -> bool:
    """
    Put the `ai-safety` corpus under `knowledge_bases_dir` and verify its index.
    Returns False, with a warning, when the corpus is unavailable; the next boot
    tries again.

    From a payload the index is distributed ready-made and never built here: an
    absent or empty one is a packaging defect and raises. From a token the PDFs
    are downloaded and indexed, and a network or token failure only warns, so a
    revoked token or a GitLab outage never blocks startup.
    """
    kb_dir = settings.knowledge_bases_dir / AI_SAFETY_SLUG
    ctx_manager = _vista_data_client()
    if isinstance(ctx_manager, nullcontext):
        logging.warning(
            "AI-safety corpus unavailable: neither vista_data_payload_dir nor "
            "vista_data_token is configured; the project is seeded without its "
            "knowledge base"
        )
        return False
    try:
        async with ctx_manager as client:
            if not await client.has(AI_SAFETY_SLUG):
                logging.warning(
                    f"AI-safety corpus unavailable: vista-data has no "
                    f"{AI_SAFETY_SLUG}/ folder; the project is seeded without its "
                    "knowledge base"
                )
                return False
            await client.download_dir(AI_SAFETY_SLUG, kb_dir / "pdfs")
            if not _from_payload():
                await _build_knowledge_base(kb_dir)
    except httpx.HTTPError as exc:
        logging.warning(
            f"AI-safety corpus unavailable: vista-data request failed ({exc!r}); "
            "the project is seeded without its knowledge base and the next "
            "startup will try again"
        )
        return False
    _assert_knowledge_base_indexed(kb_dir)
    return True


async def sync_default_projects(engine: AsyncEngine) -> None:
    """
    Bring the AI-safety default project and knowledge base to this database, on
    every startup. Additive only, like `sync_bundled_skills`.

    Inserts the project and the KB by their fixed ids where missing, and appends
    the KB's slug to the project's `knowledge_bases` when the KB exists and the
    slug is absent. Nothing else about an existing project or KB is touched, no
    user-attached KB is removed, and nothing is deleted. The science projects are
    not handled here: they stay in `seed_db`, which only ever runs on an empty
    database.

    A deleted default project comes back on the next startup, since there is no
    record of the deletion to tell it apart from a project never seeded.
    """
    async with AsyncSession(engine) as session:
        project = await session.get(ProjectTable, AI_SAFETY_PROJECT_ID)
        kb = (
            await session.exec(
                select(KnowledgeBaseTable).where(
                    (KnowledgeBaseTable.id == AI_SAFETY_KB_ID)
                    | (KnowledgeBaseTable.slug == AI_SAFETY_SLUG)
                )
            )
        ).first()

    if kb is None and await _acquire_ai_safety_corpus():
        kb_dir = settings.knowledge_bases_dir / AI_SAFETY_SLUG
        now = now_iso()
        kb = KnowledgeBaseTable(
            id=AI_SAFETY_KB_ID,
            slug=AI_SAFETY_SLUG,
            name="AI Safety Papers",
            description=(
                "Papers on the safety and security of autonomous laboratories and "
                "LLM agents: the interconnected autonomous-labs roadmap, "
                "autonomy-induced security risks in LLM agents, and security and "
                "privacy in autonomous driving. Shares its PDF folder and ChromaDB "
                "index with the MCP server's rag_search tool."
            ),
            pdfs_dir=str(kb_dir / "pdfs"),
            rag_db_path=str(kb_dir / "rag_db"),
            shared_with_mcp=True,
            publications=[],
            build_status="ready",
            last_built_at=now,
            created_at=now,
            updated_at=now,
        )
        new_kb = True
    else:
        new_kb = False

    async with AsyncSession(engine) as session:
        if new_kb:
            session.add(kb)
        if project is None:
            session.add(
                ProjectTable(
                    id=AI_SAFETY_PROJECT_ID,
                    name=AI_SAFETY_PROJECT_NAME,
                    description=(
                        "AI Safety in Autonomous Labs: the safety and security of "
                        "AI agents that plan and run experiments, grounded in a "
                        "literature corpus and runnable HPC jobs."
                    ),
                    system_prompt=(
                        SYSTEM_PROMPTS / f"{AI_SAFETY_PROJECT_NAME}.md"
                    ).read_text(encoding="utf-8"),
                    skills=[],
                    knowledge_bases=[AI_SAFETY_SLUG] if kb is not None else [],
                    tools=["*", "!agenthpc_*"],
                    usage_limits=dict(request_limit=50),
                )
            )
            if settings.env != "prod" and await session.get(UserTable, TEST_USER_ID):
                await session.flush()
                session.add(
                    ProjectMemberTable(
                        project_id=AI_SAFETY_PROJECT_ID, user_id=TEST_USER_ID
                    )
                )
        elif kb is not None and AI_SAFETY_SLUG not in project.knowledge_bases:
            existing = await session.get(ProjectTable, AI_SAFETY_PROJECT_ID)
            assert existing is not None
            # A new list: SQLAlchemy does not see an in-place append to a JSON column.
            existing.knowledge_bases = [*existing.knowledge_bases, AI_SAFETY_SLUG]
            session.add(existing)
        await session.commit()


def _bundled_skill_dirs() -> list[Path]:
    """
    The bundled skill folders. A folder without a SKILL.md -- e.g. one left
    behind holding nothing but `__pycache__` after its skill was moved -- is not
    a skill, and is skipped rather than failing startup.
    """
    return sorted(p for p in SKILLS_SRC.iterdir() if (p / "SKILL.md").is_file())


async def sync_bundled_skills(engine: AsyncEngine) -> list[str]:
    """
    Register bundled skills that are missing from the skill library, on every
    startup. Returns the names added.

    `seed_db` runs once, on an empty database, so without this a skill bundled
    after a deployment was first seeded would never reach its library. Additive
    only: a skill already in the library (by name) is left exactly as it is --
    including any edits made to it since -- and no project is changed, so a new
    skill shows up in the library for projects to opt in to, not in any project.

    Skills that need vista-data assets (`SKILL_ASSETS`) are left to `seed_db`,
    which knows how to fetch them.

    A bundled skill deleted from the library comes back on the next startup;
    there is no record of the deletion to tell it apart from a skill that was
    never registered.
    """
    async with AsyncSession(engine) as session:
        existing = set((await session.exec(select(SkillTable.name))).all())
        added: list[str] = []
        for src in _bundled_skill_dirs():
            if src.name in existing or src.name in SKILL_ASSETS:
                continue
            skill = read_skill(src)
            if skill.name in existing:
                continue
            path = new_storage_path()
            shutil.copytree(
                src,
                settings.data_dir / path,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("__pycache__"),
            )
            session.add(
                build_skill_row(
                    read_skill(settings.data_dir / path),
                    path=path,
                    author=skill.author or "VISTA Team",
                    repo_url=None,
                    is_public=True,
                    now=now_iso(),
                )
            )
            added.append(skill.name)
        if added:
            await session.commit()
            logging.info(f"Registered bundled skills new since the last seed: {added}")
        return added
