"""Source management endpoints."""

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/v1/sources", tags=["sources"])


class SourceListResponse(BaseModel):
    sources: list[dict[str, Any]]


class PrepareRequest(BaseModel):
    session_hint: str | None = None


@router.get("", response_model=SourceListResponse)
async def list_sources() -> SourceListResponse:
    """List all available audio sources from all ingress adapters."""
    return SourceListResponse(sources=[])


@router.get("/{source_id}")
async def get_source(source_id: str) -> dict[str, Any]:
    """Get details for a specific source."""
    raise HTTPException(status_code=404, detail="Source not found")


@router.post("/{source_id}/prepare")
async def prepare_source(source_id: str, request: PrepareRequest) -> dict[str, Any]:
    """Prepare a source for capture."""
    raise HTTPException(status_code=404, detail="Source not found")
