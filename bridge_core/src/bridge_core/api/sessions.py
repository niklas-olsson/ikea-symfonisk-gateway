"""Session management endpoints."""

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/v1/sessions", tags=["sessions"])


class CreateSessionRequest(BaseModel):
    source_id: str
    target_id: str
    stream_profile: str = "mp3_48k_stereo_320"
    auto_heal: bool = True


class SessionResponse(BaseModel):
    session_id: str
    state: str


class SessionListResponse(BaseModel):
    sessions: list[dict[str, Any]]


@router.post("", response_model=SessionResponse)
async def create_session(request: CreateSessionRequest) -> SessionResponse:
    """Create a new playback session."""
    raise HTTPException(status_code=501, detail="Not implemented")


@router.get("", response_model=SessionListResponse)
async def list_sessions() -> SessionListResponse:
    """List all known sessions."""
    return SessionListResponse(sessions=[])


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(session_id: str) -> SessionResponse:
    """Get session details."""
    raise HTTPException(status_code=404, detail="Session not found")


@router.post("/{session_id}/start")
async def start_session(session_id: str) -> dict[str, Any]:
    """Start a session."""
    raise HTTPException(status_code=404, detail="Session not found")


@router.post("/{session_id}/stop")
async def stop_session(session_id: str) -> dict[str, Any]:
    """Stop a session."""
    raise HTTPException(status_code=404, detail="Session not found")
