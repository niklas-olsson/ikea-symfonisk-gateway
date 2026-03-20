"""Session lifecycle management."""

import asyncio
import logging
import time
from enum import Enum
from typing import Any
from uuid import uuid4

from ingress_sdk.protocol import AudioFrame

from bridge_core.core.event_bus import EventBus, EventType
from bridge_core.core.source_registry import SourceRegistry
from bridge_core.core.target_registry import TargetRegistry
from bridge_core.stream.pipeline import StreamPipeline

logger = logging.getLogger(__name__)


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
        self.adapter_session_id: str | None = None
        self.created_at = time.time()
        self.started_at: float | None = None
        self.stopped_at: float | None = None
        self.pipeline: StreamPipeline | None = None

    def transition_to(self, new_state: SessionState) -> None:
        """Transitions the session to a new state if valid."""
        valid_transitions = {
            SessionState.CREATED: [SessionState.PREPARING, SessionState.STARTING, SessionState.FAILED],
            SessionState.PREPARING: [SessionState.READY, SessionState.STOPPING, SessionState.FAILED],
            SessionState.READY: [SessionState.STARTING, SessionState.STOPPING, SessionState.FAILED],
            SessionState.STARTING: [SessionState.PLAYING, SessionState.STOPPING, SessionState.FAILED],
            SessionState.PLAYING: [SessionState.HEALING, SessionState.STOPPING, SessionState.FAILED],
            SessionState.HEALING: [SessionState.PLAYING, SessionState.STOPPING, SessionState.DEGRADED, SessionState.FAILED],
            SessionState.DEGRADED: [SessionState.PLAYING, SessionState.HEALING, SessionState.STOPPING, SessionState.FAILED],
            SessionState.STOPPING: [SessionState.STOPPED, SessionState.FAILED],
            SessionState.STOPPED: [SessionState.STARTING, SessionState.PREPARING, SessionState.FAILED],
            SessionState.FAILED: [SessionState.PREPARING, SessionState.STARTING, SessionState.HEALING, SessionState.STOPPING],
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
            "adapter_session_id": self.adapter_session_id,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
        }


class SessionFrameSink:
    """Bridges between ingress adapter and stream pipeline."""

    def __init__(self, pipeline: StreamPipeline):
        self.pipeline = pipeline

    def on_frame(self, data: bytes, pts_ns: int, duration_ns: int) -> None:
        """Called by the ingress adapter for each frame."""
        # Wrap in AudioFrame envelope as expected by the pipeline
        frame = AudioFrame(
            sequence=0,  # Sequence handled by jitter buffer/adapter if needed
            pts_ns=pts_ns,
            duration_ns=duration_ns,
            format={"sample_rate": 48000, "channels": 2, "bit_depth": 16},
            audio_data=data,
        )
        # Push to pipeline (non-blocking)
        asyncio.create_task(self.pipeline.push_frame(frame))


