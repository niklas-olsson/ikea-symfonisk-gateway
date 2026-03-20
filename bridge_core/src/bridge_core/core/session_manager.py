"""Session lifecycle management."""

import time
from enum import Enum
from typing import Any
from uuid import uuid4

from bridge_core.core.event_bus import EventBus, EventType, Severity


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

    def __init__(self, event_bus: EventBus) -> None:
        self._sessions: dict[str, Session] = {}
        self._event_bus = event_bus

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
        session.created_at = time.time()
        self._sessions[session.session_id] = session

        self._event_bus.emit(
            EventType.SESSION_CREATED,
            payload=session.to_dict(),
            session_id=session.session_id,
        )

        return session

    def start(self, session_id: str, stream_url: str) -> bool:
        """Start a session."""
        session = self._sessions.get(session_id)
        if not session:
            return False

        session.state = SessionState.STARTING
        session.stream_url = stream_url
        self._event_bus.emit(
            EventType.SESSION_STARTING,
            payload=session.to_dict(),
            session_id=session.session_id,
        )

        session.state = SessionState.PLAYING
        session.started_at = time.time()
        self._event_bus.emit(
            EventType.SESSION_STARTED,
            payload=session.to_dict(),
            session_id=session.session_id,
        )

        return True

    def stop(self, session_id: str) -> bool:
        """Stop a session."""
        session = self._sessions.get(session_id)
        if not session:
            return False

        session.state = SessionState.STOPPING
        self._event_bus.emit(
            EventType.SESSION_STOPPING,
            payload=session.to_dict(),
            session_id=session.session_id,
        )

        session.state = SessionState.STOPPED
        self._event_bus.emit(
            EventType.SESSION_STOPPED,
            payload=session.to_dict(),
            session_id=session.session_id,
        )

        return True

    def recover(self, session_id: str) -> bool:
        """Attempt to recover a session."""
        session = self._sessions.get(session_id)
        if not session or not session.auto_heal:
            return False

        session.state = SessionState.HEALING
        self._event_bus.emit(
            EventType.HEAL_ATTEMPTED,
            payload=session.to_dict(),
            session_id=session.session_id,
            severity=Severity.WARNING,
        )

        # Here we'd actually trigger recovery logic via adapters/pipeline
        # Assuming success for now:
        session.state = SessionState.PLAYING
        self._event_bus.emit(
            EventType.HEAL_SUCCEEDED,
            payload=session.to_dict(),
            session_id=session.session_id,
        )

        return True

    def terminate(self, session_id: str, reason: str = "failed") -> bool:
        """Terminate a session unexpectedly."""
        session = self._sessions.get(session_id)
        if not session:
            return False

        session.state = SessionState.FAILED
        self._event_bus.emit(
            EventType.SESSION_FAILED,
            payload={"reason": reason, **session.to_dict()},
            session_id=session.session_id,
            severity=Severity.ERROR,
        )

        return True

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
