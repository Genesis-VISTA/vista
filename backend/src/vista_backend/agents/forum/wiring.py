"""
Live wiring for the debate forum: the seam between the API and the real world.

Everything the orchestrator needs is injected, so this module is the only place
that knows about MCP, the skills service and the h5i binary. It is also the only
place that decides what a debate is allowed to look at, which keeps that decision
readable in one file rather than spread across the tools.
"""

from __future__ import annotations

import logging
import uuid

from sqlmodel.ext.asyncio.session import AsyncSession

from ...config import settings
from ...db.db import get_engine
from ...db.schemas import (
    DebateRunTable,
    ProjectTable,
    UserPublicWithConfig,
    UserTable,
)
from ...services import debate as debate_service
from ...services import skills as skills_service
from ...services.h5i_forum import ForumClient
from ..campaign.hpc_tools import McpHpcTools
from ..campaign.mcp_invoke import build_mcp_invoke, project_paths_for
from . import simulation
from .debate import DebateOrchestrator
from .grounding import Grounding, WebReader
from .roles import RoleAgents
from ...services.h5i_forum import VotePolicy
from .simulation import (
    SimulationCommissioner,
    clusters_for,
    commissioned_count,
    runnable_jobs,
)


logger = logging.getLogger(__name__)


def build_client() -> ForumClient:
    return ForumClient(settings.forum)


async def _read_skill_body(name: str) -> str:
    """
    A domain skill's text, read through the skills service.

    Opens its own session: tools run inside an agent turn, which may outlive the
    request session that started the debate.
    """
    async with AsyncSession(get_engine()) as session:
        try:
            _, body = await skills_service.get_skill_detail(session, name)
        except Exception:  # noqa: BLE001 — a missing skill is an answer, not a crash
            return f"No skill named {name!r} is installed."
        return body


def build_grounding(client: ForumClient) -> Grounding:
    """
    What a debate may look at, with no run in hand.

    Knowledge-base search needs a live MCP connection and the opener's metadata,
    so it is wired by `build_run_grounding` instead. Prefer that wherever a run
    exists: without it the Reviewer gets `prior_debates` and nothing else, which
    is a debate arguing from the model alone.

    Web reads are granted only where they can actually work. `WebReader` refuses
    itself when the configured tier does not enforce the egress allowlist, and
    handing a model a tool that can only return a refusal is an invitation to
    call it again — which is exactly how a role burns its whole request budget
    on one turn and the debate dies with a usage-limit error.
    """
    browser = WebReader(client)
    if browser.refusal is not None:
        logger.info("debate: web grounding is off — %s", browser.refusal)
        browser = None

    return Grounding(
        skills=_read_skill_body,
        forum=client,
        browser=browser,
    )


async def build_run_grounding(
    session: AsyncSession, run: DebateRunTable, client: ForumClient
) -> Grounding:
    """
    What *this* debate may look at, including its project's knowledge bases.

    `rag_search` runs over the vista MCP server and needs the opener's metadata,
    exactly like the HPC tools do — so like them it can only be built once a run
    exists. The knowledge bases are the project's own; a debate is scoped to a
    project, so there is no second list to keep in step with what the project is
    for.

    Falls back to the run-less grounding when the project has no knowledge bases
    or the opener cannot be resolved. A search tool over an empty corpus is worse
    than no search tool: it answers "nothing found" to every question, and the
    role reads that as evidence of absence.
    """
    grounding = build_grounding(client)

    project = await session.get(ProjectTable, run.project_id)
    user_row = await session.get(UserTable, run.user_id)
    if project is None or user_row is None:
        return grounding

    kbs = list(project.knowledge_bases or [])
    if not kbs:
        logger.info(
            "debate %s: no literature search — project %r has no knowledge bases",
            run.id,
            project.name,
        )
        return grounding

    user = UserPublicWithConfig.model_validate(user_row)
    invoke = build_mcp_invoke(user, project_paths_for(run.project_id, run.user_id))
    default_kb = kbs[0] if len(kbs) == 1 else None

    async def rag(query: str, kb_slug: str | None, n_results: int) -> str:
        # A slug the project does not list is refused rather than passed through:
        # the model picks this argument, and the project's KB list is the access
        # boundary for a debate exactly as it is for a chat.
        slug = kb_slug or default_kb
        if slug is not None and slug not in kbs:
            return (
                f"No knowledge base {slug!r} on this project. "
                f"Available: {', '.join(kbs)}."
            )
        args: dict[str, object] = {"query": query, "n_results": n_results}
        if slug is not None:
            args["kb_slug"] = slug
        return await invoke("rag_search", args)

    grounding.rag = rag
    return grounding