class SessionManager:
    """Manages session lifecycle."""

    def __init__(
        self,
        event_bus: EventBus,
        source_registry: SourceRegistry,
        target_registry: TargetRegistry,
        stream_publisher: Any | None = None,
    ) -> None:
        self._sessions: dict[str, Session] = {}
        self._event_bus = event_bus
        self._source_registry = source_registry
        self._target_registry = target_registry
        self._stream_publisher = stream_publisher

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

    async def start_session(self, session_id: str) -> bool:
        """Start a session and its pipeline."""
        session = self.get(session_id)
        if not session:
            return False

        if session.state == SessionState.PLAYING:
            return True

        try:
            session.transition_to(SessionState.STARTING)
        except ValueError:
            return False

        self._event_bus.emit(EventType.SESSION_STARTING, session_id=session_id)

        try:
            # 1. Prepare and start source
            prepare_res = self._source_registry.prepare_source(session.source_id)
            if not prepare_res.success:
                raise RuntimeError(f"Failed to prepare source: {prepare_res.error or prepare_res.message}")

            # 2. Setup pipeline and publisher
            if session.pipeline is None and self._stream_publisher:
                session.pipeline = StreamPipeline(session.session_id, session.stream_profile)
                self._stream_publisher.register_pipeline(session.session_id, session.pipeline)
                session.stream_url = self._stream_publisher.get_stream_url(session.session_id, session.stream_profile)

            if session.pipeline is None:
                session.pipeline = StreamPipeline(session.session_id, session.stream_profile)

            # 3. Start source with frame sink
            frame_sink = SessionFrameSink(session.pipeline)
            start_res = self._source_registry.start_source(session.source_id, frame_sink)
            if not start_res.success:
                raise RuntimeError(f"Failed to start source: {start_res.message}")
            session.adapter_session_id = start_res.session_id

            # 4. Start pipeline
            await session.pipeline.start()

            # 5. Prepare and start renderer
            prep_target_res = await self._target_registry.prepare_target(session.target_id)
            if not prep_target_res.get("success"):
                raise RuntimeError(f"Failed to prepare target: {prep_target_res.get('error')}")

            if session.stream_url:
                play_res = await self._target_registry.play_stream(session.target_id, session.stream_url)
                if not play_res.get("success"):
                    raise RuntimeError(f"Failed to start renderer playback: {play_res.get('error')}")

            session.transition_to(SessionState.PLAYING)
            self._event_bus.emit(EventType.SESSION_STARTED, session_id=session_id)
            return True

        except Exception as e:
            logger.exception(f"Error starting session {session_id}: {e}")
            self._event_bus.emit(
                EventType.SESSION_FAILED,
                session_id=session_id,
                payload={"error": str(e)},
            )
            session.transition_to(SessionState.FAILED)
            # Try to cleanup what was started
            await self.stop_session(session_id)
            return False

    async def stop_session(self, session_id: str) -> bool:
        """Stop a session and its pipeline."""
        session = self.get(session_id)
        if not session:
            return False

        if session.state == SessionState.STOPPED:
            return True

        # If we are already stopping, don't re-trigger
        if session.state == SessionState.STOPPING:
            return True

        try:
            session.transition_to(SessionState.STOPPING)
        except ValueError:
            # If transition fails, we might be in a state where we can't stop normally
            # but we should try to cleanup anyway if it's FAILED
            if session.state != SessionState.FAILED:
                return False

        self._event_bus.emit(EventType.SESSION_STOPPING, session_id=session_id)

        # 1. Stop renderer
        try:
            await self._target_registry.stop_target(session.target_id)
        except Exception as e:
            # Log but continue cleanup
            self._event_bus.emit(
                EventType.RENDERER_PLAYBACK_FAILED,
                session_id=session_id,
                payload={"error": f"Error stopping renderer: {e}"},
            )

        # 2. Stop source
        if session.adapter_session_id:
            try:
                self._source_registry.stop_source(session.source_id, session.adapter_session_id)
            except Exception as e:
                self._event_bus.emit(
                    EventType.SESSION_FAILED,
                    session_id=session_id,
                    payload={"error": f"Error stopping source: {e}"},
                )
            session.adapter_session_id = None

        # 3. Stop pipeline
        if session.pipeline:
            await session.pipeline.stop()
            if self._stream_publisher:
                self._stream_publisher.unregister_pipeline(session.session_id)

        session.transition_to(SessionState.STOPPED)
        self._event_bus.emit(EventType.SESSION_STOPPED, session_id=session_id)
        return True

    def start(self, session_id: str) -> None:
        """Start a session (synchronous shim)."""
        if asyncio.get_event_loop().is_running():
            asyncio.create_task(self.start_session(session_id))
        else:
            asyncio.run(self.start_session(session_id))

    def stop(self, session_id: str) -> None:
        """Stop a session (synchronous shim)."""
        if asyncio.get_event_loop().is_running():
            asyncio.create_task(self.stop_session(session_id))
        else:
            asyncio.run(self.stop_session(session_id))

    async def recover(self, session_id: str) -> None:
        """Attempt to recover a failed or degraded session."""
        session = self.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        session.transition_to(SessionState.HEALING)
        self._event_bus.emit(EventType.HEAL_ATTEMPTED, session_id=session_id)

        try:
            # 1. Re-heal the target
            if self._target_registry:
                heal_result = await self._target_registry.heal_target(session.target_id)
                if not heal_result.get("success"):
                    raise RuntimeError(f"Failed to heal target: {heal_result.get('error')}")

            # 2. Transition back to PLAYING
            session.transition_to(SessionState.PLAYING)
            self._event_bus.emit(EventType.HEAL_SUCCEEDED, session_id=session_id)
        except Exception as e:
            session.transition_to(SessionState.FAILED)
            self._event_bus.emit(
                EventType.HEAL_FAILED,
                session_id=session_id,
                payload={"error": str(e)},
            )

    def terminate(self, session_id: str) -> None:
        """Stop and remove a session."""
        session = self.get(session_id)
        if not session:
            return

        if session.state not in [SessionState.STOPPED, SessionState.FAILED]:
            self.stop(session_id)

        self._sessions.pop(session_id, None)

    def update_state(self, session_id: str, state: SessionState) -> None:
        """Update session state directly (bypass validation, use with caution)."""
        session = self._sessions.get(session_id)
        if session:
            session.state = state

    async def delete(self, session_id: str) -> bool:
        """Delete a session."""
        await self.stop_session(session_id)
        return self._sessions.pop(session_id, None) is not None
