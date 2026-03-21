"""Source management endpoints."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from ingress_sdk.types import HealthResult
from pydantic import BaseModel

from bridge_core.api.models import ErrorResponse
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


@router.post("/refresh")
async def refresh_sources(request: Request) -> dict[str, Any]:
    """Refresh sources from all registered adapters."""
    registry: SourceRegistry = request.app.state.source_registry
    registry.refresh_sources()
    return {"success": True}


@router.get("/{source_id}", responses={404: {"model": ErrorResponse}})
async def get_source(request: Request, source_id: str) -> dict[str, Any]:
    """Get details for a specific source."""
    registry: SourceRegistry = request.app.state.source_registry
    source = registry.get_source(source_id)
    if not source:
        raise HTTPException(
            status_code=404,
            detail={"code": "SOURCE_NOT_FOUND", "message": f"Source {source_id} not found"},
        )
    return source.model_dump()


@router.post("/{source_id}/prepare", responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}})
async def prepare_source(request: Request, source_id: str, body: PrepareRequest) -> dict[str, Any]:
    """Prepare a source for capture."""
    registry: SourceRegistry = request.app.state.source_registry
    source = registry.get_source(source_id)
    if not source:
        raise HTTPException(
            status_code=404,
            detail={"code": "SOURCE_NOT_FOUND", "message": f"Source {source_id} not found"},
        )

    result = registry.prepare_source(source_id)
    if not result.success:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "SOURCE_PREPARE_FAILED",
                "message": result.message or result.error or "Preparation failed",
            },
        )
    return {"success": True, "source_id": source_id}


@router.get("/{source_id}/health", response_model=HealthResult, responses={404: {"model": ErrorResponse}})
async def get_source_health(request: Request, source_id: str) -> HealthResult:
    """Get health status for a specific source."""
    registry: SourceRegistry = request.app.state.source_registry
    source = registry.get_source(source_id)
    if not source:
        raise HTTPException(
            status_code=404,
            detail={"code": "SOURCE_NOT_FOUND", "message": f"Source {source_id} not found"},
        )

    health = registry.get_source_health(source_id)
    if not health:
        raise HTTPException(
            status_code=404,
            detail={"code": "HEALTH_NOT_FOUND", "message": f"Health status not found for source {source_id}"},
        )
    return health
