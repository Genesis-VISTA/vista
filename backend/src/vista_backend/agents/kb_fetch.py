"""
The core of the agent's `fetch_kb_file` tool and the knowledge-base block of its
system prompt: copy one file out of an attached knowledge base into the agent's
workspace so it can be analysed in the sandbox. Kept apart from the tool closure
so it can be tested without building an agent.
"""

import asyncio
import shutil
from collections.abc import Sequence
from pathlib import Path

from pydantic_ai.exceptions import ModelRetry
from sqlmodel.ext.asyncio.session import AsyncSession

from ..services.knowledge_base import get_kb_optional


def kb_prompt_block(slugs: Sequence[str]) -> str:
    """The system-prompt paragraph naming the project's knowledge bases and their tools."""
    if not slugs:
        return (
            "No Knowledge Bases are configured for this project; the "
            "`rag_search` tool is not available."
        )
    kb_lines = "\n".join(f"  - {slug}" for slug in slugs)
    return (
        "Knowledge Bases available to this project (pass one of these "
        "slugs as the `kb_slug` argument to `rag_search`):\n"
        + kb_lines
        + "\n`fetch_kb_file(kb_slug, filename)` copies a file from these knowledge "
        "bases into the sandbox for analysis."
    )


async def copy_kb_file(
    session: AsyncSession,
    *,
    attached: Sequence[str],
    kb_slug: str,
    filename: str,
    work_dir: Path,
) -> tuple[Path, int]:
    """
    Copy `filename` from knowledge base `kb_slug` to `work_dir/kb/<slug>/<filename>`.

    Returns the destination and its size in bytes. Raises `ModelRetry` when the KB
    is not attached to the project, or the file is not in it.
    """
    if kb_slug not in attached:
        have = (
            "attached knowledge bases: " + ", ".join(attached)
            if attached
            else "no knowledge bases are attached to this project"
        )
        raise ModelRetry(f"Knowledge base {kb_slug!r} is not attached; {have}.")
    kb = await get_kb_optional(session, kb_slug)
    if kb is None:
        raise ModelRetry(f"Knowledge base {kb_slug!r} no longer exists.")

    files_dir = Path(kb.pdfs_dir).resolve()
    src = (files_dir / filename).resolve()
    if not src.is_relative_to(files_dir):
        raise ModelRetry(f"{filename!r} is not in knowledge base {kb_slug!r}.")
    if not src.is_file():
        raise ModelRetry(
            f"{filename!r} is not in knowledge base {kb_slug!r} "
            "(it may have been deleted from disk)."
        )

    dest = work_dir / "kb" / kb_slug / src.relative_to(files_dir)
    await asyncio.to_thread(dest.parent.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(shutil.copy2, src, dest)
    return dest, dest.stat().st_size
