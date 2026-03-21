"""Session lifecycle management."""

import asyncio
import logging
import time
from enum import Enum
from typing import Any
from uuid import uuid4

from ingress_sdk.protocol import AudioFrame

from bridge_core.core.config_store import ConfigStore
from bridge_core.core.errors import (
    FRAME_INGEST_FAILED,
    MEDIA_ENGINE_NOT_FOUND,
    PIPELINE_START_FAILED,
    RENDERER_PLAYBACK_FAILED,
    SOURCE_ADAPTER_PLATFORM_MISMATCH,
    SOURCE_START_FAILED,
    WINDOWS_OUTPUT_DEVICE_SILENT,
    SessionError,
    create_session_error,
)
from bridge_core.core.event_bus import EventBus, EventType
from bridge_core.core.source_registry import SourceRegistry
from bridge_core.core.target_registry import TargetRegistry
from bridge_core.stream.pipeline import StreamPipeline
from bridge_core.stream.utils import resolve_ffmpeg_path

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
        self.last_error: SessionError | None = None

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
            "last_error": self.last_error.model_dump() if self.last_error else None,
        }


class SessionFrameSink:
    """Bridges between ingress adapter and stream pipeline."""

    def __init__(self, pipeline: StreamPipeline, on_error: Any | None = None):
        self.pipeline = pipeline
        self.on_error_callback = on_error
        self._queue: asyncio.Queue[AudioFrame] = asyncio.Queue(maxsize=100)
        self._task: asyncio.Task[None] | None = None
        self._active = False

    def start(self) -> None:
        """Start the ingestion task."""
        self._active = True
        self._task = asyncio.create_task(self._ingestion_loop())
        self._task.add_done_callback(self._handle_task_done)

    def stop(self) -> None:
        """Stop the ingestion task."""
        self._active = False
        if self._task:
            self._task.cancel()
            self._task = None

    def _handle_task_done(self, task: asyncio.Task[None]) -> None:
        """Handle completion of the ingestion task."""
        if not task.cancelled() and task.exception():
            exc = task.exception()
            logger.error(f"Ingestion task failed: {exc}", exc_info=exc)
            if self.on_error_callback:
                self.on_error_callback(exc)

    async def _ingestion_loop(self) -> None:
        """Continuously push frames from the queue to the pipeline."""
        while self._active:
            try:
                frame = await self._queue.get()
                await self.pipeline.push_frame(frame)
                self._queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in ingestion loop: {e}")
                if self.on_error_callback:
                    self.on_error_callback(e)
                break

    def on_frame(self, data: bytes, pts_ns: int, duration_ns: int) -> None:
        """Called by the ingress adapter for each frame."""
        if not self._active:
            return

        # Wrap in AudioFrame envelope as expected by the pipeline
        frame = AudioFrame(
            sequence=0,  # Sequence handled by jitter buffer/adapter if needed
            pts_ns=pts_ns,
            duration_ns=duration_ns,
            format={"sample_rate": 48000, "channels": 2, "bit_depth": 16},
            audio_data=data,
        )
        # Push to queue (non-blocking)
        try:
            self._queue.put_nowait(frame)
        except asyncio.QueueFull:
            logger.warning("Ingestion queue full, dropping frame")

    def on_error(self, error: Exception) -> None:
        """Called by the ingress adapter when an error occurs."""
        logger.error(f"Source reported error: {error}")
        if self.on_error_callback:
            self.on_error_callback(error)


