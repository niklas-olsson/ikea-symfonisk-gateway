"""Session management endpoints."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from bridge_core.core import SessionManager

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
async def create_session(request: Request, body: CreateSessionRequest) -> SessionResponse:
    """Create a new playback session."""
    manager: SessionManager = request.app.state.session_manager
    session = manager.create(
        source_id=body.source_id,
        target_id=body.target_id,
        stream_profile=body.stream_profile,
        auto_heal=body.auto_heal,
    )
    return SessionResponse(session_id=session.session_id, state=session.state.value)


@router.get("", response_model=SessionListResponse)
async def list_sessions(request: Request) -> SessionListResponse:
    """List all known sessions."""
    manager: SessionManager = request.app.state.session_manager
    sessions = [s.to_dict() for s in manager.list()]
    return SessionListResponse(sessions=sessions)


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(request: Request, session_id: str) -> SessionResponse:
    """Get session details."""
    manager: SessionManager = request.app.state.session_manager
    session = manager.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return SessionResponse(session_id=session.session_id, state=session.state.value)


@router.post("/{session_id}/start")
async def start_session(request: Request, session_id: str) -> dict[str, Any]:
    """Start a session."""
    manager: SessionManager = request.app.state.session_manager
    success = await manager.start_session(session_id)
    if not success:
        raise HTTPException(status_code=400, detail="Failed to start session")
    return {"success": True}


@router.post("/{session_id}/stop")
async def stop_session(request: Request, session_id: str) -> dict[str, Any]:
    """Stop a session."""
    manager: SessionManager = request.app.state.session_manager
    success = await manager.stop_session(session_id)
    if not success:
        raise HTTPException(status_code=400, detail="Failed to stop session")
    return {"success": True}
