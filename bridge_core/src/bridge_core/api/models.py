"""Shared API models."""

from pydantic import BaseModel

from bridge_core.core.errors import SessionError


class ErrorResponse(BaseModel):
    """Structured error response."""

    code: str
    message: str
    last_error: SessionError | None = None
