"""Source management endpoints."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from bridge_core.core import SourceRegistry

router = APIRouter(prefix="/v1/sources", tags=["sources"])


class SourceListResponse(BaseModel):
    sources: list[dict[str, Any]]


class PrepareRequest(BaseModel):
    session_hint: str | None = None


@router.get("", response_model=SourceListResponse)
async def list_sources(request: Request) -> SourceListResponse:
    """List all available audio sources from all ingress adapters."""
    registry: SourceRegistry = request.app.state.source_registry
    sources = [s.model_dump() for s in registry.list_sources()]
    return SourceListResponse(sources=sources)


@router.get("/{source_id}")
async def get_source(request: Request, source_id: str) -> dict[str, Any]:
    """Get details for a specific source."""
    registry: SourceRegistry = request.app.state.source_registry
    source = registry.get_source(source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    return source.model_dump()


@router.post("/{source_id}/prepare")
async def prepare_source(request: Request, source_id: str, body: PrepareRequest) -> dict[str, Any]:
    """Prepare a source for capture."""
    registry: SourceRegistry = request.app.state.source_registry
    result = registry.prepare_source(source_id)
    if not result.success:
        raise HTTPException(status_code=400, detail=result.error or "Preparation failed")
    return {"success": True, "source_id": source_id}
