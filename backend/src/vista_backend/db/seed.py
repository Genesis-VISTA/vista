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
from .schemas import KnowledgeBaseTable, ProjectMemberTable, ProjectTable, UserTable
from ..agents.skills import read_skill
from ..services._helpers import new_storage_path
from ..services.skills import build_skill_row
from ..utils.misc import now_iso


SYSTEM_PROMPTS = Path(__file__).parent / "system_prompts"
SKILLS_SRC = Path(__file__).parent / "skills"
REPO_ROOT = Path(__file__).parents[4]


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

    filenames = sorted(p.name for p in pdfs_dir.glob("*.pdf"))
    logging.info(
        f"Embedding {len(filenames)} PDF(s) into the knowledge base at {rag_db}."
    )
    results = await indexer.index_publications(
        rag_db_path=str(rag_db),
        pdfs_dir=str(pdfs_dir),
        filenames=filenames,
    )

    failed = [r.get("filename") for r in results if r.get("status") == "failed"]
    if failed:
        logging.warning(
            f"Knowledge base: {len(failed)} PDF(s) failed to index: {failed}"
        )
    logging.info(f"Knowledge base built at {rag_db}")


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

    if settings.vista_data_token:
        ctx_manager = GitlabRepoClient(
            "code.ornl.gov", "v28/vista-data", token=settings.vista_data_token
        )
    else:
        ctx_manager = nullcontext()
        logging.warning(
            "No vista_data_token configured; skipping vista-data fetch: seeding only public data"
        )
    async with ctx_manager as vista_data_client, AsyncSession(engine) as session:
        if vista_data_client:
            # TODO This is not really where we should handle the hpc_jobs files, but it will work for now
            job_mstdb_file = (
                REPO_ROOT
                / "hpc_jobs/forge-tune/Molten_Salt_Thermophysical_Properties.csv"
            )
            if not job_mstdb_file.exists():
                await vista_data_client.download_file(
                    "mstdb/Molten_Salt_Thermophysical_Properties.csv", job_mstdb_file
                )
            # download_dir is resumable (skips files already on disk)
            await vista_data_client.download_dir(
                "molten-salt-papers", molten_salt_kb_dir / "pdfs"
            )
            await _build_knowledge_base(molten_salt_kb_dir)

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
        skipped_skills = set()

        for src in sorted(p for p in SKILLS_SRC.iterdir() if p.is_dir()):
            path = new_storage_path()
            if vista_data_client:
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
                description="High Entropy Alloy Design — agentic optimization of refractory MoNbTaW compositions on the Andes HPC cluster.",
                system_prompt=(SYSTEM_PROMPTS / "alloy-design.md").read_text(),
                skills=sorted({"alloy-design"} - skipped_skills),
                knowledge_bases=[],
                tools=[
                    "*",
                    "!submit_hpc_job",
                    "!get_hpc_job_status",
                    "!get_hpc_job_outputs",
                    "!list_hpc_jobs",
                ],
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
                system_prompt=(SYSTEM_PROMPTS / "molten-salt.md").read_text(),
                skills=sorted(
                    {
                        "salt-analysis",
                        "salt-chemistry-md",
                        "salt-neutronics-tbr",
                        "splash-planner",
                    }
                    - skipped_skills
                ),
                knowledge_bases=[molten_salt_kb_dir.name] if vista_data_client else [],
                # Allow everything except the alloy-design HPC toolchain (the SPLASH campaign
                # dispatches + monitors HPC jobs through the standard HPC toolchain).
                tools=["*", "!agenthpc_*"],
                usage_limits=dict(request_limit=100),
            ),
        ]
        session.add_all(projects)

        if vista_data_client:
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
