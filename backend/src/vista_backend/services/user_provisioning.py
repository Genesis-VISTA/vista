"""
What a hosted user gets the first time they sign in: the default projects.

On a desktop install the seeded projects belong to `local` (the startup
backfill). A hosted server has many users, so each gets a project of their own
made from the same template as `sync_default_projects`'s hello-olcf.
"""

import uuid

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..db.ownership import visible, uid
from ..db.schemas import KnowledgeBaseTable, ProjectTable, UserTable
from ..db.seed import (
    HELLO_OLCF_PROJECT_NAME,
    HELLO_OLCF_SKILLS,
    OLCF_DOCS_SLUG,
    SYSTEM_PROMPTS,
)


async def provision_new_user(
    session: AsyncSession, user: UserTable
) -> list[ProjectTable]:
    """
    Make `user`'s default projects, returning the ones created. Idempotent: a
    project the user already has by that name is left as it is, so calling it
    twice, or for a returning user, adds nothing.

    The hello-olcf project names the OLCF docs corpus only when this server has
    it (a global knowledge base, `user_id` NULL).

    Called from the new-user branch of `services/current_user`, before that
    request's user row is committed, so a user and their projects arrive together.
    """
    created: list[ProjectTable] = []
    have = (
        await session.exec(
            select(ProjectTable.name).where(ProjectTable.user_id == uid(user))
        )
    ).all()
    if HELLO_OLCF_PROJECT_NAME not in have:
        corpus = (
            await session.exec(
                visible(
                    select(KnowledgeBaseTable).where(
                        KnowledgeBaseTable.slug == OLCF_DOCS_SLUG
                    ),
                    user,
                )
            )
        ).first()
        project = ProjectTable(
            id=uuid.uuid4(),
            user_id=uid(user),
            name=HELLO_OLCF_PROJECT_NAME,
            description=(
                "A first look at OLCF: ask the OLCF user documentation "
                "questions with cited answers, run a hello job on Odo, "
                "Frontier or Lux, and have a job of your own authored."
            ),
            system_prompt=(SYSTEM_PROMPTS / f"{HELLO_OLCF_PROJECT_NAME}.md").read_text(
                encoding="utf-8"
            ),
            skills=list(HELLO_OLCF_SKILLS),
            knowledge_bases=[OLCF_DOCS_SLUG] if corpus is not None else [],
            tools=["*", "!agenthpc_*"],
            usage_limits=dict(request_limit=50),
        )
        session.add(project)
        created.append(project)
    await session.flush()
    return created
