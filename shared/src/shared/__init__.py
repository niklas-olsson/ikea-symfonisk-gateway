"""Shared types used across all bridge packages."""

from typing import Literal

from pydantic import BaseModel


class SessionId(BaseModel):
    """Unique session identifier."""

    value: str


class SourceId(BaseModel):
    """Unique source identifier."""

    value: str


class TargetId(BaseModel):
    """Unique target identifier."""

    value: str


class EventId(BaseModel):
    """Unique event identifier."""

    value: str


SessionState = Literal["created", "preparing", "ready", "starting", "playing", "healing", "degraded", "stopping", "stopped", "failed"]

SourceState = Literal["idle", "preparing", "active", "lost", "error"]

TargetType = Literal["single_room", "stereo_pair", "saved_group", "ad_hoc_group"]

RetryClass = Literal["transient", "recoverable", "fatal"]


class BridgeVersion(BaseModel):
    """Bridge version information."""

    version: str = "0.1.0"
    api_version: str = "v1"
