"""
Default data to seed the DB with
"""
import uuid
from pathlib import Path
from ..config import settings
from .schemas import KnowledgeBaseTable, ProjectMemberTable, ProjectTable, UserTable

SYSTEM_PROMPTS = Path(__file__).parent / 'system_prompts'

DEFAULT_PROJECTS: list[ProjectTable] = [
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
        id=uuid.UUID('282531e7-1e05-4369-a339-9d1b4f20aa89'),
        name="molten-salt",
        description="Molten salt thermophysical properties assistant — querying the MSTDB-TP database, plotting phase diagrams, and searching the literature corpus.",
        system_prompt=(SYSTEM_PROMPTS / "molten-salt.md").read_text(),
        skills=["salt-analysis"],
        knowledge_bases=["molten-salt-papers"],
        # Allow everything except the alloy-design HPC toolchain.
        tools=["*", "!agenthpc_*"],
        usage_limits=dict(request_limit=10),
    ),
]

# TODO Can we remove this and these config options
# Built-in Knowledge Bases. Currently just the molten-salt corpus,
# pinned to the same paths the MCP server's rag_search tool reads from
# (`<repo>/pdfs` for source PDFs, `<repo>/rag_db` for the ChromaDB).
# Path resolution defers to `settings.molten_salt_*` so users with
# non-default layouts (set via `VISTA_MCP_RAG_DB_PATH` etc.) keep the
# seed pointing at the right place.
#
# `created_at` and `updated_at` are set at init_db time rather than
# baked in here — keeping the defaults file pure data makes the seed
# reproducible and lets `init_db` detect "first-run" cleanly.

DEFAULT_USERS: list[UserTable]
DEFAULT_PROJECT_MEMBERS: list[ProjectMemberTable]
if settings.env != 'prod':
    DEFAULT_USERS = [
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
    DEFAULT_PROJECT_MEMBERS = [
        ProjectMemberTable(project_id=project.id, user_id=DEFAULT_USERS[1].id)
        for project in DEFAULT_PROJECTS
    ]
else:
    DEFAULT_USERS = []
    DEFAULT_PROJECT_MEMBERS = []

DEFAULT_KNOWLEDGE_BASES: list[KnowledgeBaseTable] = [
    KnowledgeBaseTable(
        id=uuid.UUID("8b1d4f15-d2e9-4f2a-a5c1-7c4f2e9e8d3b"),
        slug="molten-salt-papers",
        name="Molten Salt Papers",
        description=(
            "Peer-reviewed publications on molten salt thermophysical properties, "
            "phase behavior, and tritium breeding. Shares its PDF folder and "
            "ChromaDB index with the MCP server's rag_search tool."
        ),
        builtin=True,
        pdfs_dir=str(settings.molten_salt_pdfs_dir),
        rag_db_path=str(settings.molten_salt_rag_db),
        shared_with_mcp=True,
        publications=[],
        build_status="pending",
    ),
]
