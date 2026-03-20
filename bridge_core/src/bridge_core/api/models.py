"""Shared API models."""

from pydantic import BaseModel


class ErrorResponse(BaseModel):
    """Structured error response."""

    code: str
    message: str
