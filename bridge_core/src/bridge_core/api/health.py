"""Health and status endpoints."""

import os
import platform
import sys
import threading
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
    system: dict[str, Any]
    ffmpeg: dict[str, Any]
    subsystems: dict[str, int]
    metrics: dict[str, int]


_start_time = time.time()
_last_cpu_times = os.times()
_last_cpu_check = time.time()


def get_cpu_usage() -> float:
    """Calculate CPU usage since last check."""
    global _last_cpu_times, _last_cpu_check
    current_times = os.times()
    current_time = time.time()

    dt = current_time - _last_cpu_check
    if dt <= 0:
        return 0.0

    # (user + system time)
    du = (current_times.user - _last_cpu_times.user) + (current_times.system - _last_cpu_times.system)

    _last_cpu_times = current_times
    _last_cpu_check = current_time

    return (du / dt) * 100.0


def get_rss_usage() -> int:
    """Get RSS usage in bytes."""
    try:
        import resource

        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024  # Linux returns kilobytes
    except (ImportError, AttributeError):
        return 0


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

    metrics = {}
    if hasattr(request.app.state, "metrics"):
        metrics = request.app.state.metrics.get_snapshot()

    return HealthResponse(
        status="ok",
        version="0.1.0",
        uptime=time.time() - _start_time,
        system={
            "os": platform.system(),
            "arch": platform.machine(),
            "python_version": sys.version,
            "cpu_usage_percent": get_cpu_usage(),
            "rss_bytes": get_rss_usage(),
            "thread_count": threading.active_count(),
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
        metrics=metrics,
    )
