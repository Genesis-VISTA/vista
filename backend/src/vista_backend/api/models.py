import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..agents.inference import (
    MissingInferenceCredential,
    rejected_credential_detail,
    resolve_inference_target,
)
from ..db.db import SessionDep
from ..services import project as project_service
from ..services.auth import UserDep

router = APIRouter()


class ModelInfo(BaseModel):
    id: str
    owned_by: str | None = None


class ModelsListResponse(BaseModel):
    """
    The models available through the caller's configured inference endpoint.

    `supported=False` means the resolved target isn't reached through a known,
    listable base URL (see `InferenceTarget.uses_configured_endpoint`) -- not
    an error, just nothing to list. The client falls back to letting the
    researcher name a model directly.
    """

    supported: bool
    models: list[ModelInfo] = []


def _models_urls(base_url: str) -> tuple[str, str]:
    """
    Where to list models: `<base>/models`, as the OpenAI client asks
    `<base>/chat/completions` for chat, then `<base>/v1/models` if that is 404.

    No rule about a trailing `/v1` fits every preset: i2's base has none,
    MAG's ends in it, and OLCF's carries it mid-path (`.../v1/inference`).
    The fallback keeps a base with no `/v1` (i2's LiteLLM) working.
    """
    stripped = base_url.rstrip("/")
    return f"{stripped}/models", f"{stripped}/v1/models"


@router.get("/projects/{project_name}/models", response_model=ModelsListResponse)
async def list_models(
    project_name: str, session: SessionDep, user: UserDep
) -> ModelsListResponse:
    """
    List the models available through the caller's configured inference
    endpoint.

    Scoped under a project path for consistency with the rest of the API
    surface, but the result depends only on the signed-in researcher's own
    inference configuration, not the project -- `project_name` here is an
    access check (the same 404-if-not-visible every other project route
    applies), not a filter.
    """
    await project_service.get_project_by_name(session, project_name, user)

    target = resolve_inference_target(user)
    if not target.uses_configured_endpoint:
        return ModelsListResponse(supported=False, models=[])

    if not target.has_credential:
        raise MissingInferenceCredential(target.model, target.base_url)

    headers = {"Authorization": f"Bearer {target.api_key}"}
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            for url in _models_urls(target.base_url):
                response = await client.get(url, headers=headers)
                if response.status_code != 404:
                    break
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (401, 403):
                raise HTTPException(
                    status_code=409,
                    detail=rejected_credential_detail(target.model),
                ) from exc
            raise

    payload = response.json()
    models = [
        ModelInfo(id=entry["id"], owned_by=entry.get("owned_by"))
        for entry in payload.get("data", [])
    ]
    return ModelsListResponse(supported=True, models=models)
