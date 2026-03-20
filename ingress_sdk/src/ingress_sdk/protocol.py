"""Protocol types for the Bridge Ingress Protocol.

Defines the msgpack envelope format for communication between
ingress adapters and the bridge core.
"""

from enum import Enum
from typing import Any, cast

import msgpack  # type: ignore[import-untyped]
from pydantic import BaseModel


class MessageType(str, Enum):
    HELLO = "HELLO"
    ACCEPT = "ACCEPT"
    AUDIO_FRAME = "AUDIO_FRAME"
    HEARTBEAT = "HEARTBEAT"
    HEALTH = "HEALTH"
    ERROR = "ERROR"
    STOP = "STOP"


class Envelope(BaseModel):
    """Base envelope for all protocol messages."""

    type: MessageType
    session_id: str | None = None
    payload: dict[str, Any] | None = None

    def to_msgpack(self) -> bytes:
        data = self.model_dump(mode="json", exclude_none=True)
        return cast(bytes, msgpack.packb(data, use_bin_type=True))

    @classmethod
    def from_msgpack(cls, data: bytes) -> "Envelope":
        unpacked = msgpack.unpackb(data, raw=False)
        return cls(**cast(dict[str, Any], unpacked))


class HelloMessage(Envelope):
    """Initial handshake from adapter to core."""

    type: MessageType = MessageType.HELLO
    adapter_id: str
    platform: str
    version: str
    capabilities: dict[str, Any]

    def __init__(self, **data: Any) -> None:
        data["type"] = MessageType.HELLO
        super().__init__(**data)


class AcceptMessage(Envelope):
    """Acceptance from core to adapter after HELLO."""

    type: MessageType = MessageType.ACCEPT
    session_id: str | None = None
    source_id: str = ""
    required_format: dict[str, Any] = {}
    buffer_target_ms: int = 250

    def __init__(self, **data: Any) -> None:
        data["type"] = MessageType.ACCEPT
        super().__init__(**data)


class AudioFrame(Envelope):
    """Audio frame in canonical PCM format."""

    type: MessageType = MessageType.AUDIO_FRAME
    sequence: int
    pts_ns: int
    duration_ns: int
    format: dict[str, Any]
    payload_encoding: str = "raw"
    audio_data: bytes = b""

    def __init__(self, **data: Any) -> None:
        data["type"] = MessageType.AUDIO_FRAME
        super().__init__(**data)


class Heartbeat(Envelope):
    """Keepalive message from adapter."""

    type: MessageType = MessageType.HEARTBEAT

    def __init__(self, **data: Any) -> None:
        data["type"] = MessageType.HEARTBEAT
        super().__init__(**data)


class HealthMessage(Envelope):
    """Health status from adapter."""

    type: MessageType = MessageType.HEALTH
    source_state: str
    signal_present: bool
    dropped_frames: int = 0
    last_error: str | None = None

    def __init__(self, **data: Any) -> None:
        data["type"] = MessageType.HEALTH
        super().__init__(**data)


class ErrorMessage(Envelope):
    """Error notification from either side."""

    type: MessageType = MessageType.ERROR
    code: str
    message: str

    def __init__(self, **data: Any) -> None:
        data["type"] = MessageType.ERROR
        super().__init__(**data)
