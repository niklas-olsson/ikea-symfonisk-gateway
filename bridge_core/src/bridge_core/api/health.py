"""Health and status endpoints."""

import platform
import sys
import time
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from bridge_core.stream.utils import resolve_ffmpeg_path

router = APIRouter(prefix="", tags=["health"])


class HealthResponse(BaseModel):
    status: str
    version: str
    uptime: float
    system: dict[str, str]
    ffmpeg: dict[str, Any]
    subsystems: dict[str, int]


_start_time = time.time()


@router.get("/health", response_model=HealthResponse)
async def get_health(request: Request) -> HealthResponse:
    """Return bridge health status."""
    ffmpeg_path = None
    ffmpeg_available = False
    try:
        ffmpeg_path = resolve_ffmpeg_path(request.app.state.config_store)
        ffmpeg_available = True
    except RuntimeError:
        pass

    source_registry = request.app.state.source_registry
    target_registry = request.app.state.target_registry
    session_manager = request.app.state.session_manager

    return HealthResponse(
        status="ok",
        version="0.1.0",
        uptime=time.time() - _start_time,
        system={
            "os": platform.system(),
            "arch": platform.machine(),
            "python_version": sys.version,
        },
        ffmpeg={
            "path": ffmpeg_path,
            "available": ffmpeg_available,
        },
        subsystems={
            "adapters": len(source_registry.list_adapters()),
            "sources": len(source_registry.list_sources()),
            "targets": len(target_registry.list_targets()),
            "sessions": len(session_manager.list()),
        },
    )