async def knowledge_bases_for(session: AsyncSession, run: DebateRunTable) -> list[str]:
    """
    The corpora this debate's roles may name in a search.

    Travels on `DebateDeps` rather than on `Grounding` because it is a fact about
    the run, not about the wiring — the same reason `available_jobs` does.
    """
    project = await session.get(ProjectTable, run.project_id)
    return list(project.knowledge_bases or []) if project is not None else []


async def ensure_federation(client: ForumClient | None = None) -> None:
    """
    Bring the forum's remote and vote policy in line with configuration.

    Both live in the forum's own store rather than in our settings, so this
    reconciles rather than sets: it is safe to run on every boot and does nothing
    when they already agree.

    Applying the remote is checked with a `sync`, because `forum remote` accepts
    any string — a typo is not discovered until something tries to reach it, and
    a debate silently publishing nowhere is worse than a loud startup warning.

    The vote policy is only tightened once it would mean something. `principal`
    counts one vote per enrolled account and *nothing* from an unenrolled
    machine, so setting it on a forum where nobody has run `h5i forum enroll`
    silently zeroes every vote, our own agents' included.
    """
    config = settings.forum
    if not config.enabled or config.repo_root is None:
        return
    client = client or build_client()

    if config.remote_url:
        try:
            current = await client.remote()
            if config.remote_url not in current:
                await client.set_remote(config.remote_url)
                logger.info("forum: publishing to %s", config.remote_url)
            result = await client.sync()
            logger.info(
                "forum: remote reachable (%d pulled, %d pushed)",
                result.pulled,
                result.pushed,
            )
        except Exception:  # noqa: BLE001 — an unreachable remote must not stop boot
            logger.warning(
                "forum: could not reach %s — debates will run locally and publish "
                "nothing until it is fixed",
                config.remote_url,
                exc_info=True,
            )

    if config.vote_policy:
        try:
            wanted = VotePolicy(config.vote_policy)
            if await client.vote_policy() == wanted:
                return
            if wanted is VotePolicy.PRINCIPAL and not await client.enrollments():
                logger.warning(
                    "forum: leaving the vote policy alone — `principal` counts "
                    "nothing from an unenrolled machine, and nobody has run "
                    "`h5i forum enroll` yet, so every vote would be discarded"
                )
                return
            await client.set_vote_policy(wanted)
            logger.info("forum: vote policy is now %s", wanted)
        except Exception:  # noqa: BLE001
            logger.warning("forum: could not set the vote policy", exc_info=True)


async def _commit(session: AsyncSession) -> None:
    await session.commit()


async def build_simulation(
    session: AsyncSession, run: DebateRunTable
) -> tuple[SimulationCommissioner | None, list[str], list[str]]:
    """
    The HPC commissioner for one debate, plus what it may run and where.

    Returns `(commissioner, jobs, clusters)`, and `None` when this debate cannot
    submit anything — no simulation skills on the project, or no HPC credentials
    on the person who opened it. The tool is then not granted at all, for the
    reason the usage-limit bug taught: a model handed a tool that can only fail
    keeps calling it until its budget is gone.

    Credentials are the opener's, because the job runs against their allocation.
    """
    project = await session.get(ProjectTable, run.project_id)
    user_row = await session.get(UserTable, run.user_id)
    if project is None or user_row is None:
        return None, [], []

    user = UserPublicWithConfig.model_validate(user_row)
    jobs = runnable_jobs(list(project.skills or []), settings.hpc_jobs_dir)
    clusters = clusters_for(user)

    if not jobs:
        logger.info(
            "debate %s: no HPC — project %r loads no skill with a matching job",
            run.id,
            project.name,
        )
        return None, [], []
    if not clusters:
        logger.info(
            "debate %s: no HPC — %s has no cluster credentials configured",
            run.id,
            user.email,
        )
        return None, [], []

    hpc = McpHpcTools(
        build_mcp_invoke(user, project_paths_for(run.project_id, run.user_id))
    )
    run_id, project_id, user_id, thread_id = (
        run.id,
        run.project_id,
        run.user_id,
        run.thread_id,
    )

    async def commissioner(
        *,
        participant,
        job: str,
        prediction: str,
        cluster: str | None = None,
        script_args: str | None = None,
        reply_to: str | None = None,
    ) -> str:
        if job not in jobs:
            raise ValueError(
                f"{job!r} is not runnable in this project. Available: "
                f"{', '.join(jobs)}."
            )
        if cluster is not None and cluster not in clusters:
            raise ValueError(
                f"no credentials for {cluster!r}. Available: {', '.join(clusters)}."
            )
        spent = await commissioned_count(session, debate_run_id=run_id)
        if spent >= settings.forum.max_simulations:
            raise ValueError(
                f"this debate has already commissioned {spent} of "
                f"{settings.forum.max_simulations} permitted simulations."
            )

        row = await simulation.commission(
            session,
            debate_run_id=run_id,
            project_id=project_id,
            user_id=user_id,
            thread_id=thread_id,
            participant=participant,
            hpc=hpc,
            job=job,
            prediction=prediction,
            cluster=cluster or clusters[0],
            script_args=script_args,
            reply_to=reply_to,
        )
        return row.job_id

    return commissioner, jobs, clusters


