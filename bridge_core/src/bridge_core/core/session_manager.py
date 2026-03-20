"""Session lifecycle management."""

import time
from enum import Enum
from typing import Any
from uuid import uuid4

from bridge_core.core.event_bus import EventBus, EventType


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
        self.state: SessionState = SessionState.CREATED
        self.stream_url: str | None = None
        self.created_at = time.time()
        self.started_at: float | None = None
        self.stopped_at: float | None = None

    def transition_to(self, new_state: SessionState) -> None:
        """Transitions the session to a new state if valid."""
        valid_transitions = {
            SessionState.CREATED: [SessionState.PREPARING],
            SessionState.PREPARING: [SessionState.READY, SessionState.FAILED],
            SessionState.READY: [SessionState.STARTING, SessionState.STOPPING, SessionState.FAILED],
            SessionState.STARTING: [SessionState.PLAYING, SessionState.FAILED],
            SessionState.PLAYING: [SessionState.HEALING, SessionState.STOPPING, SessionState.FAILED],
            SessionState.HEALING: [SessionState.PLAYING, SessionState.DEGRADED, SessionState.FAILED],
            SessionState.DEGRADED: [SessionState.PLAYING, SessionState.STOPPING, SessionState.FAILED],
            SessionState.STOPPING: [SessionState.STOPPED, SessionState.FAILED],
            SessionState.STOPPED: [SessionState.STARTING, SessionState.PREPARING, SessionState.FAILED],
            SessionState.FAILED: [SessionState.PREPARING, SessionState.HEALING],
        }

        if new_state not in valid_transitions.get(self.state, []):
            raise ValueError(f"Invalid transition from {self.state} to {new_state}")

        self.state = new_state
        if new_state == SessionState.PLAYING:
            self.started_at = time.time()
        elif new_state == SessionState.STOPPED:
            self.stopped_at = time.time()

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "stream_profile": self.stream_profile,
            "auto_heal": self.auto_heal,
            "state": self.state.value,
            "stream_url": self.stream_url,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
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
        self._sessions[session.session_id] = session
        self._event_bus.emit(EventType.SESSION_CREATED, payload=session.to_dict(), session_id=session.session_id)
        return session

    def get(self, session_id: str) -> Session | None:
        """Get a session by ID."""
        return self._sessions.get(session_id)

    def list(self) -> list[Session]:
        """List all sessions."""
        return list(self._sessions.values())

    def start(self, session_id: str) -> None:
        """Start a session."""
        session = self.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        session.transition_to(SessionState.STARTING)
        self._event_bus.emit(EventType.SESSION_STARTING, session_id=session_id)

        # In a real implementation, this would involve setting up the pipeline
        # For now we simulate success
        session.transition_to(SessionState.PLAYING)
        self._event_bus.emit(EventType.SESSION_STARTED, session_id=session_id)

    def stop(self, session_id: str) -> None:
        """Stop a session."""
        session = self.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        session.transition_to(SessionState.STOPPING)
        self._event_bus.emit(EventType.SESSION_STOPPING, session_id=session_id)

        session.transition_to(SessionState.STOPPED)
        self._event_bus.emit(EventType.SESSION_STOPPED, session_id=session_id)

    def recover(self, session_id: str) -> None:
        """Attempt to recover a failed or degraded session."""
        session = self.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        session.transition_to(SessionState.HEALING)
        self._event_bus.emit(EventType.HEAL_ATTEMPTED, session_id=session_id)

        # Simulating recovery success
        session.transition_to(SessionState.PLAYING)
        self._event_bus.emit(EventType.HEAL_SUCCEEDED, session_id=session_id)

    def terminate(self, session_id: str) -> None:
        """Stop and remove a session."""
        session = self.get(session_id)
        if not session:
            return

        if session.state not in [SessionState.STOPPED, SessionState.FAILED]:
            try:
                self.stop(session_id)
            except Exception:
                # Force to failed if stop fails during termination
                session.state = SessionState.FAILED

        self.delete(session_id)

    def update_state(self, session_id: str, state: SessionState) -> None:
        """Update session state directly (bypass validation, use with caution)."""
        session = self._sessions.get(session_id)
        if session:
            session.state = state

    def delete(self, session_id: str) -> bool:
        """Delete a session."""
        return self._sessions.pop(session_id, None) is not None
