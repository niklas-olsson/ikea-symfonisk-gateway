"""Health and status endpoints."""

import time

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="", tags=["health"])


class HealthResponse(BaseModel):
    status: str
    version: str
    uptime: float


_start_time = time.time()


@router.get("/health", response_model=HealthResponse)
async def get_health() -> HealthResponse:
    """Return bridge health status."""
    return HealthResponse(
        status="ok",
        version="0.1.0",
        uptime=time.time() - _start_time,
    )