def build_orchestrator(
    *,
    on_post=None,
    checkpoint=None,
    grounding: Grounding | None = None,
    available_jobs: list[str] | None = None,
    available_clusters: list[str] | None = None,
    knowledge_bases: list[str] | None = None,
) -> DebateOrchestrator:
    client = build_client()
    grounding = grounding if grounding is not None else build_grounding(client)
    return DebateOrchestrator(
        client=client,
        roles=RoleAgents(toolsets=_toolsets(grounding)),
        on_post=on_post,
        checkpoint=checkpoint,
        available_jobs=available_jobs,
        available_clusters=available_clusters,
        knowledge_bases=knowledge_bases,
    )


def _toolsets(grounding: Grounding):
    from .grounding import build_toolsets

    return build_toolsets(grounding)


async def continue_debate_task(run_id: uuid.UUID, extra_rounds: int) -> None:
    """
    Argue an existing thread for a few more rounds.

    Same shape as `run_debate_task` and the same failure handling — the caller
    returns as soon as the roster is being attached, so a crash here has to land
    on the run rather than in a task nobody awaits.

    The simulation budget is deliberately *not* refreshed: `max_simulations` is
    per debate, and a continued debate is the same debate. Otherwise continuing
    would be a way to buy more cluster time a round at a time.
    """
    async with AsyncSession(get_engine()) as session:
        try:
            run = await debate_service.require_debate(session, run_id)

            client = build_client()
            grounding = await build_run_grounding(session, run, client)
            commissioner, jobs, clusters = await build_simulation(session, run)
            grounding.simulation = commissioner

            await build_orchestrator(
                checkpoint=_commit,
                grounding=grounding,
                available_jobs=jobs,
                available_clusters=clusters,
                knowledge_bases=await knowledge_bases_for(session, run),
            ).resume(session, run=run, extra_rounds=extra_rounds)
            await session.commit()
        except Exception:
            logger.exception("debate %s: could not continue", run_id)
            await session.rollback()
            try:
                await debate_service.set_status(session, run_id=run_id, status="failed")
                await session.commit()
            except Exception:  # noqa: BLE001
                logger.exception("debate %s: could not record the failure", run_id)


async def run_debate_task(run_id: uuid.UUID) -> None:
    """
    Run one debate to completion in the background.

    Owns its own session: the request that started the debate returns as soon as
    the thread exists, and the argument then takes minutes. A failure is recorded
    on the run rather than raised into a task nobody is awaiting — otherwise a
    debate that died would sit at `debating` forever with no explanation.
    """
    async with AsyncSession(get_engine()) as session:
        try:
            run = await debate_service.require_debate(session, run_id)

            client = build_client()
            grounding = await build_run_grounding(session, run, client)
            commissioner, jobs, clusters = await build_simulation(session, run)
            grounding.simulation = commissioner

            # Checkpoint per round: a reader on another session — the event
            # stream, another worker — sees nothing of an uncommitted debate,
            # so without this the live view only goes live once it is over.
            await build_orchestrator(
                checkpoint=_commit,
                grounding=grounding,
                available_jobs=jobs,
                available_clusters=clusters,
                knowledge_bases=await knowledge_bases_for(session, run),
            ).run(session, run)
            await session.commit()
        except Exception:
            logger.exception("debate %s failed", run_id)
            await session.rollback()
            try:
                await debate_service.set_status(session, run_id=run_id, status="failed")
                await session.commit()
            except Exception:  # noqa: BLE001
                logger.exception("debate %s: could not record the failure", run_id)
