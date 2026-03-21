"""Target management endpoints."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from bridge_core.api.models import ErrorResponse
from bridge_core.core import TargetRegistry

router = APIRouter(prefix="/v1/targets", tags=["targets"])


class TargetResponse(BaseModel):
    target_id: str
    renderer: str
    type: str
    display_name: str
    members: list[str]
    coordinator_id: str
    is_preferred: bool = False
    is_active: bool = False
    is_available: bool = True


class TargetListResponse(BaseModel):
    targets: list[TargetResponse]


class VolumeRequest(BaseModel):
    volume: float


@router.get("", response_model=TargetListResponse)
async def list_targets(request: Request) -> TargetListResponse:
    """List all available render targets."""
    registry: TargetRegistry = request.app.state.target_registry
    targets = []
    for t in registry.list_targets():
        targets.append(
            TargetResponse(
                target_id=t.target_id,
                renderer=t.renderer,
                type=t.target_type,
                display_name=t.display_name,
                members=t.members,
                coordinator_id=t.coordinator_id,
                is_preferred=getattr(t, "is_preferred", False),
                is_active=getattr(t, "is_active", False),
                is_available=getattr(t, "is_available", True),
            )
        )
    return TargetListResponse(targets=targets)


@router.get("/{target_id}", response_model=TargetResponse, responses={404: {"model": ErrorResponse}})
async def get_target(request: Request, target_id: str) -> TargetResponse:
    """Get details for a specific target."""
    registry: TargetRegistry = request.app.state.target_registry
    t = registry.get_target(target_id)
    if not t:
        raise HTTPException(
            status_code=404,
            detail={"code": "TARGET_NOT_FOUND", "message": f"Target {target_id} not found"},
        )

    # list_targets handles preferred/active/available decoration,
    # so we find it there to get the current state flags.
    all_targets = registry.list_targets()
    found = next((target for target in all_targets if target.target_id == target_id), None)

    if not found:
        # Fallback if not in list_targets (shouldn't happen for valid targets)
        return TargetResponse(
            target_id=t.target_id,
            renderer=t.renderer,
            type=t.target_type,
            display_name=t.display_name,
            members=t.members,
            coordinator_id=t.coordinator_id,
        )

    return TargetResponse(
        target_id=found.target_id,
        renderer=found.renderer,
        type=found.target_type,
        display_name=found.display_name,
        members=found.members,
        coordinator_id=found.coordinator_id,
        is_preferred=getattr(found, "is_preferred", False),
        is_active=getattr(found, "is_active", False),
        is_available=getattr(found, "is_available", True),
    )


@router.post("/refresh")
async def refresh_targets(request: Request) -> dict[str, Any]:
    """Refresh targets from all registered adapters."""
    registry: TargetRegistry = request.app.state.target_registry
    await registry.refresh_targets()
    return {"success": True}


@router.post("/{target_id}/heal", responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}})
async def heal_target(request: Request, target_id: str) -> dict[str, Any]:
    """Request topology/group healing for a target."""
    registry: TargetRegistry = request.app.state.target_registry
    t = registry.get_target(target_id)
    if not t:
        raise HTTPException(
            status_code=404,
            detail={"code": "TARGET_NOT_FOUND", "message": f"Target {target_id} not found"},
        )

    result = await registry.heal_target(target_id)
    if not result.get("success"):
        raise HTTPException(
            status_code=400,
            detail={"code": "TARGET_HEAL_FAILED", "message": result.get("error") or "Healing failed"},
        )
    return result


@router.post("/{target_id}/volume", responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}})
async def set_volume(request: Request, target_id: str, body: VolumeRequest) -> dict[str, Any]:
    """Set volume for a target."""
    registry: TargetRegistry = request.app.state.target_registry
    t = registry.get_target(target_id)
    if not t:
        raise HTTPException(
            status_code=404,
            detail={"code": "TARGET_NOT_FOUND", "message": f"Target {target_id} not found"},
        )

    result = await registry.set_volume(target_id, body.volume)
    if not result.get("success"):
        raise HTTPException(
            status_code=400,
            detail={"code": "TARGET_VOLUME_FAILED", "message": result.get("error") or "Failed to set volume"},
        )
    return result
