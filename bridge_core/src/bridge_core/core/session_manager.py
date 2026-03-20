"""Session lifecycle management."""

from enum import Enum
from typing import Any
from uuid import uuid4


class SessionState(str, Enum):
    CREATED = "created"
    PREPARING = "preparing"
    READY = "ready"
    STARTING = "starting"
    PLAYING = "playing"
    HEALING = "healing"
    DEGRADED = "degraded"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


class Session:
    """Represents a playback session."""

    def __init__(
        self,
        source_id: str,
        target_id: str,
        stream_profile: str = "mp3_48k_stereo_320",
        auto_heal: bool = True,
    ):
        self.session_id = f"sess_{uuid4().hex[:12]}"
        self.source_id = source_id
        self.target_id = target_id
        self.stream_profile = stream_profile
        self.auto_heal = auto_heal
        self.state = SessionState.CREATED
        self.stream_url: str | None = None
        self.created_at: float | None = None
        self.started_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "stream_profile": self.stream_profile,
            "auto_heal": self.auto_heal,
            "state": self.state.value,
            "stream_url": self.stream_url,
        }


class SessionManager:
    """Manages session lifecycle."""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def create(
        self,
        source_id: str,
        target_id: str,
        stream_profile: str = "mp3_48k_stereo_320",
        auto_heal: bool = True,
    ) -> Session:
        """Create a new session."""
        session = Session(
            source_id=source_id,
            target_id=target_id,
            stream_profile=stream_profile,
            auto_heal=auto_heal,
        )
        self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str) -> Session | None:
        """Get a session by ID."""
        return self._sessions.get(session_id)

    def list(self) -> list[Session]:
        """List all sessions."""
        return list(self._sessions.values())

    def update_state(self, session_id: str, state: SessionState) -> None:
        """Update session state."""
        session = self._sessions.get(session_id)
        if session:
            session.state = state

    def delete(self, session_id: str) -> bool:
        """Delete a session."""
        return self._sessions.pop(session_id, None) is not None
