import asyncio
import logging
import os
import shutil
import uuid
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import select, func
from sqlmodel.ext.asyncio.session import AsyncSession

from ..config import settings
from .schemas import KnowledgeBaseTable, ProjectMemberTable, ProjectTable, UserTable


SYSTEM_PROMPTS = Path(__file__).parent / "system_prompts"

async def _build_knowledge_base(kb_dir: Path):
    """
    Build the ChromaDB store under `kb_dir/rag_db` by embedding the PDFs in
    `kb_dir/pdfs`. Skips if already exists.
    """
    from ..utils import indexer

    pdfs_dir = kb_dir / "pdfs"
    rag_db = kb_dir / "rag_db"

    if (rag_db / 'chroma.sqlite3').exists():
        return

    filenames = sorted(p.name for p in pdfs_dir.glob("*.pdf"))
    logging.info(f"Embedding {len(filenames)} PDF(s) into the knowledge base at {rag_db}.")
    results = await indexer.index_publications(
        rag_db_path=str(rag_db), pdfs_dir=str(pdfs_dir), filenames=filenames,
    )

    failed = [r.get("filename") for r in results if r.get("status") == "failed"]
    if failed:
        logging.warning(f"Knowledge base: {len(failed)} PDF(s) failed to index: {failed}")
    logging.info(f"Knowledge base built at {rag_db}")


@asynccontextmanager
async def _fetch_vista_data_repo() -> AsyncIterator[Path | None]:
    """
    Fetches the vista-data repo to a tmpdir. Returns None if not accessible. Contextmanager
    to clean up the repo.
    """
    if settings.gitlab_token:
        clone_url = f"https://oauth2:{settings.gitlab_token}@code.ornl.gov/v28/vista-data.git"
        log_url = "https://code.ornl.gov/v28/vista-data.git"
    else:
        clone_url = "git@code.ornl.gov:v28/vista-data.git"
        log_url = clone_url

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_dir = Path(tmp_dir)

        # Shallow clone — we only need the current PDFs, not the history.
        proc = await asyncio.create_subprocess_exec(
            "git", "clone", "--depth", "1", clone_url, str(tmp_dir),
            env = {
                **os.environ,
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_SSH_COMMAND": "ssh -oBatchMode=yes -oStrictHostKeyChecking=accept-new",
            },
        )
        try:
            returncode = await asyncio.wait_for(proc.wait(), 300)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            logging.warning(f"Timed out cloning {log_url}, seeding only with public data.")
            yield None
            return

        if returncode == 0:
            yield tmp_dir
        else:
            logging.warning(f"Unable to fetch {log_url}, seeding only with public data.")
            yield None


async def seed_db(engine: AsyncEngine) -> None:
    """
    Seed the DB with default data on first run, and a no-op thereafter.
    """
    async with AsyncSession(engine) as session:
        count = (await session.exec(select(func.count()).select_from(ProjectTable))).one()
        if count > 0:
            return # Already seeded

    logging.info("Seeding database and data dir with initial data (this can take a bit)...")
    molten_salt_kb_dir = settings.knowledge_bases_dir / "molten-salt-papers"
    have_vista_data = False
    if (molten_salt_kb_dir / 'pdfs').exists():
        have_vista_data = True
    else:
        async with _fetch_vista_data_repo() as vista_data_repo:
            if vista_data_repo:
                have_vista_data = True
                shutil.copytree(vista_data_repo / "molten-salt-papers", molten_salt_kb_dir / 'pdfs')
    if have_vista_data:
        await _build_knowledge_base(molten_salt_kb_dir)

    now = datetime.now(timezone.utc).isoformat()

    async with AsyncSession(engine) as session:
        projects = [
            ProjectTable(
                id=uuid.UUID("f855bdd8-c433-423e-ab5c-3a9a63b6e661"),
                name="alloy-design",
                description="High Entropy Alloy Design — agentic optimization of refractory MoNbTaW compositions on the Andes HPC cluster.",
                system_prompt=(SYSTEM_PROMPTS / "allow-design.md").read_text(),
                skills=["alloy-design"],
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
                description="Molten salt thermophysical properties assistant — querying the MSTDB-TP database, plotting phase diagrams, and searching the literature corpus.",
                system_prompt=(SYSTEM_PROMPTS / "molten-salt.md").read_text(),
                skills=["salt-analysis"],
                knowledge_bases=[molten_salt_kb_dir.name] if have_vista_data else [],
                # Allow everything except the alloy-design HPC toolchain.
                tools=["*", "!agenthpc_*"],
                usage_limits=dict(request_limit=10),
            ),
        ]
        session.add_all(projects)

        if have_vista_data:
            session.add(KnowledgeBaseTable(
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
            ))

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
