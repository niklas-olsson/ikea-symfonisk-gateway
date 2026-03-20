"""Target management endpoints."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from bridge_core.core import TargetRegistry

router = APIRouter(prefix="/v1/targets", tags=["targets"])


class TargetListResponse(BaseModel):
    targets: list[dict[str, Any]]


class VolumeRequest(BaseModel):
    volume: float


@router.get("", response_model=TargetListResponse)
async def list_targets(request: Request) -> TargetListResponse:
    """List all available render targets."""
    registry: TargetRegistry = request.app.state.target_registry
    targets = []
    for t in registry.list_targets():
        targets.append(
            {
                "target_id": t.target_id,
                "renderer": t.renderer,
                "type": t.target_type,
                "display_name": t.display_name,
                "members": t.members,
                "coordinator_id": t.coordinator_id,
            }
        )
    return TargetListResponse(targets=targets)


@router.get("/{target_id}")
async def get_target(request: Request, target_id: str) -> dict[str, Any]:
    """Get details for a specific target."""
    registry: TargetRegistry = request.app.state.target_registry
    t = registry.get_target(target_id)
    if not t:
        raise HTTPException(status_code=404, detail="Target not found")
    return {
        "target_id": t.target_id,
        "renderer": t.renderer,
        "type": t.target_type,
        "display_name": t.display_name,
        "members": t.members,
        "coordinator_id": t.coordinator_id,
    }


@router.post("/{target_id}/heal")
async def heal_target(request: Request, target_id: str) -> dict[str, Any]:
    """Request topology/group healing for a target."""
    registry: TargetRegistry = request.app.state.target_registry
    result = await registry.heal_target(target_id)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error") or "Healing failed")
    return result


@router.post("/{target_id}/volume")
async def set_volume(request: Request, target_id: str, body: VolumeRequest) -> dict[str, Any]:
    """Set volume for a target."""
    registry: TargetRegistry = request.app.state.target_registry
    result = await registry.set_volume(target_id, body.volume)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error") or "Failed to set volume")
    return result
