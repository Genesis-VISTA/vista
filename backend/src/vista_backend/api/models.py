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


def _unversioned(base_url: str) -> str:
    """
    `base_url` without one trailing `/v1`, so appending `/v1/models` is right
    either way. Chat takes the endpoint in both forms (the MAG preset ends in
    `/v1`; i2's does not), so listing has to as well.
    """
    stripped = base_url.rstrip("/")
    return stripped.removesuffix("/v1")


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

    async with httpx.AsyncClient(
        base_url=_unversioned(target.base_url), timeout=10
    ) as client:
        try:
            response = await client.get(
                "/v1/models",
                headers={"Authorization": f"Bearer {target.api_key}"},
            )
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
