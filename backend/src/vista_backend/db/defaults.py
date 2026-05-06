"""
Default data to seed the DB with
"""
import uuid
from pathlib import Path
from .schemas import ProjectTable

SYSTEM_PROMPTS = Path(__file__).parent / 'system_prompts'

DEFAULT_PROJECTS: list[ProjectTable] = [
    ProjectTable(
        id=uuid.UUID("f855bdd8-c433-423e-ab5c-3a9a63b6e661"),
        name="alloy-design",
        description="High Entropy Alloy Design — agentic optimization of refractory MoNbTaW compositions on the Andes HPC cluster.",
        system_prompt=(SYSTEM_PROMPTS / "allow-design.md").read_text(),
        skills=["alloy-design"],
        tools=[
            "display_file",
            "run_bash",
            "create_file",
            "view",
            "rag_search",
            "openmetadata-search_metadata",
            "openmetadata-get_entity_details",
            "openmetadata-get_entity_lineage",
            "agenthpc_list_applications",
            "agenthpc_get_search_space",
            "agenthpc_submit_parameter_set",
            "agenthpc_check_job_status",
            "agenthpc_wait_for_job",
            "agenthpc_get_job_result",
            "agenthpc_get_all_results",
            "agenthpc_list_pending_jobs",
            "agenthpc_cancel_job",
            "agenthpc_cancel_all_pending",
            "agenthpc_plot_progress",
        ],
        usage_limits=dict(request_limit=600),
    ),
    ProjectTable(
        id=uuid.UUID('282531e7-1e05-4369-a339-9d1b4f20aa89'),
        name="molten-salt",
        description="Molten salt thermophysical properties assistant — querying the MSTDB-TP database, plotting phase diagrams, and searching the literature corpus.",
        system_prompt=(SYSTEM_PROMPTS / "molten-salt.md").read_text(),
        skills=["salt-analysis"],
        tools=[
            "submit_hpc_job",
            "get_hpc_job_status",
            "get_hpc_job_outputs",
            "list_hpc_jobs",
            "display_file",
            "run_bash",
            "create_file",
            "view",
            "rag_search",
        ],
        usage_limits=dict(request_limit=10),
    ),
]
