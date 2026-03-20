"""Adapter management endpoints."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from bridge_core.api.models import ErrorResponse
from bridge_core.core import SourceRegistry

router = APIRouter(prefix="/v1/adapters", tags=["adapters"])


class AdapterListResponse(BaseModel):
    adapters: list[dict[str, Any]]


class PairingRequest(BaseModel):
    timeout_seconds: int = 60


@router.get("", response_model=AdapterListResponse)
async def list_adapters(request: Request) -> AdapterListResponse:
    """List all registered ingress adapters."""
    registry: SourceRegistry = request.app.state.source_registry
    adapters = [a.to_dict() for a in registry.list_adapters()]
    return AdapterListResponse(adapters=adapters)


@router.post("/{adapter_id}/pairing/start", responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}})
async def start_pairing(request: Request, adapter_id: str, body: PairingRequest) -> dict[str, Any]:
    """Start pairing mode on an adapter."""
    registry: SourceRegistry = request.app.state.source_registry
    result = registry.start_pairing(adapter_id, body.timeout_seconds)
    if not result.success:
        code = "ADAPTER_NOT_FOUND" if "not found" in (result.error or "").lower() else "PAIRING_FAILED"
        raise HTTPException(
            status_code=404 if code == "ADAPTER_NOT_FOUND" else 400,
            detail={
                "code": code,
                "message": result.message or result.error or "Failed to start pairing",
            },
        )
    return {"success": True, "message": result.message or "Pairing mode started"}


@router.post("/{adapter_id}/pairing/stop", responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}})
async def stop_pairing(request: Request, adapter_id: str) -> dict[str, Any]:
    """Stop pairing mode on an adapter."""
    registry: SourceRegistry = request.app.state.source_registry
    result = registry.stop_pairing(adapter_id)
    if not result.success:
        code = "ADAPTER_NOT_FOUND" if "not found" in (result.error or "").lower() else "PAIRING_STOP_FAILED"
        raise HTTPException(
            status_code=404 if code == "ADAPTER_NOT_FOUND" else 400,
            detail={
                "code": code,
                "message": result.message or result.error or "Failed to stop pairing",
            },
        )
    return {"success": True, "message": result.message or "Pairing mode stopped"}
