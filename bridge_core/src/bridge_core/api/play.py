"""Canonical playback orchestration endpoint."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from bridge_core.api.models import ErrorResponse
from bridge_core.api.sessions import SessionResponse
from bridge_core.core import SessionManager
from bridge_core.core.errors import QUIESCED_SESSION_CONFLICT, SessionConflictError

router = APIRouter(prefix="/v1/play", tags=["play"])


class PlayRequest(BaseModel):
    source_id: str
    target_id: str
    conflict_policy: str = "takeover"
    stream_profile: str = "auto"
    auto_heal: bool = True


@router.post("", response_model=SessionResponse, responses={400: {"model": ErrorResponse}, 409: {"model": ErrorResponse}})
async def play(request: Request, body: PlayRequest) -> SessionResponse:
    """Canonical playback orchestration."""
    manager: SessionManager = request.app.state.session_manager
    source_registry = request.app.state.source_registry
    try:
        session = await manager.play(
            source_id=body.source_id,
            target_id=body.target_id,
            conflict_policy=body.conflict_policy,
            stream_profile=body.stream_profile,
            auto_heal=body.auto_heal,
        )
        source_health = source_registry.get_source_health(session.source_id)
        return SessionResponse(**session.to_dict(source_health=source_health))
    except SessionConflictError as e:
        raise HTTPException(
            status_code=409,
            detail={"code": QUIESCED_SESSION_CONFLICT, "message": str(e)},
        )
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_REQUEST", "message": str(e)},
        )
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail={"code": "PLAY_FAILED", "message": str(e)},
        )
