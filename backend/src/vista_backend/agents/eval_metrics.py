"""
`EvalMetricsCapability` -- hook-based backend metrics collection.

Phase 1 of the eval-metrics migration introduces a single PydanticAI
capability that can own the backend-side metrics currently emitted from
`agents.py`: per-run summaries (`agent_run`), client-observed tool timing
(`tool_call.client`), and lightweight run counters (tool calls / human
interventions). The JSONL schema stays unchanged; only the attachment point
changes from bespoke agent code to capability hooks.

Phase 2 wires the capability into `ProjectAgent` and removes the redundant
backend-side timing from `agents.py`. MCP-server and tool-internal probes stay
where they are.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from typing import Any

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import UsageLimitExceeded

from ..metrics import get_recorder, run_context
from ..vistaguard.capabilities import VistaGuardDeny


class EvalMetricsCapability(AbstractCapability):
    """
    Hook-based owner for backend-side eval metrics.

    The capability is intentionally narrow: it tracks the same backend-side
    metrics the eval branch already emits today and leaves MCP-server /
    tool-internal probes alone.
    """

    def __init__(
        self,
        *,
        session_id: str,
        project_id: str,
        skills_loaded: Callable[[], list[str]],
        attribute_skill: Callable[[str, str | None], str | None] | None = None,
    ) -> None:
        self._session_id = session_id
        self._project_id = project_id
        self._skills_loaded = skills_loaded
        self._attribute_skill = attribute_skill
        self._tool_calls = 0
        self._human_interventions = 0

    def note_human_intervention(self) -> None:
        """Count a user approval / elicitation for the current run."""
        self._human_interventions += 1

    async def wrap_run(self, ctx, *, handler):  # type: ignore[override]
        self._tool_calls = 0
        self._human_interventions = 0
        recorder = get_recorder()
        if not recorder.enabled:
            return await handler()

        started = time.monotonic()
        result: Any = None
        stop_reason = "user_abort"
        with run_context(session_id=self._session_id, project_id=self._project_id):
            try:
                result = await handler()
                stop_reason = "completed"
                return result
            except VistaGuardDeny:
                stop_reason = "guard_denied"
                raise
            except UsageLimitExceeded:
                stop_reason = "budget_exhausted"
                raise
            except asyncio.CancelledError:
                stop_reason = "user_abort"
                raise
            except Exception:
                stop_reason = "error"
                raise
            finally:
                usage = (
                    result.usage()
                    if result is not None and hasattr(result, "usage")
                    else None
                )
                recorder.agent_run(
                    duration_ms=(time.monotonic() - started) * 1000,
                    stop_reason=stop_reason,
                    usage=usage,
                    tool_calls=self._tool_calls,
                    human_interventions=self._human_interventions,
                    skills_loaded=list(self._skills_loaded()),
                )

    async def wrap_tool_execute(self, ctx, *, call, tool_def, args, handler):  # type: ignore[override]
        recorder = get_recorder()
        if not recorder.enabled:
            return await handler(args)

        self._tool_calls += 1
        started = time.monotonic()
        status = "ok"
        try:
            return await handler(args)
        except Exception:
            status = "error"
            raise
        finally:
            payload: dict[str, Any] = {}
            want_skill = recorder.active("perf", "skill_usage")
            args_blob: str | None = None
            if recorder.detailed or want_skill:
                try:
                    args_blob = json.dumps(args, default=str)
                except TypeError, ValueError:
                    pass
            if recorder.detailed and args_blob is not None:
                payload["args_bytes"] = len(args_blob)
            if want_skill and self._attribute_skill is not None:
                skill = self._attribute_skill(tool_def.name, args_blob)
                if skill is not None:
                    payload["skill"] = skill
            recorder.tool_call(
                tool_name=tool_def.name,
                duration_ms=(time.monotonic() - started) * 1000,
                status=status,
                payload=payload,
            )
