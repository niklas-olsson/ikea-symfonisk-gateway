"""Session management endpoints."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from bridge_core.api.models import ErrorResponse
from bridge_core.core import SessionManager
from bridge_core.core.errors import SessionError

router = APIRouter(prefix="/v1/sessions", tags=["sessions"])


class CreateSessionRequest(BaseModel):
    source_id: str
    target_id: str
    stream_profile: str = "mp3_48k_stereo_320"
    auto_heal: bool = True


class SessionResponse(BaseModel):
    session_id: str
    source_id: str
    target_id: str
    stream_profile: str
    auto_heal: bool
    state: str
    stream_url: str | None = None
    adapter_session_id: str | None = None
    created_at: float
    started_at: float | None = None
    stopped_at: float | None = None
    last_error: SessionError | None = None


class SessionListResponse(BaseModel):
    sessions: list[SessionResponse]


@router.post("", response_model=SessionResponse, responses={400: {"model": ErrorResponse}})
async def create_session(request: Request, body: CreateSessionRequest) -> SessionResponse:
    """Create a new playback session."""
    manager: SessionManager = request.app.state.session_manager
    try:
        session = manager.create(
            source_id=body.source_id,
            target_id=body.target_id,
            stream_profile=body.stream_profile,
            auto_heal=body.auto_heal,
        )
        return SessionResponse(**session.to_dict())
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail={"code": "SESSION_CREATE_FAILED", "message": str(e)},
        )


@router.get("", response_model=SessionListResponse)
async def list_sessions(request: Request) -> SessionListResponse:
    """List all known sessions."""
    manager: SessionManager = request.app.state.session_manager
    sessions = [SessionResponse(**s.to_dict()) for s in manager.list()]
    return SessionListResponse(sessions=sessions)


@router.get("/{session_id}", response_model=SessionResponse, responses={404: {"model": ErrorResponse}})
async def get_session(request: Request, session_id: str) -> SessionResponse:
    """Get session details."""
    manager: SessionManager = request.app.state.session_manager
    session = manager.get(session_id)
    if not session:
        raise HTTPException(
            status_code=404,
            detail={"code": "SESSION_NOT_FOUND", "message": f"Session {session_id} not found"},
        )
    return SessionResponse(**session.to_dict())


@router.post("/{session_id}/start", responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}})
async def start_session(request: Request, session_id: str) -> dict[str, Any]:
    """Start a session."""
    manager: SessionManager = request.app.state.session_manager
    session = manager.get(session_id)
    if not session:
        raise HTTPException(
            status_code=404,
            detail={"code": "SESSION_NOT_FOUND", "message": f"Session {session_id} not found"},
        )

    success = await manager.start_session(session_id)
    if not success:
        error_detail: dict[str, Any] = {"code": "SESSION_START_FAILED", "message": "Failed to start session"}
        if session.last_error:
            error_detail["last_error"] = session.last_error.model_dump()
            error_detail["message"] = session.last_error.message

        raise HTTPException(
            status_code=400,
            detail=error_detail,
        )
    return {"success": True}


@router.post("/{session_id}/stop", responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}})
async def stop_session(request: Request, session_id: str) -> dict[str, Any]:
    """Stop a session."""
    manager: SessionManager = request.app.state.session_manager
    session = manager.get(session_id)
    if not session:
        raise HTTPException(
            status_code=404,
            detail={"code": "SESSION_NOT_FOUND", "message": f"Session {session_id} not found"},
        )

    success = await manager.stop_session(session_id)
    if not success:
        raise HTTPException(
            status_code=400,
            detail={"code": "SESSION_STOP_FAILED", "message": "Failed to stop session"},
        )
    return {"success": True}


@router.post("/{session_id}/recover", responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}})
async def recover_session(request: Request, session_id: str) -> dict[str, Any]:
    """Attempt to recover a failed or degraded session."""
    manager: SessionManager = request.app.state.session_manager
    try:
        await manager.recover(session_id)
        return {"success": True}
    except ValueError as e:
        raise HTTPException(
            status_code=404,
            detail={"code": "SESSION_NOT_FOUND", "message": str(e)},
        )
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail={"code": "SESSION_RECOVERY_FAILED", "message": str(e)},
        )
