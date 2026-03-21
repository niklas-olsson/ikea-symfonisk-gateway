"""Session lifecycle management."""

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from ingress_sdk.protocol import AudioFrame
from ingress_sdk.types import SourceType

from bridge_core.core.config_store import ConfigStore
from bridge_core.core.errors import (
    MEDIA_ENGINE_NOT_FOUND,
    PIPELINE_START_FAILED,
    RENDERER_PLAYBACK_FAILED,
    SOURCE_ADAPTER_PLATFORM_MISMATCH,
    SOURCE_START_FAILED,
    WINDOWS_LOOPBACK_CAPTURE_STALLED,
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
AUDIO_STABLE_KEEPALIVE_ENABLED_DEFAULT = False
AUDIO_EXPERIMENTAL_KEEPALIVE_ENABLED_DEFAULT = True
AUDIO_STABLE_KEEPALIVE_IDLE_THRESHOLD_MS_DEFAULT = 200
AUDIO_STABLE_KEEPALIVE_FRAME_DURATION_MS_DEFAULT = 20
AUDIO_EXPERIMENTAL_KEEPALIVE_IDLE_THRESHOLD_MS_DEFAULT = 100
AUDIO_EXPERIMENTAL_KEEPALIVE_FRAME_DURATION_MS_DEFAULT = 10
AUDIO_SOURCE_OUTAGE_GRACE_MS_DEFAULT = 5000
AUDIO_STABLE_LIVE_JITTER_TARGET_MS_DEFAULT = 250
AUDIO_EXPERIMENTAL_LIVE_JITTER_TARGET_MS_DEFAULT = 60
AUDIO_LIVE_STARTUP_ALLOW_SILENT_SOURCE_DEFAULT = True
AUDIO_LIVE_STARTUP_VIABILITY_TIMEOUT_MS_DEFAULT = 1000
AUDIO_DELIVERY_PROFILE_DEFAULT = "stable"
AUDIO_AUTO_HEAL_DELIVERY_ENABLED_DEFAULT = True
AUDIO_PRIMARY_HEALTH_REQUIRE_YIELD_PROGRESS_DEFAULT = True
AUDIO_STABLE_TRANSPORT_HEARTBEAT_WINDOW_MS_DEFAULT = 1000
AUDIO_EXPERIMENTAL_TRANSPORT_HEARTBEAT_WINDOW_MS_DEFAULT = 750
AUDIO_STABLE_PRIMARY_CLIENT_QUEUE_BYTES_DEFAULT = 262144
AUDIO_STABLE_PRIMARY_CLIENT_OVERFLOW_GRACE_MS_DEFAULT = 1500
AUDIO_STABLE_PRIMARY_CLIENT_MAX_BACKLOG_MS_DEFAULT = 1500
AUDIO_EXPERIMENTAL_PRIMARY_CLIENT_QUEUE_BYTES_DEFAULT = 131072
AUDIO_EXPERIMENTAL_PRIMARY_CLIENT_OVERFLOW_GRACE_MS_DEFAULT = 750
AUDIO_EXPERIMENTAL_PRIMARY_CLIENT_MAX_BACKLOG_MS_DEFAULT = 750
AUDIO_STABLE_PRIMARY_DETACH_GRACE_MS_DEFAULT = 10000
AUDIO_EXPERIMENTAL_PRIMARY_DETACH_GRACE_MS_DEFAULT = 5000
AUDIO_AUX_CLIENT_QUEUE_BYTES_DEFAULT = 131072
AUDIO_AUX_CLIENT_OVERFLOW_GRACE_MS_DEFAULT = 750
AUDIO_AUX_CLIENT_MAX_BACKLOG_MS_DEFAULT = 1500
AUDIO_PRIMARY_ATTACH_GRACE_MS_DEFAULT = 5000
AUDIO_CLIENT_QUEUE_BYTES_DEFAULT = 131072
AUDIO_CLIENT_OVERFLOW_GRACE_MS_DEFAULT = 750
AUDIO_CLIENT_MAX_BACKLOG_MS_DEFAULT = 1500
AUDIO_DEBUG_CAPTURE_ENABLED_DEFAULT = False
AUDIO_DEBUG_PACING_LOGS_ENABLED_DEFAULT = False
WINDOWS_SOURCE_ACTIVITY_WATCHDOG_MS_DEFAULT = 15000
DeliveryProfile = Literal["stable", "experimental"]


@dataclass(frozen=True)
class DeliveryProfileDefaults:
    delivery_profile: DeliveryProfile
    keepalive_idle_threshold_ms: int
    keepalive_frame_duration_ms: int
    live_jitter_target_ms: int
    transport_heartbeat_window_ms: int
    primary_client_queue_bytes: int
    primary_client_overflow_grace_ms: int
    primary_client_max_backlog_ms: int
    primary_detach_grace_ms: int


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
        self.media_state: str = "playing_degraded"
        self.media_reason: str | None = None
        self.media_heal_attempts = 0
        self.media_heal_in_progress = False
        self.media_epoch = 0
        self.last_media_heal_at: float | None = None
        self.last_media_healthy_at: float | None = None
        self.primary_delivery_path_id: str | None = None
        self.primary_attached_at: float | None = None
        self.primary_remote_addr: str | None = None
        self.primary_user_agent: str | None = None
        self.primary_established = False
        self.last_renderer_play_requested_monotonic: float | None = None
        self.primary_attach_grace_deadline_monotonic: float | None = None
        self.primary_delivery_miss_started_at: float | None = None
        self.primary_delivery_recovery_started_at: float | None = None
        self.primary_detach_started_at: float | None = None
        self.primary_detach_grace_deadline_monotonic: float | None = None
        self.startup_viability_ms: float | None = None
        self.first_primary_attach_ms: float | None = None
        self.delivery_heal_attempts_total = 0
        self.delivery_profile: DeliveryProfile = "stable"
        self.effective_delivery_profile: DeliveryProfile = "stable"
        self.primary_detach_events_total = 0
        self.primary_detach_grace_recoveries_total = 0
        self.primary_detach_escalations_total = 0
        self.last_primary_detach_at: float | None = None
        self.recent_primary_detach_times_monotonic: deque[float] = deque()
        self.auto_fell_back_to_stable = False
        self.fallback_reason: str | None = None

    def transition_to(self, new_state: SessionState) -> None:
        """Transitions the session to a new state if valid."""
        valid_transitions = {
            SessionState.CREATED: [SessionState.PREPARING, SessionState.STARTING, SessionState.FAILED],
            SessionState.PREPARING: [SessionState.READY, SessionState.STOPPING, SessionState.FAILED],
            SessionState.READY: [SessionState.STARTING, SessionState.STOPPING, SessionState.FAILED],
            SessionState.STARTING: [SessionState.PLAYING, SessionState.STOPPING, SessionState.FAILED],
            SessionState.PLAYING: [SessionState.HEALING, SessionState.DEGRADED, SessionState.STOPPING, SessionState.FAILED],
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
        diagnostics: dict[str, Any] = {}
        if self.pipeline and hasattr(self.pipeline, "get_diagnostics_snapshot"):
            try:
                diagnostics = self.pipeline.get_diagnostics_snapshot()
            except Exception:
                diagnostics = {}

        now = time.monotonic()
        last_stdout_read_monotonic = diagnostics.get("last_stdout_read_monotonic")
        last_client_fanout_monotonic = diagnostics.get("last_client_fanout_monotonic")
        last_client_overflow_monotonic = diagnostics.get("last_client_overflow_monotonic")
        last_client_stall_disconnect_monotonic = diagnostics.get("last_client_stall_disconnect_monotonic")
        last_primary_enqueue_monotonic = diagnostics.get("last_primary_enqueue_monotonic")
        last_primary_dequeue_monotonic = diagnostics.get("last_primary_dequeue_monotonic")
        last_primary_yield_monotonic = diagnostics.get("last_primary_yield_monotonic")
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
            "media_status": {
                "state": self.media_state,
                "reason": self.media_reason,
                "encoder_alive": bool(diagnostics.get("encoder_alive", diagnostics.get("transport_alive", False))),
                "delivery_alive": bool(diagnostics.get("delivery_alive", False)),
                "primary_delivery_alive": bool(diagnostics.get("primary_delivery_alive", False)),
                "transport_alive": bool(diagnostics.get("transport_alive", False)),
                "active_client_count": int(diagnostics.get("active_client_count") or 0),
                "effective_client_count": int(diagnostics.get("effective_client_count") or 0),
                "primary_client_count": int(diagnostics.get("primary_client_count") or 0),
                "primary_effective_client_count": int(diagnostics.get("primary_effective_client_count") or 0),
                "encoded_bytes_emitted_last_window": int(diagnostics.get("encoded_bytes_emitted_last_window") or 0),
                "backpressure_events_total": int(diagnostics.get("backpressure_events_total") or 0),
                "client_queue_overflow_events_total": int(diagnostics.get("client_queue_overflow_events_total") or 0),
                "client_stall_disconnects_total": int(diagnostics.get("client_stall_disconnects_total") or 0),
                "startup_viability_ms": self.startup_viability_ms,
                "first_primary_attach_ms": self.first_primary_attach_ms,
                "primary_resume_to_first_successful_yield_ms": diagnostics.get("primary_resume_to_first_successful_yield_ms"),
                "max_primary_backlog_ms_observed": diagnostics.get("max_primary_backlog_ms_observed"),
                "delivery_heal_attempts_total": self.delivery_heal_attempts_total,
                "delivery_profile": self.delivery_profile,
                "effective_delivery_profile": self.effective_delivery_profile,
                "running_delivery_profile": diagnostics.get("running_delivery_profile", diagnostics.get("delivery_profile")),
                "auto_fell_back_to_stable": self.auto_fell_back_to_stable,
                "fallback_reason": self.fallback_reason,
                "primary_detach_events_total": self.primary_detach_events_total,
                "primary_detach_grace_recoveries_total": self.primary_detach_grace_recoveries_total,
                "primary_detach_escalations_total": self.primary_detach_escalations_total,
                "last_stdout_read_age_ms": (now - last_stdout_read_monotonic) * 1000
                if isinstance(last_stdout_read_monotonic, (int, float))
                else None,
                "last_client_fanout_age_ms": (now - last_client_fanout_monotonic) * 1000
                if isinstance(last_client_fanout_monotonic, (int, float))
                else None,
                "last_client_overflow_age_ms": (now - last_client_overflow_monotonic) * 1000
                if isinstance(last_client_overflow_monotonic, (int, float))
                else None,
                "last_client_stall_disconnect_age_ms": (now - last_client_stall_disconnect_monotonic) * 1000
                if isinstance(last_client_stall_disconnect_monotonic, (int, float))
                else None,
                "last_primary_enqueue_age_ms": (now - last_primary_enqueue_monotonic) * 1000
                if isinstance(last_primary_enqueue_monotonic, (int, float))
                else None,
                "last_primary_dequeue_age_ms": (now - last_primary_dequeue_monotonic) * 1000
                if isinstance(last_primary_dequeue_monotonic, (int, float))
                else None,
                "last_primary_yield_age_ms": (now - last_primary_yield_monotonic) * 1000
                if isinstance(last_primary_yield_monotonic, (int, float))
                else None,
                "keepalive_active": bool(diagnostics.get("keepalive_active", False)),
            },
        }


class SessionFrameSink:
    """Bridges between ingress adapter and stream pipeline."""

    def __init__(self, pipeline: StreamPipeline, on_error: Any | None = None):
        self.pipeline = pipeline
        self.on_error_callback = on_error
        self._queue: asyncio.Queue[AudioFrame] = asyncio.Queue(maxsize=100)
        self._task: asyncio.Task[None] | None = None
        self._active = False
        self._next_sequence = 0
        self._media_epoch = 0

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
                pipeline = self.pipeline
                await pipeline.push_frame(frame)
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
            sequence=self._next_sequence,
            pts_ns=pts_ns,
            duration_ns=duration_ns,
            format={"sample_rate": 48000, "channels": 2, "bit_depth": 16},
            audio_data=data,
        )
        self._next_sequence += 1
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

    def set_pipeline(self, pipeline: StreamPipeline, media_epoch: int) -> None:
        """Swap the downstream pipeline without resetting frame sequencing."""
        self.pipeline = pipeline
        self._media_epoch = media_epoch


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
        self._session_monitors: dict[str, asyncio.Task[None]] = {}
        self._session_heals: dict[str, asyncio.Task[None]] = {}

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

    def _get_bool_config(self, key: str, default: bool) -> bool:
        if not self._config_store:
            return default
        value = self._config_store.get(key, default)
        return value if isinstance(value, bool) else default

    def _get_int_config(self, key: str, default: int) -> int:
        if not self._config_store:
            return default
        value = self._config_store.get(key, default)
        return value if isinstance(value, int) and not isinstance(value, bool) else default

    def _get_optional_str_config(self, key: str) -> str | None:
        if not self._config_store:
            return None
        value = self._config_store.get(key, None)
        return value if isinstance(value, str) and value else None

    def _get_optional_int_config(self, key: str) -> int | None:
        if not self._config_store:
            return None
        value = self._config_store.get(key, None)
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    def _normalize_delivery_profile(self, value: object) -> DeliveryProfile | None:
        if value == "stable":
            return "stable"
        if value == "experimental":
            return "experimental"
        return None

    def _get_delivery_profile(self) -> DeliveryProfile:
        configured = self._normalize_delivery_profile(self._get_optional_str_config("audio_delivery_profile"))
        return configured or "stable"

    def _get_delivery_profile_overrides(self) -> dict[str, DeliveryProfile]:
        if not self._config_store:
            return {}
        value = self._config_store.get("audio_delivery_profile_overrides", None)
        if not isinstance(value, dict):
            return {}

        overrides: dict[str, DeliveryProfile] = {}
        for source_id, configured_profile in value.items():
            if not isinstance(source_id, str):
                continue
            normalized = self._normalize_delivery_profile(configured_profile)
            if normalized is None:
                continue
            overrides[source_id] = normalized
        return overrides

    def _resolve_delivery_profile_for_source(self, source_id: str) -> DeliveryProfile:
        return self._get_delivery_profile_overrides().get(source_id, self._get_delivery_profile())

    def _resolve_delivery_profile_defaults(self, profile: DeliveryProfile) -> DeliveryProfileDefaults:
        if profile == "experimental":
            return DeliveryProfileDefaults(
                delivery_profile=profile,
                keepalive_idle_threshold_ms=AUDIO_EXPERIMENTAL_KEEPALIVE_IDLE_THRESHOLD_MS_DEFAULT,
                keepalive_frame_duration_ms=AUDIO_EXPERIMENTAL_KEEPALIVE_FRAME_DURATION_MS_DEFAULT,
                live_jitter_target_ms=AUDIO_EXPERIMENTAL_LIVE_JITTER_TARGET_MS_DEFAULT,
                transport_heartbeat_window_ms=AUDIO_EXPERIMENTAL_TRANSPORT_HEARTBEAT_WINDOW_MS_DEFAULT,
                primary_client_queue_bytes=AUDIO_EXPERIMENTAL_PRIMARY_CLIENT_QUEUE_BYTES_DEFAULT,
                primary_client_overflow_grace_ms=AUDIO_EXPERIMENTAL_PRIMARY_CLIENT_OVERFLOW_GRACE_MS_DEFAULT,
                primary_client_max_backlog_ms=AUDIO_EXPERIMENTAL_PRIMARY_CLIENT_MAX_BACKLOG_MS_DEFAULT,
                primary_detach_grace_ms=AUDIO_EXPERIMENTAL_PRIMARY_DETACH_GRACE_MS_DEFAULT,
            )
        return DeliveryProfileDefaults(
            delivery_profile=profile,
            keepalive_idle_threshold_ms=AUDIO_STABLE_KEEPALIVE_IDLE_THRESHOLD_MS_DEFAULT,
            keepalive_frame_duration_ms=AUDIO_STABLE_KEEPALIVE_FRAME_DURATION_MS_DEFAULT,
            live_jitter_target_ms=AUDIO_STABLE_LIVE_JITTER_TARGET_MS_DEFAULT,
            transport_heartbeat_window_ms=AUDIO_STABLE_TRANSPORT_HEARTBEAT_WINDOW_MS_DEFAULT,
            primary_client_queue_bytes=AUDIO_STABLE_PRIMARY_CLIENT_QUEUE_BYTES_DEFAULT,
            primary_client_overflow_grace_ms=AUDIO_STABLE_PRIMARY_CLIENT_OVERFLOW_GRACE_MS_DEFAULT,
            primary_client_max_backlog_ms=AUDIO_STABLE_PRIMARY_CLIENT_MAX_BACKLOG_MS_DEFAULT,
            primary_detach_grace_ms=AUDIO_STABLE_PRIMARY_DETACH_GRACE_MS_DEFAULT,
        )

    def _resolve_profile_int_override(
        self,
        profile: DeliveryProfile,
        per_profile_key_suffix: str,
        global_key: str,
        default: int,
    ) -> int:
        per_profile_key = f"audio_{profile}_{per_profile_key_suffix}"
        per_profile_override = self._get_optional_int_config(per_profile_key)
        if per_profile_override is not None:
            return per_profile_override

        global_override = self._get_optional_int_config(global_key)
        if global_override is not None:
            return global_override

        return default

    def _resolve_profile_bool_override(
        self,
        profile: DeliveryProfile,
        per_profile_key_suffix: str,
        global_key: str,
        default: bool,
    ) -> bool:
        per_profile_key = f"audio_{profile}_{per_profile_key_suffix}"
        if self._config_store:
            per_profile_value = self._config_store.get(per_profile_key, None)
            if isinstance(per_profile_value, bool):
                return per_profile_value

        global_override = self._config_store.get(global_key, None) if self._config_store else None
        if isinstance(global_override, bool):
            return global_override

        return default

    def _is_windows_system_output_source(self, source_id: str) -> bool:
        source_binding = self._source_registry.resolve_source(source_id)
        source = source_binding.source if source_binding else None
        return bool(source and source.platform == "windows" and source.source_type == SourceType.SYSTEM_OUTPUT)

    def _build_pipeline_kwargs(self, source_id: str, profile: DeliveryProfile | None = None) -> dict[str, Any]:
        profile = profile or self._resolve_delivery_profile_for_source(source_id)
        defaults = self._resolve_delivery_profile_defaults(profile)
        return {
            "keepalive_enabled": self._resolve_profile_bool_override(
                profile,
                "keepalive_enabled",
                "audio_keepalive_enabled",
                AUDIO_EXPERIMENTAL_KEEPALIVE_ENABLED_DEFAULT if profile == "experimental" else AUDIO_STABLE_KEEPALIVE_ENABLED_DEFAULT,
            ),
            "keepalive_idle_threshold_ms": self._resolve_profile_int_override(
                profile,
                "keepalive_idle_threshold_ms",
                "audio_keepalive_idle_threshold_ms",
                defaults.keepalive_idle_threshold_ms,
            ),
            "keepalive_frame_duration_ms": self._resolve_profile_int_override(
                profile,
                "keepalive_frame_duration_ms",
                "audio_keepalive_frame_duration_ms",
                defaults.keepalive_frame_duration_ms,
            ),
            "source_outage_grace_ms": self._get_int_config(
                "audio_source_outage_grace_ms",
                AUDIO_SOURCE_OUTAGE_GRACE_MS_DEFAULT,
            ),
            "live_jitter_target_ms": self._resolve_profile_int_override(
                profile,
                "live_jitter_target_ms",
                "audio_live_jitter_target_ms",
                defaults.live_jitter_target_ms,
            ),
            "transport_heartbeat_window_ms": self._resolve_profile_int_override(
                profile,
                "transport_heartbeat_window_ms",
                "audio_transport_heartbeat_window_ms",
                defaults.transport_heartbeat_window_ms,
            ),
            "delivery_profile": profile,
            "client_queue_bytes": self._get_optional_int_config("audio_client_queue_bytes"),
            "client_overflow_grace_ms": self._get_optional_int_config("audio_client_overflow_grace_ms"),
            "client_max_backlog_ms": self._get_optional_int_config("audio_client_max_backlog_ms"),
            "primary_client_queue_bytes": self._resolve_profile_int_override(
                profile,
                "primary_client_queue_bytes",
                "audio_primary_client_queue_bytes",
                defaults.primary_client_queue_bytes,
            ),
            "primary_client_overflow_grace_ms": self._resolve_profile_int_override(
                profile,
                "primary_client_overflow_grace_ms",
                "audio_primary_client_overflow_grace_ms",
                defaults.primary_client_overflow_grace_ms,
            ),
            "primary_client_max_backlog_ms": self._resolve_profile_int_override(
                profile,
                "primary_client_max_backlog_ms",
                "audio_primary_client_max_backlog_ms",
                defaults.primary_client_max_backlog_ms,
            ),
            "aux_client_queue_bytes": self._get_int_config("audio_aux_client_queue_bytes", AUDIO_AUX_CLIENT_QUEUE_BYTES_DEFAULT),
            "aux_client_overflow_grace_ms": self._get_int_config(
                "audio_aux_client_overflow_grace_ms",
                AUDIO_AUX_CLIENT_OVERFLOW_GRACE_MS_DEFAULT,
            ),
            "aux_client_max_backlog_ms": self._get_int_config(
                "audio_aux_client_max_backlog_ms",
                AUDIO_AUX_CLIENT_MAX_BACKLOG_MS_DEFAULT,
            ),
            "primary_health_require_yield_progress": self._get_bool_config(
                "audio_primary_health_require_yield_progress",
                AUDIO_PRIMARY_HEALTH_REQUIRE_YIELD_PROGRESS_DEFAULT,
            ),
            "debug_capture_enabled": self._get_bool_config(
                "audio_debug_capture_enabled",
                AUDIO_DEBUG_CAPTURE_ENABLED_DEFAULT,
            ),
            "debug_capture_pre_encoder_path": self._get_optional_str_config("audio_debug_capture_pre_encoder_path"),
            "debug_capture_post_encoder_path": self._get_optional_str_config("audio_debug_capture_post_encoder_path"),
            "debug_pacing_logs_enabled": self._get_bool_config(
                "audio_debug_pacing_logs_enabled",
                AUDIO_DEBUG_PACING_LOGS_ENABLED_DEFAULT,
            ),
        }

    def _build_pipeline(self, session: Session) -> StreamPipeline:
        try:
            ffmpeg_path = resolve_ffmpeg_path(self._config_store)
        except RuntimeError as e:
            session.last_error = create_session_error(MEDIA_ENGINE_NOT_FOUND, str(e))
            raise

        session.delivery_profile = self._resolve_delivery_profile_for_source(session.source_id)
        if not session.auto_fell_back_to_stable:
            session.effective_delivery_profile = session.delivery_profile

        return StreamPipeline(
            session.session_id,
            session.stream_profile,
            target_id=session.target_id,
            ffmpeg_path=ffmpeg_path,
            on_error=lambda e: self._handle_session_error(session.session_id, e),
            **self._build_pipeline_kwargs(session.source_id, profile=session.effective_delivery_profile),
        )

    def _get_transport_heartbeat_window_ms(self, session: Session) -> int:
        defaults = self._resolve_delivery_profile_defaults(session.effective_delivery_profile)
        return self._resolve_profile_int_override(
            session.effective_delivery_profile,
            "transport_heartbeat_window_ms",
            "audio_transport_heartbeat_window_ms",
            defaults.transport_heartbeat_window_ms,
        )

    def _get_primary_detach_grace_ms(self, session: Session) -> int:
        defaults = self._resolve_delivery_profile_defaults(session.effective_delivery_profile)
        return self._resolve_profile_int_override(
            session.effective_delivery_profile,
            "primary_detach_grace_ms",
            "audio_primary_detach_grace_ms",
            defaults.primary_detach_grace_ms,
        )

    def _register_pipeline(self, session: Session, pipeline: StreamPipeline, *, swap: bool = False) -> None:
        if not self._stream_publisher:
            return

        if swap and hasattr(self._stream_publisher, "swap_pipeline"):
            self._stream_publisher.swap_pipeline(session.session_id, pipeline)
        else:
            self._stream_publisher.register_pipeline(session.session_id, pipeline)

        if not session.stream_url:
            session.stream_url = self._stream_publisher.get_stream_url(
                session.session_id,
                session.stream_profile,
                subscriber_role="primary_renderer",
                delivery_path_id=session.target_id,
            )
            self._event_bus.emit(
                EventType.PUBLISHER_ACTIVE,
                session_id=session.session_id,
                payload={"stream_url": session.stream_url},
            )

    def _set_media_state(self, session: Session, state: str, reason: str | None = None) -> None:
        session.media_state = state
        session.media_reason = reason

    async def _start_renderer_playback(self, session: Session) -> None:
        session.last_renderer_play_requested_monotonic = time.monotonic()
        session.primary_attach_grace_deadline_monotonic = (
            session.last_renderer_play_requested_monotonic
            + (self._get_int_config("audio_primary_attach_grace_ms", AUDIO_PRIMARY_ATTACH_GRACE_MS_DEFAULT) / 1000)
        )
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

            if not session.media_heal_in_progress:
                self._set_media_state(session, "establishing_primary", None)

            self._event_bus.emit(
                EventType.RENDERER_PLAYBACK_STARTED,
                session_id=session.session_id,
                payload={
                    "target_id": session.target_id,
                    "stream_url": session.stream_url,
                },
            )

    def _derive_encoder_alive(self, diagnostics: dict[str, Any]) -> bool:
        return bool(diagnostics.get("transport_alive"))

    def _derive_delivery_alive(self, diagnostics: dict[str, Any], heartbeat_window_ms: int) -> bool:
        del heartbeat_window_ms
        return bool(diagnostics.get("primary_delivery_alive", False))

    def _primary_attach_grace_open(self, session: Session) -> bool:
        return bool(
            session.primary_attach_grace_deadline_monotonic is not None and time.monotonic() <= session.primary_attach_grace_deadline_monotonic
        )

    def _find_primary_candidate(self, session: Session, diagnostics: dict[str, Any]) -> dict[str, Any] | None:
        for subscriber in diagnostics.get("subscribers", []):
            if not isinstance(subscriber, dict):
                continue
            if subscriber.get("role") == "primary_renderer" and subscriber.get("delivery_path_id") == session.target_id:
                return subscriber
        return None

    def _maybe_establish_primary(self, session: Session, diagnostics: dict[str, Any]) -> bool:
        if session.primary_established:
            return True
        candidate = self._find_primary_candidate(session, diagnostics)
        if not candidate:
            return False
        if not (candidate.get("is_establishing_candidate") or candidate.get("is_primary_healthy")):
            return False
        session.primary_established = True
        session.primary_delivery_path_id = session.target_id
        session.primary_attached_at = candidate.get("attached_monotonic")
        session.primary_remote_addr = candidate.get("remote_addr")
        session.primary_user_agent = candidate.get("user_agent")
        if session.first_primary_attach_ms is None and session.last_renderer_play_requested_monotonic is not None:
            attached_monotonic = candidate.get("attached_monotonic")
            if isinstance(attached_monotonic, (int, float)):
                session.first_primary_attach_ms = (attached_monotonic - session.last_renderer_play_requested_monotonic) * 1000
        logger.info(
            "audio_primary_delivery_path_established session_id=%s target_id=%s delivery_path_id=%s remote_addr=%s user_agent=%s",
            session.session_id,
            session.target_id,
            session.primary_delivery_path_id,
            session.primary_remote_addr,
            session.primary_user_agent,
        )
        return True

    def _current_media_state(self, diagnostics: dict[str, Any]) -> str:
        if int(diagnostics.get("real_frames_written") or 0) > 0:
            return "playing_active"
        if bool(diagnostics.get("keepalive_active")):
            return "playing_idle"
        return "playing_degraded"

    def _schedule_media_plane_heal(self, session_id: str, mode: str, reason: str, *, force: bool = False) -> None:
        session = self.get(session_id)
        if not session or session.media_heal_in_progress:
            return
        if mode == "replay" and not force and not self._get_bool_config("audio_auto_heal_delivery_enabled", AUDIO_AUTO_HEAL_DELIVERY_ENABLED_DEFAULT):
            return
        if session.media_heal_attempts >= 3:
            return

        delay_seconds = [0.0, 0.5, 1.5][session.media_heal_attempts]
        session.media_heal_attempts += 1
        if mode == "replay":
            session.delivery_heal_attempts_total += 1
        session.media_heal_in_progress = True
        session.last_media_heal_at = time.time()
        session.media_epoch += 1
        epoch = session.media_epoch
        self._set_media_state(session, "healing_media_plane", "media_plane_heal_in_progress")
        self._event_bus.emit(
            EventType.SOURCE_STATE_CHANGED,
            session_id=session_id,
            payload={"state": "media_plane_heal_started", "details": {"mode": mode, "reason": reason, "attempt": session.media_heal_attempts}},
        )
        self._session_heals[session_id] = asyncio.create_task(self._heal_media_plane(session_id, mode, reason, delay_seconds, epoch))

    async def _heal_media_plane(self, session_id: str, mode: str, reason: str, delay_seconds: float, epoch: int) -> None:
        if delay_seconds > 0:
            await asyncio.sleep(delay_seconds)

        session = self.get(session_id)
        if not session or session.media_epoch != epoch:
            return

        try:
            health = self._source_registry.probe_source_health(session.source_id)
            if health is not None and not health.healthy and not self._is_windows_source_viable(health):
                self._set_media_state(session, "playing_degraded", "media_plane_heal_failed")
                return

            if mode == "replay":
                await self._start_renderer_playback(session)
            else:
                old_pipeline = session.pipeline
                new_pipeline = self._build_pipeline(session)
                await new_pipeline.start()
                session.media_epoch += 1
                if self._stream_publisher:
                    self._register_pipeline(session, new_pipeline, swap=True)
                frame_sink = self._frame_sinks.get(session_id)
                if frame_sink:
                    frame_sink.set_pipeline(new_pipeline, session.media_epoch)
                session.pipeline = new_pipeline
                if old_pipeline:
                    await old_pipeline.stop()
                await self._start_renderer_playback(session)

            heartbeat_window_ms = self._get_transport_heartbeat_window_ms(session)
            recovery_started_at: float | None = None
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                await asyncio.sleep(0.1)
                session = self.get(session_id)
                if not session or not session.pipeline or session.media_epoch not in {epoch, epoch + 1}:
                    return
                diagnostics = session.pipeline.get_diagnostics_snapshot()
                encoder_alive = self._derive_encoder_alive(diagnostics)
                delivery_alive = self._derive_delivery_alive(diagnostics, heartbeat_window_ms)
                if encoder_alive and (mode != "replay" or delivery_alive):
                    if recovery_started_at is None:
                        recovery_started_at = time.monotonic()
                    if (time.monotonic() - recovery_started_at) * 1000 >= heartbeat_window_ms:
                        try:
                            session.transition_to(SessionState.PLAYING)
                        except ValueError:
                            if session.state != SessionState.PLAYING:
                                raise
                        self._set_media_state(session, self._current_media_state(diagnostics), None)
                        session.last_media_healthy_at = time.time()
                        self._event_bus.emit(
                            EventType.SOURCE_STATE_CHANGED,
                            session_id=session_id,
                            payload={"state": "media_plane_heal_succeeded", "details": {"mode": mode, "reason": reason}},
                        )
                        self._clear_primary_detach_state(session)
                        session.media_heal_attempts = 0
                        return
                else:
                    recovery_started_at = None

            if session.media_epoch in {epoch, epoch + 1}:
                self._set_media_state(session, "playing_degraded", "media_plane_heal_failed")
                self._event_bus.emit(
                    EventType.SOURCE_STATE_CHANGED,
                    session_id=session_id,
                    payload={"state": "media_plane_heal_failed", "details": {"mode": mode, "reason": reason}},
                )
        finally:
            session = self.get(session_id)
            if session and session.media_epoch in {epoch, epoch + 1}:
                session.media_heal_in_progress = False
                self._clear_primary_detach_state(session)
            self._session_heals.pop(session_id, None)

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
            phase_started_at = time.monotonic()
            session.delivery_profile = self._resolve_delivery_profile_for_source(session.source_id)
            session.effective_delivery_profile = session.delivery_profile
            session.auto_fell_back_to_stable = False
            session.fallback_reason = None
            session.recent_primary_detach_times_monotonic.clear()
            self._clear_primary_detach_state(session)
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
            logger.info("Session %s: source prepare completed in %.1fms", session_id, (time.monotonic() - phase_started_at) * 1000)

            # 2. Setup pipeline and publisher
            phase_started_at = time.monotonic()
            try:
                if session.pipeline is None:
                    session.pipeline = self._build_pipeline(session)

                if self._stream_publisher:
                    self._register_pipeline(session, session.pipeline)
            except Exception as e:
                if not session.last_error:
                    session.last_error = create_session_error(PIPELINE_START_FAILED, str(e))
                raise
            logger.info("Session %s: pipeline setup completed in %.1fms", session_id, (time.monotonic() - phase_started_at) * 1000)

            # 3. Start source with frame sink
            phase_started_at = time.monotonic()
            try:
                frame_sink = SessionFrameSink(
                    session.pipeline,
                    on_error=lambda e: self._handle_session_error(session_id, e),
                )
                frame_sink.start()
                self._frame_sinks[session_id] = frame_sink

                source_binding = self._source_registry.resolve_source(session.source_id)
                source_desc = source_binding.source if source_binding else None
                adapter_info = source_binding.adapter_info if source_binding else None

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
                    payload={"adapter_session_id": session.adapter_session_id, "backend": start_res.backend},
                )
            except Exception as e:
                if not session.last_error:
                    session.last_error = create_session_error(SOURCE_START_FAILED, str(e))
                raise
            logger.info("Session %s: source start completed in %.1fms", session_id, (time.monotonic() - phase_started_at) * 1000)

            # 4. Start pipeline
            phase_started_at = time.monotonic()
            try:
                await session.pipeline.start()
            except Exception as e:
                session.last_error = create_session_error(PIPELINE_START_FAILED, str(e))
                raise
            logger.info("Session %s: pipeline start completed in %.1fms", session_id, (time.monotonic() - phase_started_at) * 1000)

            # 5. Verify Windows source activity before touching the renderer
            phase_started_at = time.monotonic()
            source_binding = self._source_registry.resolve_source(session.source_id)
            source_desc = source_binding.source if source_binding else None
            verification_result = "active"
            if source_desc and source_desc.platform == "windows" and source_desc.source_type == SourceType.SYSTEM_OUTPUT:
                verification_result = await self._verify_windows_source_startup(session_id, session)
                session.startup_viability_ms = (time.monotonic() - phase_started_at) * 1000
                logger.info(
                    "Session %s: windows verification gate resolved result=%s in %.1fms",
                    session_id,
                    verification_result,
                    (time.monotonic() - phase_started_at) * 1000,
                )
            logger.info(
                "Session %s: windows source verification completed in %.1fms",
                session_id,
                (time.monotonic() - phase_started_at) * 1000,
            )

            # 6. Prepare and start renderer
            phase_started_at = time.monotonic()
            try:
                await self._start_renderer_playback(session)
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

            logger.info("Session %s: renderer prepare/play completed in %.1fms", session_id, (time.monotonic() - phase_started_at) * 1000)

            session.transition_to(SessionState.PLAYING)
            initial_media_state = "establishing_primary"
            self._set_media_state(session, initial_media_state, None)
            session.last_media_healthy_at = time.time()
            self._event_bus.emit(
                EventType.SESSION_STARTED,
                session_id=session_id,
                payload=session.to_dict(),
            )
            self._start_session_monitor(session_id, source_desc, verification_result if source_desc and source_desc.platform == "windows" and source_desc.source_type == SourceType.SYSTEM_OUTPUT else None)
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

    async def _verify_windows_source_startup(self, session_id: str, session: Session) -> str:
        verification_started_at = time.monotonic()
        observed_buffer_activity = False
        last_health = None
        last_health_details: dict[str, Any] = {}
        allow_silent_source = self._get_bool_config(
            "audio_live_startup_allow_silent_source",
            AUDIO_LIVE_STARTUP_ALLOW_SILENT_SOURCE_DEFAULT,
        )
        viability_timeout_ms = self._get_int_config(
            "audio_live_startup_viability_timeout_ms",
            AUDIO_LIVE_STARTUP_VIABILITY_TIMEOUT_MS_DEFAULT,
        )
        poll_interval_seconds = 0.1
        deadline = verification_started_at + (viability_timeout_ms / 1000)
        while time.monotonic() < deadline:
            jitter_buffer_size_ms = session.pipeline.jitter_buffer.size_ms if session.pipeline else 0.0
            if jitter_buffer_size_ms > 0:
                observed_buffer_activity = True

            health = self._source_registry.probe_source_health(session.source_id)
            last_health = health
            health_details = health.details if health else {}
            last_health_details = health_details

            if health is not None and not health.healthy and not self._is_windows_source_viable(health):
                break

            verification_result, verification_reason = self._classify_windows_verification_state(health)
            if verification_result == "active":
                logger.info(
                    "audio_startup_active session_id=%s source_id=%s elapsed_source_start_ms=%.1f elapsed_session_start_ms=%.1f callback_count=%s frames_emitted=%s jitter_buffer_size_ms=%.1f health=%s reason=%s",
                    session_id,
                    session.source_id,
                    (time.monotonic() - verification_started_at) * 1000,
                    (time.monotonic() - verification_started_at) * 1000,
                    health_details.get("callback_count"),
                    health_details.get("frames_emitted"),
                    jitter_buffer_size_ms,
                    health_details,
                    verification_reason,
                )
                self._log_windows_verification_result(
                    session_id,
                    "active",
                    verification_started_at,
                    health.source_state if health else "active",
                    health_details,
                    jitter_buffer_size_ms,
                    observed_buffer_activity,
                    success_reason=verification_reason,
                )
                return "active"

            if verification_result == "healthy_but_idle":
                source_binding = self._source_registry.resolve_source(session.source_id)
                adapter_info = source_binding.adapter_info if source_binding else None
                adapter_name = adapter_info.adapter.__class__.__name__ if adapter_info and adapter_info.adapter else "Unknown"
                logger.warning(
                    "Session %s: Windows source is healthy but idle. Proceeding anyway. adapter=%s details=%s",
                    session_id,
                    adapter_name,
                    health_details,
                )
                logger.info(
                    "audio_startup_idle session_id=%s source_id=%s elapsed_source_start_ms=%.1f elapsed_session_start_ms=%.1f callback_count=%s frames_emitted=%s jitter_buffer_size_ms=%.1f health=%s reason=%s",
                    session_id,
                    session.source_id,
                    (time.monotonic() - verification_started_at) * 1000,
                    (time.monotonic() - verification_started_at) * 1000,
                    health_details.get("callback_count"),
                    health_details.get("frames_emitted"),
                    jitter_buffer_size_ms,
                    health_details,
                    verification_reason,
                )
                self._log_windows_verification_result(
                    session_id,
                    "healthy_but_idle",
                    verification_started_at,
                    health.source_state if health else "healthy_but_idle",
                    health_details,
                    jitter_buffer_size_ms,
                    observed_buffer_activity,
                    success_reason=verification_reason,
                )
                session.last_error = create_session_error(WINDOWS_OUTPUT_DEVICE_SILENT, details=health_details)
                return "healthy_but_idle"

            if allow_silent_source and self._is_windows_source_viable(health):
                logger.info(
                    "audio_startup_viable_silent session_id=%s source_id=%s elapsed_source_start_ms=%.1f elapsed_session_start_ms=%.1f callback_count=%s frames_emitted=%s jitter_buffer_size_ms=%.1f health=%s",
                    session_id,
                    session.source_id,
                    (time.monotonic() - verification_started_at) * 1000,
                    (time.monotonic() - verification_started_at) * 1000,
                    health_details.get("callback_count"),
                    health_details.get("frames_emitted"),
                    jitter_buffer_size_ms,
                    health_details,
                )
                self._log_windows_verification_result(
                    session_id,
                    "idle_pending_signal",
                    verification_started_at,
                    health.source_state if health else "idle_pending_signal",
                    health_details,
                    jitter_buffer_size_ms,
                    observed_buffer_activity,
                    success_reason="viable_silent_source",
                )
                return "idle_pending_signal"

            await asyncio.sleep(poll_interval_seconds)

        health = last_health
        source_binding = self._source_registry.resolve_source(session.source_id)
        adapter_info = source_binding.adapter_info if source_binding else None
        adapter_name = adapter_info.adapter.__class__.__name__ if adapter_info and adapter_info.adapter else "Unknown"
        health_details = health.details if health else last_health_details
        jitter_buffer_size_ms = session.pipeline.jitter_buffer.size_ms if session.pipeline else 0.0
        if jitter_buffer_size_ms > 0:
            observed_buffer_activity = True

        _, verification_reason = self._classify_windows_verification_state(health)

        logger.error(
            "Session %s: Windows source verification failed. adapter=%s state=%s healthy=%s signal_present=%s details=%s",
            session_id,
            adapter_name,
            health.source_state if health else "unknown",
            health.healthy if health else None,
            health.signal_present if health else None,
            health_details,
        )
        logger.error(
            "audio_startup_failed session_id=%s source_id=%s elapsed_source_start_ms=%.1f elapsed_session_start_ms=%.1f callback_count=%s frames_emitted=%s jitter_buffer_size_ms=%.1f health=%s failure_reason=%s",
            session_id,
            session.source_id,
            (time.monotonic() - verification_started_at) * 1000,
            (time.monotonic() - verification_started_at) * 1000,
            health_details.get("callback_count"),
            health_details.get("frames_emitted"),
            jitter_buffer_size_ms,
            health_details,
            verification_reason,
        )
        self._log_windows_verification_result(
            session_id,
            "stall",
            verification_started_at,
            health.source_state if health else "unknown",
            health_details,
            jitter_buffer_size_ms,
            observed_buffer_activity,
            failure_reason=verification_reason,
        )
        session.last_error = create_session_error(WINDOWS_LOOPBACK_CAPTURE_STALLED, details=health_details)
        raise RuntimeError(session.last_error.message)

    def _is_windows_source_viable(self, health: Any) -> bool:
        """Return true when Windows loopback startup is viable without requiring live signal."""
        if not health:
            return False
        if not health.healthy:
            return False
        details = health.details or {}
        viability = details.get("start_viability")
        if not isinstance(viability, dict):
            return False
        stream_opened = bool(viability.get("stream_opened"))
        stream_started = bool(viability.get("stream_started"))
        fatal_error = bool(health.last_error)
        return stream_opened and stream_started and not fatal_error

    def _clear_primary_detach_state(self, session: Session) -> None:
        session.primary_delivery_miss_started_at = None
        session.primary_delivery_recovery_started_at = None
        session.primary_detach_started_at = None
        session.primary_detach_grace_deadline_monotonic = None

    def _record_primary_detach_event(self, session: Session, now: float) -> None:
        session.primary_detach_events_total += 1
        session.last_primary_detach_at = time.time()
        session.recent_primary_detach_times_monotonic.append(now)
        cutoff = now - 90.0
        while session.recent_primary_detach_times_monotonic and session.recent_primary_detach_times_monotonic[0] < cutoff:
            session.recent_primary_detach_times_monotonic.popleft()

    def _should_fallback_to_stable(self, session: Session, now: float) -> tuple[bool, str | None]:
        if session.effective_delivery_profile != "experimental":
            return (False, None)

        cutoff = now - 90.0
        while session.recent_primary_detach_times_monotonic and session.recent_primary_detach_times_monotonic[0] < cutoff:
            session.recent_primary_detach_times_monotonic.popleft()

        if len(session.recent_primary_detach_times_monotonic) >= 2:
            return (True, "repeated_primary_detach")
        if session.delivery_heal_attempts_total > 1:
            return (True, "repeated_delivery_heal")
        return (False, None)

    def _apply_delivery_profile_fallback(self, session: Session, reason: str) -> None:
        if session.effective_delivery_profile == "stable":
            return
        session.effective_delivery_profile = "stable"
        session.auto_fell_back_to_stable = True
        session.fallback_reason = reason

    def _start_session_monitor(self, session_id: str, source_desc: Any, startup_result: str | None) -> None:
        existing = self._session_monitors.pop(session_id, None)
        if existing:
            existing.cancel()
        self._session_monitors[session_id] = asyncio.create_task(
            self._monitor_media_delivery_session(
                session_id,
                source_desc,
                startup_result if source_desc and source_desc.platform == "windows" and source_desc.source_type == SourceType.SYSTEM_OUTPUT else None,
            )
        )

    async def _monitor_media_delivery_session(self, session_id: str, source_desc: Any, startup_result: str | None) -> None:
        activation_watchdog_ms = WINDOWS_SOURCE_ACTIVITY_WATCHDOG_MS_DEFAULT
        source_outage_grace_ms = self._get_int_config("audio_source_outage_grace_ms", AUDIO_SOURCE_OUTAGE_GRACE_MS_DEFAULT)
        started_at = time.monotonic()
        windows_startup_monitor = bool(source_desc and source_desc.platform == "windows" and source_desc.source_type == SourceType.SYSTEM_OUTPUT)
        activation_seen = startup_result == "active"
        activation_degraded_emitted = False
        transport_degraded_emitted = False
        transport_miss_started_at: float | None = None
        transport_recovery_started_at: float | None = None

        try:
            while True:
                await asyncio.sleep(0.25)
                session = self.get(session_id)
                if not session or session.state in {SessionState.STOPPED, SessionState.STOPPING, SessionState.FAILED}:
                    return
                if not session.pipeline:
                    continue
                transport_heartbeat_window_ms = self._get_transport_heartbeat_window_ms(session)
                primary_detach_grace_ms = self._get_primary_detach_grace_ms(session)

                health = self._source_registry.probe_source_health(session.source_id)
                diagnostics = session.pipeline.get_diagnostics_snapshot()
                callback_count = int((health.details or {}).get("callback_count") or 0) if health else 0
                frames_emitted = int((health.details or {}).get("frames_emitted") or 0) if health else 0
                signal_present = bool(health.signal_present) if health else False
                real_frames_written = int(diagnostics.get("real_frames_written") or 0)
                runtime_mode = diagnostics.get("runtime_mode")
                encoder_alive = self._derive_encoder_alive(diagnostics)
                self._maybe_establish_primary(session, diagnostics)
                delivery_alive = self._derive_delivery_alive(diagnostics, transport_heartbeat_window_ms)
                diagnostics["encoder_alive"] = encoder_alive
                diagnostics["delivery_alive"] = delivery_alive
                if session.state == SessionState.PLAYING and session.media_state == "establishing_primary" and delivery_alive:
                    self._set_media_state(session, self._current_media_state(diagnostics), None)
                    session.last_media_healthy_at = time.time()
                transport_alive = bool(diagnostics.get("transport_alive"))
                effective_client_count = int(diagnostics.get("effective_client_count") or 0)
                primary_client_count = int(diagnostics.get("primary_client_count") or 0)
                primary_effective_client_count = int(diagnostics.get("primary_effective_client_count") or 0)
                encoded_bytes_emitted_total = int(diagnostics.get("encoded_bytes_emitted_total") or 0)
                encoded_bytes_emitted_last_window = int(diagnostics.get("encoded_bytes_emitted_last_window") or 0)
                last_stdout_read_monotonic = diagnostics.get("last_stdout_read_monotonic")
                last_stdin_write_monotonic = diagnostics.get("last_stdin_write_monotonic")
                silence_frames_written = int(diagnostics.get("silence_frames_written") or 0)
                keepalive_active = bool(diagnostics.get("keepalive_active"))
                active_client_count = int(diagnostics.get("active_client_count") or 0)
                first_keepalive_encoded_output_after_session_start_ms = diagnostics.get(
                    "first_keepalive_encoded_output_after_session_start_ms"
                )
                activation_seen = activation_seen or callback_count > 0 or frames_emitted > 0 or signal_present or real_frames_written > 0

                elapsed_ms = (time.monotonic() - started_at) * 1000
                if (
                    windows_startup_monitor
                    and startup_result == "idle_pending_signal"
                    and not activation_seen
                    and elapsed_ms >= activation_watchdog_ms
                    and not activation_degraded_emitted
                ):
                    if session.state == SessionState.PLAYING:
                        session.transition_to(SessionState.DEGRADED)
                    self._set_media_state(session, "playing_degraded", "degraded_no_source_activity_yet")
                    details = {
                        "watchdog_ms": activation_watchdog_ms,
                        "startup_result": startup_result,
                        "callback_count": callback_count,
                        "frames_emitted": frames_emitted,
                        "real_frames_written": real_frames_written,
                        "runtime_mode": runtime_mode,
                        "transport_alive": transport_alive,
                        "encoder_alive": encoder_alive,
                        "delivery_alive": delivery_alive,
                        "encoded_bytes_emitted_total": encoded_bytes_emitted_total,
                        "encoded_bytes_emitted_last_window": encoded_bytes_emitted_last_window,
                        "active_client_count": active_client_count,
                        "effective_client_count": effective_client_count,
                        "primary_client_count": primary_client_count,
                        "primary_effective_client_count": primary_effective_client_count,
                        "health": health.details if health else None,
                    }
                    logger.warning(
                        "audio_source_activity_watchdog_degraded session_id=%s source_id=%s elapsed_ms=%.1f details=%s",
                        session_id,
                        session.source_id,
                        elapsed_ms,
                        details,
                    )
                    self._event_bus.emit(
                        EventType.SOURCE_STATE_CHANGED,
                        session_id=session_id,
                        payload={"state": "degraded_no_source_activity_yet", "details": details},
                    )
                    activation_degraded_emitted = True

                stdout_silence_age_ms: float | None = None
                now = time.monotonic()
                if last_stdout_read_monotonic is not None:
                    stdout_silence_age_ms = (now - last_stdout_read_monotonic) * 1000
                elif elapsed_ms >= transport_heartbeat_window_ms:
                    stdout_silence_age_ms = elapsed_ms

                if not encoder_alive and stdout_silence_age_ms is not None and stdout_silence_age_ms >= transport_heartbeat_window_ms:
                    if transport_miss_started_at is None:
                        transport_miss_started_at = now
                    transport_recovery_started_at = None
                else:
                    transport_miss_started_at = None

                if session.state == SessionState.PLAYING and not session.primary_established and self._primary_attach_grace_open(session):
                    self._set_media_state(session, "establishing_primary", None)

                transport_should_degrade = (
                    session.state == SessionState.PLAYING
                    and transport_miss_started_at is not None
                    and (now - transport_miss_started_at) * 1000 >= transport_heartbeat_window_ms
                    and not encoder_alive
                )
                if transport_should_degrade and not transport_degraded_emitted:
                    session.transition_to(SessionState.DEGRADED)
                    self._set_media_state(session, "playing_degraded", "transport_heartbeat_lost")
                    details = {
                        "reason": "transport_heartbeat_lost",
                        "transport_alive": transport_alive,
                        "encoder_alive": encoder_alive,
                        "delivery_alive": delivery_alive,
                        "transport_heartbeat_window_ms": transport_heartbeat_window_ms,
                        "last_stdout_read_monotonic": last_stdout_read_monotonic,
                        "last_stdin_write_monotonic": last_stdin_write_monotonic,
                        "encoded_bytes_emitted_total": encoded_bytes_emitted_total,
                        "encoded_bytes_emitted_last_window": encoded_bytes_emitted_last_window,
                        "real_frames_written": real_frames_written,
                        "silence_frames_written": silence_frames_written,
                        "runtime_mode": runtime_mode,
                        "active_client_count": active_client_count,
                        "primary_client_count": primary_client_count,
                        "primary_effective_client_count": primary_effective_client_count,
                        "health": health.details if health else None,
                    }
                    logger.warning(
                        "audio_transport_heartbeat_degraded session_id=%s source_id=%s details=%s",
                        session_id,
                        session.source_id,
                        details,
                    )
                    self._event_bus.emit(
                        EventType.SOURCE_STATE_CHANGED,
                        session_id=session_id,
                        payload={"state": "playing_degraded", "details": details},
                    )
                    transport_degraded_emitted = True
                    if session.auto_heal:
                        self._schedule_media_plane_heal(session_id, "swap", "transport_heartbeat_lost")

                if session.primary_established and not self._primary_attach_grace_open(session) and encoder_alive and not delivery_alive:
                    if session.primary_detach_started_at is None:
                        session.primary_detach_started_at = now
                        session.primary_detach_grace_deadline_monotonic = now + (primary_detach_grace_ms / 1000)
                        self._record_primary_detach_event(session, now)
                    session.primary_delivery_recovery_started_at = None
                    should_fallback, fallback_reason = self._should_fallback_to_stable(session, now)
                    if should_fallback and fallback_reason:
                        self._apply_delivery_profile_fallback(session, fallback_reason)
                else:
                    if session.primary_detach_started_at is not None and delivery_alive:
                        session.primary_detach_grace_recoveries_total += 1
                    self._clear_primary_detach_state(session)

                delivery_should_degrade = (
                    session.state == SessionState.PLAYING
                    and encoder_alive
                    and session.primary_established
                    and not self._primary_attach_grace_open(session)
                    and session.primary_detach_grace_deadline_monotonic is not None
                    and now >= session.primary_detach_grace_deadline_monotonic
                )
                if delivery_should_degrade and session.media_reason != "client_detached_while_session_open":
                    session.primary_detach_escalations_total += 1
                    session.transition_to(SessionState.DEGRADED)
                    self._set_media_state(session, "playing_degraded", "client_detached_while_session_open")
                    details = {
                        "reason": "client_detached_while_session_open",
                        "transport_alive": transport_alive,
                        "encoder_alive": encoder_alive,
                        "delivery_alive": delivery_alive,
                        "active_client_count": active_client_count,
                        "effective_client_count": effective_client_count,
                        "primary_client_count": primary_client_count,
                        "primary_effective_client_count": primary_effective_client_count,
                        "last_primary_enqueue_monotonic": diagnostics.get("last_primary_enqueue_monotonic"),
                        "last_primary_dequeue_monotonic": diagnostics.get("last_primary_dequeue_monotonic"),
                        "last_primary_yield_monotonic": diagnostics.get("last_primary_yield_monotonic"),
                        "client_stall_disconnects_total": diagnostics.get("client_stall_disconnects_total"),
                        "encoded_bytes_emitted_last_window": encoded_bytes_emitted_last_window,
                        "max_primary_backlog_ms_observed": diagnostics.get("max_primary_backlog_ms_observed"),
                        "runtime_mode": runtime_mode,
                        "primary_detach_grace_ms": primary_detach_grace_ms,
                    }
                    logger.warning(
                        "audio_primary_delivery_path_lost session_id=%s source_id=%s details=%s",
                        session_id,
                        session.source_id,
                        details,
                    )
                    self._event_bus.emit(
                        EventType.SOURCE_STATE_CHANGED,
                        session_id=session_id,
                        payload={"state": "playing_degraded", "details": details},
                    )
                    if (
                        session.effective_delivery_profile == "experimental"
                        and session.auto_heal
                        and self._get_bool_config("audio_auto_heal_delivery_enabled", AUDIO_AUTO_HEAL_DELIVERY_ENABLED_DEFAULT)
                    ):
                        self._schedule_media_plane_heal(session_id, "replay", "client_detached_while_session_open")

                if session.state == SessionState.DEGRADED and encoder_alive and not session.media_heal_in_progress:
                    if session.media_reason == "client_detached_while_session_open":
                        if delivery_alive:
                            if session.primary_delivery_recovery_started_at is None:
                                session.primary_delivery_recovery_started_at = now
                        else:
                            session.primary_delivery_recovery_started_at = None
                    elif transport_recovery_started_at is None:
                        transport_recovery_started_at = now
                else:
                    if session.media_reason != "client_detached_while_session_open":
                        transport_recovery_started_at = None

                if (
                    session.state == SessionState.DEGRADED
                    and encoder_alive
                    and (
                        (
                            session.media_reason == "client_detached_while_session_open"
                            and session.primary_delivery_recovery_started_at is not None
                            and (now - session.primary_delivery_recovery_started_at) * 1000 >= transport_heartbeat_window_ms
                            and delivery_alive
                        )
                        or (
                            session.media_reason != "client_detached_while_session_open"
                            and transport_recovery_started_at is not None
                            and (now - transport_recovery_started_at) * 1000 >= transport_heartbeat_window_ms
                        )
                    )
                    and (real_frames_written > 0 or keepalive_active)
                ):
                    session.transition_to(SessionState.PLAYING)
                    restored_state = "active" if real_frames_written > 0 else "idle"
                    self._set_media_state(session, "playing_active" if restored_state == "active" else "playing_idle", None)
                    session.last_media_healthy_at = time.time()
                    details = {
                        "transport_alive": transport_alive,
                        "encoder_alive": encoder_alive,
                        "delivery_alive": delivery_alive,
                        "encoded_bytes_emitted_total": encoded_bytes_emitted_total,
                        "encoded_bytes_emitted_last_window": encoded_bytes_emitted_last_window,
                        "callback_count": callback_count,
                        "frames_emitted": frames_emitted,
                        "real_frames_written": real_frames_written,
                        "silence_frames_written": silence_frames_written,
                        "keepalive_to_first_real_frame_ms": diagnostics.get("keepalive_to_first_real_frame_ms"),
                        "first_keepalive_encoded_output_after_session_start_ms": first_keepalive_encoded_output_after_session_start_ms,
                        "first_real_encoded_output_after_session_start_ms": diagnostics.get(
                            "first_real_encoded_output_after_session_start_ms"
                        ),
                        "active_client_count": active_client_count,
                        "first_primary_attach_ms": session.first_primary_attach_ms,
                        "primary_resume_to_first_successful_yield_ms": diagnostics.get("primary_resume_to_first_successful_yield_ms"),
                    }
                    logger.info(
                        "audio_transport_heartbeat_restored session_id=%s source_id=%s details=%s",
                        session_id,
                        session.source_id,
                        details,
                    )
                    self._event_bus.emit(
                        EventType.SOURCE_STATE_CHANGED,
                        session_id=session_id,
                        payload={
                            "state": restored_state,
                            "details": details,
                        },
                    )
                    transport_degraded_emitted = False
                    transport_miss_started_at = None
                    self._clear_primary_detach_state(session)
                    if session.last_media_healthy_at and (time.time() - session.last_media_healthy_at) >= 5:
                        session.media_heal_attempts = 0

                last_real_frame_age_ms = diagnostics.get("last_real_frame_age_ms")
                if (
                    windows_startup_monitor
                    and activation_seen
                    and isinstance(last_real_frame_age_ms, (int, float))
                    and last_real_frame_age_ms >= source_outage_grace_ms
                    and health is not None
                    and not health.healthy
                ):
                    details = {
                        "source_outage_grace_ms": source_outage_grace_ms,
                        "last_real_frame_age_ms": last_real_frame_age_ms,
                        "runtime_mode": runtime_mode,
                        "health": health.details,
                    }
                    session.last_error = create_session_error(
                        SOURCE_START_FAILED,
                        "Windows source outage grace exceeded after startup.",
                        details=details,
                    )
                    logger.error(
                        "audio_pipeline_starvation_warning session_id=%s source_id=%s details=%s",
                        session_id,
                        session.source_id,
                        details,
                    )
                    self._event_bus.emit(
                        EventType.SESSION_FAILED,
                        session_id=session_id,
                        payload=session.to_dict(),
                    )
                    session.transition_to(SessionState.FAILED)
                    await self.stop_session(session_id)
                    return
        except asyncio.CancelledError:
            return

    def _classify_windows_verification_state(self, health: Any) -> tuple[str, str]:
        if not health:
            return ("stall", "missing_health")

        details = health.details or {}
        frames_emitted = int(details.get("frames_emitted") or 0)
        callback_count = int(details.get("callback_count") or 0)

        if not health.healthy and not self._is_windows_source_viable(health):
            return ("stall", "unhealthy_backend")

        if health.healthy and health.source_state == "active":
            return ("active", "backend_active_state")
        if health.healthy and frames_emitted > 0:
            return ("active", "frames_emitted")
        if health.healthy and callback_count > 0 and health.signal_present:
            return ("active", "callbacks_with_signal")

        # The Windows loopback backend may remain callback-active during silence without emitting
        # bridge frames for every callback. Require a healthy stream plus real callback activity,
        # but do not require frames_emitted > 0 for the idle classification.
        if health.healthy and health.source_state == "healthy_but_idle" and not health.signal_present and callback_count > 0:
            return ("healthy_but_idle", "healthy_but_idle")

        if health.source_state in {
            "stream_started_no_callbacks",
            "callbacks_active_no_samples",
            "samples_received_no_frames_emitted",
        }:
            return ("stall", health.source_state)

        return ("stall", health.source_state or "unknown")

    def _log_windows_verification_result(
        self,
        session_id: str,
        verification_result: str,
        verification_started_at: float,
        startup_substate: str,
        details: dict[str, Any],
        jitter_buffer_size_ms: float,
        observed_buffer_activity: bool,
        success_reason: str | None = None,
        failure_reason: str | None = None,
    ) -> None:
        logger.info(
            "Windows verification result: session_id=%s verification_result=%s elapsed_ms=%.1f startup_substate=%s callback_count=%s samples_received=%s frames_emitted=%s jitter_buffer_size_ms=%.1f observed_buffer_activity=%s verification_success_reason=%s verification_failure_reason=%s",
            session_id,
            verification_result,
            (time.monotonic() - verification_started_at) * 1000,
            startup_substate,
            details.get("callback_count"),
            details.get("samples_received"),
            details.get("frames_emitted"),
            jitter_buffer_size_ms,
            observed_buffer_activity,
            success_reason,
            failure_reason,
        )

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

        monitor_task = self._session_monitors.pop(session_id, None)
        if monitor_task:
            monitor_task.cancel()
        heal_task = self._session_heals.pop(session_id, None)
        if heal_task:
            heal_task.cancel()
        self._clear_primary_detach_state(session)

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

        if session.state == SessionState.DEGRADED and session.media_reason in {
            "transport_heartbeat_lost",
            "client_detached_while_session_open",
            "media_plane_heal_failed",
        }:
            mode = "replay" if session.media_reason == "client_detached_while_session_open" else "swap"
            self._schedule_media_plane_heal(session_id, mode, session.media_reason, force=True)
            self._event_bus.emit(EventType.HEAL_ATTEMPTED, session_id=session_id)
            return

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
