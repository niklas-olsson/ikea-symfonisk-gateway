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
    candidate_mac: str | None = None


class AliasRequest(BaseModel):
    alias: str


class VisibilityRequest(BaseModel):
    enabled: bool
    timeout: int = 0


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
    result = registry.start_pairing(adapter_id, body.timeout_seconds, candidate_mac=body.candidate_mac)
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


@router.get("/{adapter_id}/status", responses={404: {"model": ErrorResponse}})
async def get_adapter_status(request: Request, adapter_id: str) -> dict[str, Any]:
    """Get status of an adapter."""
    registry: SourceRegistry = request.app.state.source_registry
    adapter_info = registry.get_adapter(adapter_id)
    if not adapter_info or not adapter_info.adapter:
        raise HTTPException(
            status_code=404,
            detail={"code": "ADAPTER_NOT_FOUND", "message": f"Adapter {adapter_id} not found"},
        )

    if hasattr(adapter_info.adapter, "get_adapter_status"):
        return await adapter_info.adapter.get_adapter_status()
    return {"message": "Adapter does not support status reporting"}


@router.post("/{adapter_id}/alias", responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}})
async def set_adapter_alias(request: Request, adapter_id: str, body: AliasRequest) -> dict[str, Any]:
    """Set the adapter alias."""
    registry: SourceRegistry = request.app.state.source_registry
    adapter_info = registry.get_adapter(adapter_id)
    if not adapter_info or not adapter_info.adapter:
        raise HTTPException(
            status_code=404,
            detail={"code": "ADAPTER_NOT_FOUND", "message": f"Adapter {adapter_id} not found"},
        )

    if hasattr(adapter_info.adapter, "set_adapter_alias"):
        success = await adapter_info.adapter.set_adapter_alias(body.alias)
        return {"success": success}
    raise HTTPException(
        status_code=400,
        detail={"code": "NOT_SUPPORTED", "message": "Adapter does not support setting alias"},
    )


@router.post("/{adapter_id}/discoverable", responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}})
async def set_discoverable(request: Request, adapter_id: str, body: VisibilityRequest) -> dict[str, Any]:
    """Set discoverable mode."""
    registry: SourceRegistry = request.app.state.source_registry
    adapter_info = registry.get_adapter(adapter_id)
    if not adapter_info or not adapter_info.adapter:
        raise HTTPException(
            status_code=404,
            detail={"code": "ADAPTER_NOT_FOUND", "message": f"Adapter {adapter_id} not found"},
        )

    if hasattr(adapter_info.adapter, "set_discoverable"):
        success = await adapter_info.adapter.set_discoverable(body.enabled, body.timeout)
        return {"success": success}
    raise HTTPException(
        status_code=400,
        detail={"code": "NOT_SUPPORTED", "message": "Adapter does not support discoverable mode"},
    )


@router.post("/{adapter_id}/pairable", responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}})
async def set_pairable(request: Request, adapter_id: str, body: VisibilityRequest) -> dict[str, Any]:
    """Set pairable mode."""
    registry: SourceRegistry = request.app.state.source_registry
    adapter_info = registry.get_adapter(adapter_id)
    if not adapter_info or not adapter_info.adapter:
        raise HTTPException(
            status_code=404,
            detail={"code": "ADAPTER_NOT_FOUND", "message": f"Adapter {adapter_id} not found"},
        )

    if hasattr(adapter_info.adapter, "set_pairable"):
        success = await adapter_info.adapter.set_pairable(body.enabled, body.timeout)
        return {"success": success}
    raise HTTPException(
        status_code=400,
        detail={"code": "NOT_SUPPORTED", "message": "Adapter does not support pairable mode"},
    )
