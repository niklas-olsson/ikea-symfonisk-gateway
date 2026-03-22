"""Discovery endpoints."""

from typing import Any

from fastapi import APIRouter, Request

router = APIRouter(prefix="/v1/discovery", tags=["discovery"])


@router.post("/refresh")
async def refresh_discovery(request: Request) -> dict[str, Any]:
    """Refresh both sources and targets from all registered adapters."""
    source_registry = request.app.state.source_registry
    target_registry = request.app.state.target_registry

    # Refresh sources (synchronous)
    source_registry.refresh_sources()

    # Refresh targets (asynchronous)
    await target_registry.refresh_targets()

    return {"success": True}