class SessionManager:
    """Manages session lifecycle."""

    def __init__(
        self,
        event_bus: EventBus,
        source_registry: SourceRegistry,
        target_registry: TargetRegistry,
        stream_publisher: Any | None = None,
        config_store: ConfigStore | None = None,
    ) -> None:
        self._sessions: dict[str, Session] = {}
        self._event_bus = event_bus
        self._source_registry = source_registry
        self._target_registry = target_registry
        self._stream_publisher = stream_publisher
        self._config_store = config_store
        self._frame_sinks: dict[str, SessionFrameSink] = {}

    def _handle_session_error(self, session_id: str, error: Exception) -> None:
        """Central error handler for session-related task failures."""
        session = self.get(session_id)
        if not session or session.state in [SessionState.FAILED, SessionState.STOPPED, SessionState.STOPPING]:
            return

        logger.error(f"Session {session_id} encountered a fatal error: {error}")
        self._event_bus.emit(
            EventType.SESSION_FAILED,
            session_id=session_id,
            payload={"error": str(error), "fatal": True},
        )

        # Update state directly to avoid transition loops if needed,
        # but here we want to trigger cleanup
        try:
            session.transition_to(SessionState.FAILED)
        except ValueError:
            session.state = SessionState.FAILED

        # Trigger cleanup
        asyncio.create_task(self.stop_session(session_id))

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

        self._event_bus.emit(
            EventType.SESSION_STARTING,
            session_id=session_id,
            payload=session.to_dict(),
        )

        try:
            # 1. Prepare and start source
            try:
                prepare_res = self._source_registry.prepare_source(session.source_id)
                if not prepare_res.success:
                    if prepare_res.code == SOURCE_ADAPTER_PLATFORM_MISMATCH:
                        session.last_error = create_session_error(
                            prepare_res.code,
                            prepare_res.message,
                        )
                    else:
                        session.last_error = create_session_error(
                            prepare_res.code or SOURCE_START_FAILED,
                            f"Failed to prepare source: {prepare_res.error or prepare_res.message}",
                        )
                    raise RuntimeError(session.last_error.message)
            except Exception as e:
                if not session.last_error:
                    session.last_error = create_session_error(SOURCE_START_FAILED, str(e))
                raise

            # 2. Setup pipeline and publisher
            try:
                if session.pipeline is None:
                    try:
                        ffmpeg_path = resolve_ffmpeg_path(self._config_store)
                    except RuntimeError as e:
                        session.last_error = create_session_error(MEDIA_ENGINE_NOT_FOUND, str(e))
                        raise

                    session.pipeline = StreamPipeline(
                        session.session_id,
                        session.stream_profile,
                        ffmpeg_path=ffmpeg_path,
                        on_error=lambda e: self._handle_session_error(session_id, e),
                    )

                if self._stream_publisher:
                    self._stream_publisher.register_pipeline(session.session_id, session.pipeline)
                    session.stream_url = self._stream_publisher.get_stream_url(session.session_id, session.stream_profile)
                    self._event_bus.emit(
                        EventType.PUBLISHER_ACTIVE,
                        session_id=session_id,
                        payload={"stream_url": session.stream_url},
                    )
            except Exception as e:
                if not session.last_error:
                    session.last_error = create_session_error(PIPELINE_START_FAILED, str(e))
                raise

            # 3. Start source with frame sink
            try:
                frame_sink = SessionFrameSink(
                    session.pipeline,
                    on_error=lambda e: self._handle_session_error(session_id, e),
                )
                frame_sink.start()
                self._frame_sinks[session_id] = frame_sink

                source_desc = self._source_registry.get_source(session.source_id)
                adapter_info = self._source_registry._get_adapter_info_for_source(session.source_id)

                start_res = self._source_registry.start_source(session.source_id, frame_sink)
                if not start_res.success:
                    if start_res.code == SOURCE_ADAPTER_PLATFORM_MISMATCH:
                        session.last_error = create_session_error(
                            start_res.code,
                            start_res.message,
                        )
                    else:
                        session.last_error = create_session_error(
                            start_res.code or SOURCE_START_FAILED, f"Failed to start source: {start_res.message}"
                        )
                    frame_sink.stop()
                    raise RuntimeError(session.last_error.message)

                # Diagnostic logging
                adapter_name = adapter_info.adapter.__class__.__name__ if adapter_info and adapter_info.adapter else "Unknown"
                logger.info(
                    f"Starting source: source_id={session.source_id} "
                    f"source_type={source_desc.source_type.value if source_desc else 'unknown'} "
                    f"platform={source_desc.platform if source_desc else 'unknown'} "
                    f"adapter={adapter_name} "
                    f"backend={start_res.backend or 'unknown'}"
                )

                session.adapter_session_id = start_res.session_id
                self._event_bus.emit(
                    EventType.SOURCE_STARTED,
                    session_id=session_id,
                    payload={"adapter_session_id": session.adapter_session_id},
                )
            except Exception as e:
                if not session.last_error:
                    session.last_error = create_session_error(SOURCE_START_FAILED, str(e))
                raise

            # 4. Start pipeline
            try:
                await session.pipeline.start()
            except Exception as e:
                session.last_error = create_session_error(PIPELINE_START_FAILED, str(e))
                raise

            # 5. Prepare and start renderer
            try:
                prep_target_res = await self._target_registry.prepare_target(session.target_id)
                if not prep_target_res.get("success"):
                    session.last_error = create_session_error(
                        RENDERER_PLAYBACK_FAILED, f"Failed to prepare target: {prep_target_res.get('error')}"
                    )
                    raise RuntimeError(session.last_error.message)

                if session.stream_url:
                    play_res = await self._target_registry.play_stream(session.target_id, session.stream_url)
                    if not play_res.get("success"):
                        session.last_error = create_session_error(
                            RENDERER_PLAYBACK_FAILED, f"Failed to start renderer playback: {play_res.get('error')}"
                        )
                        raise RuntimeError(session.last_error.message)

                    self._event_bus.emit(
                        EventType.RENDERER_PLAYBACK_STARTED,
                        session_id=session_id,
                        payload={
                            "target_id": session.target_id,
                            "stream_url": session.stream_url,
                        },
                    )
            except Exception as e:
                if not session.last_error:
                    session.last_error = create_session_error(RENDERER_PLAYBACK_FAILED, str(e))
                self._event_bus.emit(
                    EventType.RENDERER_PLAYBACK_FAILED,
                    session_id=session_id,
                    payload={
                        "error": str(e),
                        "last_error": session.last_error.model_dump(),
                        "target_id": session.target_id,
                    },
                )
                raise

            # 6. Verify frame ingestion
            frames_ingested = False
            for _ in range(6):  # 3 seconds, 500ms intervals
                if session.pipeline and session.pipeline.jitter_buffer.size_ms > 0:
                    frames_ingested = True
                    break
                await asyncio.sleep(0.5)

            if not frames_ingested:
                # Check if it's a Windows loopback source that is just silent
                health = self._source_registry.probe_source_health(session.source_id)
                source_desc = self._source_registry.get_source(session.source_id)
                adapter_info = self._source_registry._get_adapter_info_for_source(session.source_id)
                adapter_name = adapter_info.adapter.__class__.__name__ if adapter_info and adapter_info.adapter else "Unknown"

                if (
                    health
                    and health.healthy
                    and not health.signal_present
                    and source_desc
                    and source_desc.platform == "windows"
                    and source_desc.source_type == "system_audio"
                ):
                    logger.warning(f"Session {session_id}: Source is healthy but silent. Proceeding anyway. (adapter: {adapter_name})")
                    session.last_error = create_session_error(WINDOWS_OUTPUT_DEVICE_SILENT)
                else:
                    logger.error(
                        f"Session {session_id}: Failed to ingest frames from source {session.source_id} using adapter {adapter_name}"
                    )
                    session.last_error = create_session_error(FRAME_INGEST_FAILED)
                    raise RuntimeError(session.last_error.message)

            session.transition_to(SessionState.PLAYING)
            self._event_bus.emit(
                EventType.SESSION_STARTED,
                session_id=session_id,
                payload=session.to_dict(),
            )
            return True

        except Exception as e:
            logger.exception(f"Error starting session {session_id}: {e}")
            if not session.last_error:
                session.last_error = create_session_error("session_start_failed", str(e))

            self._event_bus.emit(
                EventType.SESSION_FAILED,
                session_id=session_id,
                payload=session.to_dict(),
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

        self._event_bus.emit(
            EventType.SESSION_STOPPING,
            session_id=session_id,
            payload=session.to_dict(),
        )

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

        # 2. Stop source and frame sink
        if session_id in self._frame_sinks:
            self._frame_sinks[session_id].stop()
            del self._frame_sinks[session_id]

        if session.adapter_session_id:
            try:
                self._source_registry.stop_source(session.source_id, session.adapter_session_id)
            except Exception as e:
                logger.error(f"Error stopping source for session {session_id}: {e}")
            session.adapter_session_id = None

        # 3. Stop pipeline
        if session.pipeline:
            await session.pipeline.stop()
            if self._stream_publisher:
                self._stream_publisher.unregister_pipeline(session.session_id)

        session.transition_to(SessionState.STOPPED)
        self._event_bus.emit(
            EventType.SESSION_STOPPED,
            session_id=session_id,
            payload=session.to_dict(),
        )
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
