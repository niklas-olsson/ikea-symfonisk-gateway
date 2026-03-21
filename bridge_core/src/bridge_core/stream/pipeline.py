"""Audio pipeline implementation with jitter buffer, keepalive, and FFmpeg encoding."""

import asyncio
import heapq
import json
import logging
import time
import wave
from collections import deque
from collections.abc import AsyncGenerator
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from ingress_sdk.protocol import AudioFrame

from bridge_core.stream.profiles import STREAM_PROFILES

logger = logging.getLogger(__name__)
DEFAULT_PCM_SAMPLE_FORMAT = "s16le"
DEFAULT_KEEPALIVE_IDLE_THRESHOLD_MS = 200
DEFAULT_KEEPALIVE_FRAME_DURATION_MS = 20
DEFAULT_SOURCE_OUTAGE_GRACE_MS = 5000
DEFAULT_JITTER_TARGET_MS = 250
DEFAULT_TRANSPORT_HEARTBEAT_WINDOW_MS = 1000
DEFAULT_DELIVERY_PROFILE = "stable"
DEFAULT_PRIMARY_CLIENT_QUEUE_BYTES = 262144
DEFAULT_PRIMARY_CLIENT_OVERFLOW_GRACE_MS = 1500
DEFAULT_PRIMARY_CLIENT_MAX_BACKLOG_MS = 1500
DEFAULT_PRIMARY_CLIENT_QUEUE_BYTES_EXPERIMENTAL = 131072
DEFAULT_PRIMARY_CLIENT_OVERFLOW_GRACE_MS_EXPERIMENTAL = 750
DEFAULT_PRIMARY_CLIENT_MAX_BACKLOG_MS_EXPERIMENTAL = 750
DEFAULT_AUX_CLIENT_QUEUE_BYTES = 131072
DEFAULT_AUX_CLIENT_OVERFLOW_GRACE_MS = 750
DEFAULT_AUX_CLIENT_MAX_BACKLOG_MS = 1500
DEFAULT_CLIENT_QUEUE_BYTES = 131072
DEFAULT_CLIENT_OVERFLOW_GRACE_MS = 750
PRIMARY_BACKLOG_WARNING_THRESHOLD_MS = 750.0
PACING_LOG_INTERVAL_SECONDS = 2.0
STABLE_PRIMARY_HARD_EVICTION_QUEUE_MULTIPLIER = 8
SubscriberRole = Literal["primary_renderer", "auxiliary", "unknown"]


@dataclass(frozen=True)
class PipelineInputFormat:
    """PCM format fed into FFmpeg stdin."""

    sample_rate: int
    channels: int
    sample_format: str
    bytes_per_sample: int
    frame_duration_ms: int

    @property
    def duration_ns(self) -> int:
        return self.frame_duration_ms * 1_000_000

    @property
    def samples_per_channel_per_frame(self) -> int:
        return int(self.sample_rate * self.frame_duration_ms / 1000)

    @property
    def bytes_per_frame(self) -> int:
        return self.samples_per_channel_per_frame * self.channels * self.bytes_per_sample


class JitterBuffer:
    """Buffer for incoming audio frames to handle jitter and reordering."""

    def __init__(self, target_ms: int = 250, sample_rate: int = 48000):
        self.target_ms = target_ms
        self.sample_rate = sample_rate
        self._frames: list[tuple[int, int, AudioFrame]] = []
        self._next_sequence: int | None = None
        self._last_pts: int | None = None
        self._buffer_size_ms = 0.0
        self._counter = 0
        self._lock = asyncio.Lock()
        self._ready = asyncio.Event()

    async def push(self, frame: AudioFrame) -> None:
        """Push a new frame into the jitter buffer."""
        async with self._lock:
            if self._next_sequence is not None and frame.sequence < self._next_sequence:
                logger.warning(f"Dropping late frame: seq={frame.sequence}, expected={self._next_sequence}")
                return

            self._counter += 1
            heapq.heappush(self._frames, (frame.sequence, self._counter, frame))
            self._buffer_size_ms += frame.duration_ns / 1_000_000

            if self._buffer_size_ms >= self.target_ms:
                self._ready.set()

    async def pop(self) -> AudioFrame | None:
        """Pop the next frame from the jitter buffer."""
        async with self._lock:
            if not self._frames:
                self._ready.clear()
                return None

            if not self._ready.is_set() and self._next_sequence is None:
                return None

            seq, _, frame = heapq.heappop(self._frames)
            self._buffer_size_ms -= frame.duration_ns / 1_000_000

            if self._next_sequence is not None and seq > self._next_sequence:
                logger.warning(f"Gap detected in jitter buffer: expected {self._next_sequence}, got {seq}")

            self._next_sequence = seq + 1
            self._last_pts = frame.pts_ns

            if not self._frames:
                self._ready.clear()

            return frame

    @property
    def size_ms(self) -> float:
        """Current size of the jitter buffer in milliseconds."""
        return self._buffer_size_ms


@dataclass
class PipelineSubscriber:
    """Tracks per-subscriber delivery and backlog state."""

    subscriber_id: int
    queue: asyncio.Queue[bytes]
    wake_event: asyncio.Event
    attached_monotonic: float
    role: SubscriberRole
    remote_addr: str | None
    user_agent: str | None
    delivery_path_id: str | None
    last_successful_enqueue_monotonic: float | None
    last_successful_dequeue_monotonic: float | None
    last_successful_yield_monotonic: float | None
    overflow_started_monotonic: float | None
    overflow_events: int
    queued_bytes: int
    closed: bool = False


@dataclass(frozen=True)
class PipelineSubscriberInfo:
    """Metadata captured from the HTTP subscriber request."""

    role: SubscriberRole = "auxiliary"
    remote_addr: str | None = None
    user_agent: str | None = None
    delivery_path_id: str | None = None


@dataclass(frozen=True)
class PipelineClientPolicy:
    """Backpressure policy for a subscriber role."""

    queue_bytes: int
    overflow_grace_ms: int
    max_backlog_ms: int | None


class StreamPipeline:
    """Active audio pipeline for a session using FFmpeg for encoding."""

    def __init__(
        self,
        session_id: str,
        profile_id: str,
        target_id: str | None = None,
        ffmpeg_path: str = "ffmpeg",
        on_error: Any | None = None,
        keepalive_enabled: bool = False,
        keepalive_idle_threshold_ms: int = DEFAULT_KEEPALIVE_IDLE_THRESHOLD_MS,
        source_outage_grace_ms: int = DEFAULT_SOURCE_OUTAGE_GRACE_MS,
        keepalive_frame_duration_ms: int = DEFAULT_KEEPALIVE_FRAME_DURATION_MS,
        live_jitter_target_ms: int = DEFAULT_JITTER_TARGET_MS,
        transport_heartbeat_window_ms: int = DEFAULT_TRANSPORT_HEARTBEAT_WINDOW_MS,
        delivery_profile: Literal["stable", "experimental"] = "stable",
        client_queue_bytes: int | None = None,
        client_overflow_grace_ms: int | None = None,
        client_max_backlog_ms: int | None = None,
        primary_client_queue_bytes: int | None = None,
        primary_client_overflow_grace_ms: int | None = None,
        primary_client_max_backlog_ms: int | None = None,
        aux_client_queue_bytes: int | None = None,
        aux_client_overflow_grace_ms: int | None = None,
        aux_client_max_backlog_ms: int | None = None,
        primary_health_require_yield_progress: bool = True,
        debug_capture_enabled: bool = False,
        debug_capture_pre_encoder_path: str | None = None,
        debug_capture_post_encoder_path: str | None = None,
        debug_pacing_logs_enabled: bool = False,
        input_format: PipelineInputFormat | None = None,
    ):
        self.session_id = session_id
        self.profile_id = profile_id
        self.target_id = target_id
        self.ffmpeg_path = ffmpeg_path
        self.on_error = on_error
        self.profile = STREAM_PROFILES[profile_id]
        self._ffmpeg_input_format = input_format or self._build_default_input_format(keepalive_frame_duration_ms)
        self.jitter_buffer = JitterBuffer(target_ms=live_jitter_target_ms, sample_rate=self._ffmpeg_input_format.sample_rate)
        self._process: asyncio.subprocess.Process | None = None
        self._clients: list[PipelineSubscriber] = []
        self._active = False
        self._lock = asyncio.Lock()
        self._feed_task: asyncio.Task[None] | None = None
        self._read_task: asyncio.Task[None] | None = None

        self._keepalive_enabled = keepalive_enabled
        self._idle_threshold_ms = keepalive_idle_threshold_ms
        self._source_outage_grace_ms = source_outage_grace_ms
        self._frame_duration_ms = self._ffmpeg_input_format.frame_duration_ms
        self._live_jitter_target_ms = live_jitter_target_ms
        self._transport_heartbeat_window_ms = transport_heartbeat_window_ms
        self._delivery_profile = delivery_profile
        self._primary_policy, self._aux_policy = self._resolve_client_policies(
            delivery_profile=delivery_profile,
            client_queue_bytes=client_queue_bytes,
            client_overflow_grace_ms=client_overflow_grace_ms,
            client_max_backlog_ms=client_max_backlog_ms,
            primary_client_queue_bytes=primary_client_queue_bytes,
            primary_client_overflow_grace_ms=primary_client_overflow_grace_ms,
            primary_client_max_backlog_ms=primary_client_max_backlog_ms,
            aux_client_queue_bytes=aux_client_queue_bytes,
            aux_client_overflow_grace_ms=aux_client_overflow_grace_ms,
            aux_client_max_backlog_ms=aux_client_max_backlog_ms,
        )
        self._primary_health_require_yield_progress = primary_health_require_yield_progress
        self._debug_capture_enabled = debug_capture_enabled
        self._debug_capture_pre_encoder_path = debug_capture_pre_encoder_path
        self._debug_capture_post_encoder_path = debug_capture_post_encoder_path
        self._debug_pacing_logs_enabled = debug_pacing_logs_enabled

        self._last_real_frame_monotonic: float | None = None
        self._last_real_frame_duration_ns: int | None = None
        self._last_real_frame_pts_ns: int | None = None
        self._pipeline_started_monotonic: float | None = None
        self._keepalive_active = False
        self._source_outage_active = False
        self._silence_frames_written = 0
        self._real_frames_written = 0
        self._keepalive_activation_count = 0
        self._source_outage_activation_count = 0
        self._encoder_write_count = 0
        self._last_encoder_write_monotonic: float | None = None
        self._last_encoder_write_was_silence = False
        self._last_stdin_write_monotonic: float | None = None
        self._last_stdout_read_monotonic: float | None = None
        self._last_client_fanout_monotonic: float | None = None
        self._last_client_attach_monotonic: float | None = None
        self._last_client_detach_monotonic: float | None = None
        self._last_client_overflow_monotonic: float | None = None
        self._last_client_stall_disconnect_monotonic: float | None = None
        self._ffmpeg_stdin_write_errors = 0
        self._jitter_buffer_underrun_count = 0
        self._last_pacing_log_monotonic: float | None = None
        self._runtime_mode: str = "active"
        self._silence_bytes = b"\x00" * self._ffmpeg_input_format.bytes_per_frame
        self._keepalive_started_monotonic: float | None = None
        self._keepalive_to_first_real_frame_ms: float | None = None
        self._encoded_bytes_emitted_total = 0
        self._stdout_window_samples: deque[tuple[float, int]] = deque()
        self._first_encoded_output_monotonic: float | None = None
        self._first_real_encoded_output_monotonic: float | None = None
        self._first_keepalive_encoded_output_monotonic: float | None = None
        self._awaiting_real_encoded_output = False
        self._awaiting_keepalive_encoded_output = False
        self._backpressure_events_total = 0
        self._client_queue_overflow_events_total = 0
        self._client_stall_disconnects_total = 0
        self._next_subscriber_id = 0
        self._first_primary_attach_monotonic: float | None = None
        self._last_primary_attach_monotonic: float | None = None
        self._last_primary_enqueue_monotonic: float | None = None
        self._last_primary_dequeue_monotonic: float | None = None
        self._last_primary_yield_monotonic: float | None = None
        self._max_primary_backlog_ms_observed = 0.0
        self._primary_backlog_warning_emitted = False
        self._awaiting_primary_resume_yield_since_monotonic: float | None = None
        self._primary_resume_to_first_successful_yield_ms: float | None = None

        self._pre_encoder_debug_writer: Any | None = None
        self._pre_encoder_debug_mode: str | None = None
        self._post_encoder_debug_writer: Any | None = None
        self._pre_encoder_sidecar_path: Path | None = None

    def _build_default_input_format(self, frame_duration_ms: int) -> PipelineInputFormat:
        return PipelineInputFormat(
            sample_rate=self.profile.sample_rate,
            channels=self.profile.channels,
            sample_format=DEFAULT_PCM_SAMPLE_FORMAT,
            bytes_per_sample=_bytes_per_sample_for_format(DEFAULT_PCM_SAMPLE_FORMAT),
            frame_duration_ms=frame_duration_ms,
        )

    def _resolve_client_policies(
        self,
        *,
        delivery_profile: Literal["stable", "experimental"],
        client_queue_bytes: int | None,
        client_overflow_grace_ms: int | None,
        client_max_backlog_ms: int | None,
        primary_client_queue_bytes: int | None,
        primary_client_overflow_grace_ms: int | None,
        primary_client_max_backlog_ms: int | None,
        aux_client_queue_bytes: int | None,
        aux_client_overflow_grace_ms: int | None,
        aux_client_max_backlog_ms: int | None,
    ) -> tuple[PipelineClientPolicy, PipelineClientPolicy]:
        if delivery_profile == "experimental":
            primary_defaults = PipelineClientPolicy(
                queue_bytes=DEFAULT_PRIMARY_CLIENT_QUEUE_BYTES_EXPERIMENTAL,
                overflow_grace_ms=DEFAULT_PRIMARY_CLIENT_OVERFLOW_GRACE_MS_EXPERIMENTAL,
                max_backlog_ms=DEFAULT_PRIMARY_CLIENT_MAX_BACKLOG_MS_EXPERIMENTAL,
            )
        else:
            primary_defaults = PipelineClientPolicy(
                queue_bytes=DEFAULT_PRIMARY_CLIENT_QUEUE_BYTES,
                overflow_grace_ms=DEFAULT_PRIMARY_CLIENT_OVERFLOW_GRACE_MS,
                max_backlog_ms=DEFAULT_PRIMARY_CLIENT_MAX_BACKLOG_MS,
            )
        aux_defaults = PipelineClientPolicy(
            queue_bytes=DEFAULT_AUX_CLIENT_QUEUE_BYTES,
            overflow_grace_ms=DEFAULT_AUX_CLIENT_OVERFLOW_GRACE_MS,
            max_backlog_ms=DEFAULT_AUX_CLIENT_MAX_BACKLOG_MS,
        )

        # Temporary compatibility for older config keys.
        if client_queue_bytes is not None:
            primary_defaults = PipelineClientPolicy(
                queue_bytes=client_queue_bytes,
                overflow_grace_ms=client_overflow_grace_ms or DEFAULT_CLIENT_OVERFLOW_GRACE_MS,
                max_backlog_ms=client_max_backlog_ms,
            )
            aux_defaults = PipelineClientPolicy(
                queue_bytes=client_queue_bytes,
                overflow_grace_ms=client_overflow_grace_ms or DEFAULT_CLIENT_OVERFLOW_GRACE_MS,
                max_backlog_ms=client_max_backlog_ms,
            )

        primary_policy = PipelineClientPolicy(
            queue_bytes=primary_client_queue_bytes or primary_defaults.queue_bytes,
            overflow_grace_ms=primary_client_overflow_grace_ms or primary_defaults.overflow_grace_ms,
            max_backlog_ms=primary_client_max_backlog_ms if primary_client_max_backlog_ms is not None else primary_defaults.max_backlog_ms,
        )
        aux_policy = PipelineClientPolicy(
            queue_bytes=aux_client_queue_bytes or aux_defaults.queue_bytes,
            overflow_grace_ms=aux_client_overflow_grace_ms or aux_defaults.overflow_grace_ms,
            max_backlog_ms=aux_client_max_backlog_ms if aux_client_max_backlog_ms is not None else aux_defaults.max_backlog_ms,
        )
        return primary_policy, aux_policy

    async def start(self) -> None:
        """Start the pipeline and FFmpeg subprocess."""
        if self._active:
            return

        self._pipeline_started_monotonic = time.monotonic()
        self._open_debug_captures()

        cmd = self._get_ffmpeg_cmd()
        self._log_format_audit()
        logger.info(f"Starting FFmpeg for session {self.session_id}: {' '.join(cmd)}")

        try:
            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except Exception:
            self._close_debug_captures()
            raise

        self._active = True
        self._feed_task = asyncio.create_task(self._feed_ffmpeg())
        self._read_task = asyncio.create_task(self._read_ffmpeg())
        self._feed_task.add_done_callback(self._handle_task_done)
        self._read_task.add_done_callback(self._handle_task_done)

    def _handle_task_done(self, task: asyncio.Task[None]) -> None:
        """Handle completion of background tasks and report errors."""
        if not task.cancelled() and task.exception():
            exc = task.exception()
            logger.error(f"Pipeline task for {self.session_id} failed: {exc}", exc_info=exc)
            if self.on_error:
                self.on_error(exc)

    async def stop(self) -> None:
        """Stop the pipeline and FFmpeg subprocess."""
        self._active = False
        await self._close_all_subscribers(reason="pipeline_stopped")
        await self._cancel_and_drain(self._feed_task)
        await self._cancel_and_drain(self._read_task)
        self._feed_task = None
        self._read_task = None

        if self._process:
            try:
                self._process.terminate()
                await self._process.wait()
            except Exception:
                pass
            self._process = None

        self._close_debug_captures()

    async def _cancel_and_drain(self, task: asyncio.Task[Any] | None) -> None:
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _close_all_subscribers(self, reason: str) -> None:
        async with self._lock:
            subscribers = list(self._clients)
        for subscriber in subscribers:
            await self._remove_subscriber(subscriber, reason=reason)

    def _get_ffmpeg_cmd(self) -> list[str]:
        """Generate the FFmpeg command based on the profile."""
        args = [
            self.ffmpeg_path,
            "-f",
            self._ffmpeg_input_format.sample_format,
            "-ar",
            str(self._ffmpeg_input_format.sample_rate),
            "-ac",
            str(self._ffmpeg_input_format.channels),
            "-i",
            "pipe:0",
        ]

        if self.profile.codec == "mp3":
            args += ["-c:a", "libmp3lame", "-b:a", f"{self.profile.bitrate_kbps}k", "-f", "mp3"]
        elif self.profile.codec == "aac":
            args += ["-c:a", "aac", "-b:a", f"{self.profile.bitrate_kbps}k", "-f", "adts"]
        elif self.profile.codec == "pcm_s16le":
            args += ["-c:a", "pcm_s16le", "-f", "wav"]
        else:
            raise ValueError(f"Unsupported codec: {self.profile.codec}")

        args += ["pipe:1"]
        return args

    async def push_frame(self, frame: AudioFrame) -> None:
        """Push an incoming audio frame to the jitter buffer."""
        await self.jitter_buffer.push(frame)
        self._last_real_frame_monotonic = time.monotonic()
        self._last_real_frame_pts_ns = frame.pts_ns
        self._last_real_frame_duration_ns = frame.duration_ns

    async def _feed_ffmpeg(self) -> None:
        """Continuously feed audio data from jitter buffer to FFmpeg's stdin."""
        if self._is_stable_profile():
            await self._feed_ffmpeg_stable()
            return

        await self._feed_ffmpeg_experimental()

    async def _feed_ffmpeg_stable(self) -> None:
        """Legacy conservative feed loop: write frames as they arrive."""
        while self._active:
            try:
                frame = await self.jitter_buffer.pop()
                now = time.monotonic()
                if frame is not None:
                    await self._write_encoder_input(frame.audio_data, is_silence=False, now=now)
                    self._set_runtime_mode(None, now)
                    continue

                keepalive_payload, keepalive_mode = self._resolve_keepalive_payload(now)
                if keepalive_payload is not None:
                    await self._write_encoder_input(keepalive_payload, is_silence=True, now=now)
                    self._set_runtime_mode(keepalive_mode, now)
                    await asyncio.sleep(self._frame_duration_ms / 1000)
                else:
                    await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error feeding FFmpeg for session %s", self.session_id)
                raise

    async def _feed_ffmpeg_experimental(self) -> None:
        """Paced low-latency feed loop used for the experimental profile."""
        slot_seconds = self._frame_duration_ms / 1000
        next_slot_at = time.monotonic()

        while self._active:
            try:
                now = time.monotonic()
                sleep_duration = next_slot_at - now
                if sleep_duration > 0:
                    await asyncio.sleep(sleep_duration)
                    now = time.monotonic()

                frame = await self.jitter_buffer.pop()
                if frame is not None:
                    await self._write_encoder_input(frame.audio_data, is_silence=False, now=now)
                    self._set_runtime_mode(None, now)
                else:
                    self._jitter_buffer_underrun_count += 1
                    keepalive_payload, keepalive_mode = self._resolve_keepalive_payload(now)
                    if keepalive_payload is not None:
                        await self._write_encoder_input(keepalive_payload, is_silence=True, now=now)
                        self._set_runtime_mode(keepalive_mode, now)

                self._maybe_log_pacing_snapshot(now)
                next_slot_at += slot_seconds
                if next_slot_at < now:
                    next_slot_at = now + slot_seconds
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error feeding FFmpeg for session %s", self.session_id)
                raise

    async def _read_ffmpeg(self) -> None:
        """Continuously read encoded data from FFmpeg's stdout and fan out to clients."""
        while self._active:
            try:
                if self._process and self._process.stdout:
                    data = await self._process.stdout.read(4096)
                    if not data:
                        if self._active:
                            raise RuntimeError(f"FFmpeg stdout closed unexpectedly for session {self.session_id}")
                        break

                    now = time.monotonic()
                    self._last_stdout_read_monotonic = now
                    self._encoded_bytes_emitted_total += len(data)
                    self._stdout_window_samples.append((now, len(data)))
                    self._evict_old_stdout_window_samples(now)
                    if self._first_encoded_output_monotonic is None:
                        self._first_encoded_output_monotonic = now
                    if self._awaiting_real_encoded_output and self._first_real_encoded_output_monotonic is None:
                        self._first_real_encoded_output_monotonic = now
                        self._awaiting_real_encoded_output = False
                    if self._awaiting_keepalive_encoded_output and self._first_keepalive_encoded_output_monotonic is None:
                        self._first_keepalive_encoded_output_monotonic = now
                        self._awaiting_keepalive_encoded_output = False

                    self._write_post_encoder_debug(data)
                    await self._fan_out_encoded_chunk(data, now)
                else:
                    await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error reading from FFmpeg for session %s", self.session_id)
                raise

    async def subscribe(self, subscriber_info: PipelineSubscriberInfo | None = None) -> AsyncGenerator[bytes, None]:
        """Subscribe to encoded audio data. Yields chunks of encoded data."""
        info = subscriber_info or PipelineSubscriberInfo()
        now = time.monotonic()
        self._next_subscriber_id += 1
        subscriber = PipelineSubscriber(
            subscriber_id=self._next_subscriber_id,
            queue=asyncio.Queue(),
            wake_event=asyncio.Event(),
            attached_monotonic=now,
            role=info.role if info.role in {"primary_renderer", "auxiliary", "unknown"} else "unknown",
            remote_addr=info.remote_addr,
            user_agent=info.user_agent,
            delivery_path_id=info.delivery_path_id,
            last_successful_enqueue_monotonic=None,
            last_successful_dequeue_monotonic=None,
            last_successful_yield_monotonic=None,
            overflow_started_monotonic=None,
            overflow_events=0,
            queued_bytes=0,
        )
        async with self._lock:
            self._clients.append(subscriber)
            self._last_client_attach_monotonic = subscriber.attached_monotonic
            if self._is_primary_candidate(subscriber):
                self._last_primary_attach_monotonic = subscriber.attached_monotonic
                if self._first_primary_attach_monotonic is None:
                    self._first_primary_attach_monotonic = subscriber.attached_monotonic
        logger.info(
            "audio_subscriber_attached session_id=%s subscriber_id=%s role=%s delivery_path_id=%s remote_addr=%s user_agent=%s",
            self.session_id,
            subscriber.subscriber_id,
            subscriber.role,
            subscriber.delivery_path_id,
            subscriber.remote_addr,
            subscriber.user_agent,
        )

        try:
            while True:
                if subscriber.closed and subscriber.queue.empty():
                    break
                queue_get = asyncio.create_task(subscriber.queue.get())
                wake_wait = asyncio.create_task(subscriber.wake_event.wait())
                try:
                    done, pending = await asyncio.wait({queue_get, wake_wait}, return_when=asyncio.FIRST_COMPLETED)
                    for pending_task in pending:
                        pending_task.cancel()
                    for pending_task in pending:
                        with suppress(asyncio.CancelledError):
                            await pending_task
                    if wake_wait in done:
                        subscriber.wake_event.clear()
                        if subscriber.closed and subscriber.queue.empty():
                            if not queue_get.done():
                                queue_get.cancel()
                                with suppress(asyncio.CancelledError):
                                    await queue_get
                            break
                    if queue_get not in done:
                        continue
                    data = queue_get.result()
                finally:
                    if not queue_get.done():
                        queue_get.cancel()
                        with suppress(asyncio.CancelledError):
                            await queue_get
                    if not wake_wait.done():
                        wake_wait.cancel()
                        with suppress(asyncio.CancelledError):
                            await wake_wait
                dequeue_now = time.monotonic()
                async with self._lock:
                    subscriber.queued_bytes = max(0, subscriber.queued_bytes - len(data))
                    subscriber.last_successful_dequeue_monotonic = dequeue_now
                    if self._is_primary_candidate(subscriber):
                        self._last_primary_dequeue_monotonic = dequeue_now
                yield data
                yield_now = time.monotonic()
                async with self._lock:
                    subscriber.last_successful_yield_monotonic = yield_now
                    if self._is_primary_candidate(subscriber):
                        self._last_primary_yield_monotonic = yield_now
                        if self._awaiting_primary_resume_yield_since_monotonic is not None:
                            self._primary_resume_to_first_successful_yield_ms = (
                                yield_now - self._awaiting_primary_resume_yield_since_monotonic
                            ) * 1000
                            self._awaiting_primary_resume_yield_since_monotonic = None
        finally:
            await self._remove_subscriber(subscriber, reason="subscriber_closed")

    def get_diagnostics_snapshot(self) -> dict[str, Any]:
        """Return a diagnostic snapshot for logging and tests."""
        now = time.monotonic()
        encoded_bytes_emitted_last_window = self._encoded_bytes_emitted_last_window(now)
        effective_client_count = self._effective_client_count(now)
        primary_client_count = self._primary_client_count(now)
        primary_effective_client_count = self._primary_effective_client_count(now)
        primary_delivery_alive = self._primary_delivery_alive(now)
        return {
            "keepalive_active": self._keepalive_active,
            "source_outage_active": self._source_outage_active,
            "silence_frames_written": self._silence_frames_written,
            "real_frames_written": self._real_frames_written,
            "encoder_write_count": self._encoder_write_count,
            "jitter_buffer_size_ms": self.jitter_buffer.size_ms,
            "last_real_frame_age_ms": self._get_real_frame_age_ms(time.monotonic()),
            "last_encoder_write_was_silence": self._last_encoder_write_was_silence,
            "consecutive_idle_duration_ms": self._get_idle_age_ms(time.monotonic()),
            "consecutive_outage_duration_ms": self._get_outage_age_ms(time.monotonic()),
            "ffmpeg_stdin_write_errors": self._ffmpeg_stdin_write_errors,
            "jitter_buffer_underrun_count": self._jitter_buffer_underrun_count,
            "runtime_mode": self._runtime_mode,
            "keepalive_to_first_real_frame_ms": self._keepalive_to_first_real_frame_ms,
            "live_jitter_target_ms": self._live_jitter_target_ms,
            "transport_heartbeat_window_ms": self._transport_heartbeat_window_ms,
            "last_stdin_write_monotonic": self._last_stdin_write_monotonic,
            "last_stdout_read_monotonic": self._last_stdout_read_monotonic,
            "last_client_fanout_monotonic": self._last_client_fanout_monotonic,
            "last_client_attach_monotonic": self._last_client_attach_monotonic,
            "last_client_detach_monotonic": self._last_client_detach_monotonic,
            "last_client_overflow_monotonic": self._last_client_overflow_monotonic,
            "last_client_stall_disconnect_monotonic": self._last_client_stall_disconnect_monotonic,
            "active_client_count": len(self._clients),
            "effective_client_count": effective_client_count,
            "primary_client_count": primary_client_count,
            "primary_effective_client_count": primary_effective_client_count,
            "primary_delivery_alive": primary_delivery_alive,
            "primary_delivery_health_model": "attached" if self._is_stable_profile() else "strict",
            "first_primary_attach_after_session_start_ms": self._elapsed_since_start_ms(self._first_primary_attach_monotonic),
            "last_primary_attach_monotonic": self._last_primary_attach_monotonic,
            "last_primary_enqueue_monotonic": self._last_primary_enqueue_monotonic,
            "last_primary_dequeue_monotonic": self._last_primary_dequeue_monotonic,
            "last_primary_yield_monotonic": self._last_primary_yield_monotonic,
            "primary_resume_to_first_successful_yield_ms": self._primary_resume_to_first_successful_yield_ms,
            "max_primary_backlog_ms_observed": self._max_primary_backlog_ms_observed,
            "delivery_profile": self._delivery_profile,
            "running_delivery_profile": self._delivery_profile,
            "encoded_bytes_emitted_total": self._encoded_bytes_emitted_total,
            "encoded_bytes_emitted_last_window": encoded_bytes_emitted_last_window,
            "backpressure_events_total": self._backpressure_events_total,
            "client_queue_overflow_events_total": self._client_queue_overflow_events_total,
            "client_stall_disconnects_total": self._client_stall_disconnects_total,
            "transport_alive": self._is_transport_alive(now, encoded_bytes_emitted_last_window),
            "subscribers": [self._subscriber_snapshot(subscriber, now) for subscriber in self._clients],
            "first_encoded_output_after_session_start_ms": self._elapsed_since_start_ms(self._first_encoded_output_monotonic),
            "first_real_encoded_output_after_session_start_ms": self._elapsed_since_start_ms(self._first_real_encoded_output_monotonic),
            "first_keepalive_encoded_output_after_session_start_ms": self._elapsed_since_start_ms(
                self._first_keepalive_encoded_output_monotonic
            ),
            "ffmpeg_input_format": {
                **asdict(self._ffmpeg_input_format),
                "samples_per_channel_per_frame": self._ffmpeg_input_format.samples_per_channel_per_frame,
                "bytes_per_frame": self._ffmpeg_input_format.bytes_per_frame,
                "duration_ns": self._ffmpeg_input_format.duration_ns,
            },
        }

    async def _fan_out_encoded_chunk(self, data: bytes, now: float) -> None:
        delivered = False
        to_evict: list[PipelineSubscriber] = []
        async with self._lock:
            for subscriber in list(self._clients):
                if subscriber.closed:
                    continue
                backlog_ms = self._estimate_backlog_ms(subscriber.queued_bytes + len(data), now)
                if self._is_primary_candidate(subscriber) and isinstance(backlog_ms, (int, float)):
                    self._max_primary_backlog_ms_observed = max(self._max_primary_backlog_ms_observed, backlog_ms)
                    if backlog_ms > PRIMARY_BACKLOG_WARNING_THRESHOLD_MS and not self._primary_backlog_warning_emitted:
                        logger.warning(
                            "audio_primary_backlog_warning session_id=%s subscriber_id=%s backlog_ms=%.1f queued_bytes=%s",
                            self.session_id,
                            subscriber.subscriber_id,
                            backlog_ms,
                            subscriber.queued_bytes,
                        )
                        self._primary_backlog_warning_emitted = True

                if self._subscriber_is_overflowing(subscriber, len(data), now):
                    self._backpressure_events_total += 1
                    self._client_queue_overflow_events_total += 1
                    self._last_client_overflow_monotonic = now
                    subscriber.overflow_events += 1
                    if subscriber.overflow_started_monotonic is None:
                        subscriber.overflow_started_monotonic = now
                        logger.info(
                            "audio_subscriber_backpressure_started session_id=%s subscriber_id=%s role=%s delivery_path_id=%s queued_bytes=%s estimated_backlog_ms=%s",
                            self.session_id,
                            subscriber.subscriber_id,
                            subscriber.role,
                            subscriber.delivery_path_id,
                            subscriber.queued_bytes,
                            backlog_ms,
                        )
                    if self._should_evict_subscriber(subscriber, now, len(data)):
                        to_evict.append(subscriber)
                        continue
                else:
                    if subscriber.overflow_started_monotonic is not None:
                        logger.info(
                            "audio_subscriber_backpressure_cleared session_id=%s subscriber_id=%s role=%s delivery_path_id=%s queued_bytes=%s estimated_backlog_ms=%s",
                            self.session_id,
                            subscriber.subscriber_id,
                            subscriber.role,
                            subscriber.delivery_path_id,
                            subscriber.queued_bytes,
                            backlog_ms,
                        )
                    subscriber.overflow_started_monotonic = None

                subscriber.queue.put_nowait(data)
                subscriber.queued_bytes += len(data)
                subscriber.last_successful_enqueue_monotonic = now
                if self._is_primary_candidate(subscriber):
                    self._last_primary_enqueue_monotonic = now
                subscriber.wake_event.set()
                delivered = True

            if delivered:
                self._last_client_fanout_monotonic = now

        for subscriber in to_evict:
            await self._evict_subscriber(subscriber, now)

    def _subscriber_is_overflowing(self, subscriber: PipelineSubscriber, incoming_bytes: int, now: float) -> bool:
        policy = self._policy_for_subscriber(subscriber)
        next_queued_bytes = subscriber.queued_bytes + incoming_bytes
        if next_queued_bytes > policy.queue_bytes:
            return True
        if policy.max_backlog_ms is None:
            return False
        backlog_ms = self._estimate_backlog_ms(next_queued_bytes, now)
        return backlog_ms is not None and backlog_ms > policy.max_backlog_ms

    def _estimate_backlog_ms(self, queued_bytes: int, now: float) -> float | None:
        encoded_bytes_emitted_last_window = self._encoded_bytes_emitted_last_window(now)
        if encoded_bytes_emitted_last_window <= 0:
            return None
        bytes_per_ms = encoded_bytes_emitted_last_window / max(self._transport_heartbeat_window_ms, 1)
        if bytes_per_ms <= 0:
            return None
        return queued_bytes / bytes_per_ms

    def _effective_client_count(self, now: float) -> int:
        heartbeat_window_seconds = self._transport_heartbeat_window_ms / 1000
        count = 0
        for subscriber in self._clients:
            if subscriber.closed:
                continue
            if subscriber.last_successful_enqueue_monotonic is None:
                continue
            if (now - subscriber.last_successful_enqueue_monotonic) <= heartbeat_window_seconds:
                count += 1
        return count

    def _primary_client_count(self, now: float) -> int:
        del now
        return sum(1 for subscriber in self._clients if self._is_primary_candidate(subscriber) and not subscriber.closed)

    def _primary_effective_client_count(self, now: float) -> int:
        return sum(1 for subscriber in self._clients if self._subscriber_primary_healthy(subscriber, now))

    def _primary_delivery_alive(self, now: float) -> bool:
        if self._is_stable_profile():
            return self._primary_client_count(now) > 0
        return self._primary_effective_client_count(now) > 0

    def _is_stable_profile(self) -> bool:
        return self._delivery_profile == "stable"

    def _is_experimental_profile(self) -> bool:
        return self._delivery_profile == "experimental"

    def _policy_for_subscriber(self, subscriber: PipelineSubscriber) -> PipelineClientPolicy:
        if subscriber.role == "primary_renderer":
            return self._primary_policy
        return self._aux_policy

    def _is_primary_candidate(self, subscriber: PipelineSubscriber) -> bool:
        return (
            not subscriber.closed
            and subscriber.role == "primary_renderer"
            and self.target_id is not None
            and subscriber.delivery_path_id == self.target_id
        )

    def _subscriber_primary_healthy(self, subscriber: PipelineSubscriber, now: float) -> bool:
        if not self._is_primary_candidate(subscriber):
            return False
        if self._is_stable_profile():
            return True
        if subscriber.last_successful_enqueue_monotonic is None:
            return False
        heartbeat_window_seconds = self._transport_heartbeat_window_ms / 1000
        if (now - subscriber.last_successful_enqueue_monotonic) > heartbeat_window_seconds:
            return False
        if subscriber.last_successful_dequeue_monotonic is None and subscriber.last_successful_yield_monotonic is None:
            return False
        latest_progress = max(
            [
                marker
                for marker in (subscriber.last_successful_dequeue_monotonic, subscriber.last_successful_yield_monotonic)
                if marker is not None
            ],
            default=None,
        )
        if latest_progress is None or (now - latest_progress) > heartbeat_window_seconds:
            return False
        if self._primary_health_require_yield_progress:
            if (
                subscriber.last_successful_yield_monotonic is None
                or (now - subscriber.last_successful_yield_monotonic) > heartbeat_window_seconds
            ):
                return False
        if self._subscriber_is_past_overflow_grace(subscriber, now):
            return False
        backlog_ms = self._estimate_backlog_ms(subscriber.queued_bytes, now)
        policy = self._policy_for_subscriber(subscriber)
        if subscriber.queued_bytes > policy.queue_bytes:
            return False
        return backlog_ms is None or policy.max_backlog_ms is None or backlog_ms <= policy.max_backlog_ms

    def _subscriber_is_establishing_candidate(self, subscriber: PipelineSubscriber, now: float) -> bool:
        if not self._is_primary_candidate(subscriber):
            return False
        if subscriber.last_successful_enqueue_monotonic is None:
            return False
        heartbeat_window_seconds = self._transport_heartbeat_window_ms / 1000
        if (now - subscriber.attached_monotonic) > heartbeat_window_seconds:
            return False
        if (now - subscriber.last_successful_enqueue_monotonic) > heartbeat_window_seconds:
            return False
        return not self._subscriber_is_past_overflow_grace(subscriber, now)

    def _subscriber_is_past_overflow_grace(self, subscriber: PipelineSubscriber, now: float) -> bool:
        if subscriber.overflow_started_monotonic is None:
            return False
        return (now - subscriber.overflow_started_monotonic) * 1000 >= self._policy_for_subscriber(subscriber).overflow_grace_ms

    def _subscriber_snapshot(self, subscriber: PipelineSubscriber, now: float) -> dict[str, Any]:
        return {
            "subscriber_id": subscriber.subscriber_id,
            "role": subscriber.role,
            "delivery_path_id": subscriber.delivery_path_id,
            "remote_addr": subscriber.remote_addr,
            "user_agent": subscriber.user_agent,
            "attached_monotonic": subscriber.attached_monotonic,
            "last_successful_enqueue_monotonic": subscriber.last_successful_enqueue_monotonic,
            "last_successful_dequeue_monotonic": subscriber.last_successful_dequeue_monotonic,
            "last_successful_yield_monotonic": subscriber.last_successful_yield_monotonic,
            "overflow_started_monotonic": subscriber.overflow_started_monotonic,
            "overflow_events": subscriber.overflow_events,
            "queued_bytes": subscriber.queued_bytes,
            "estimated_backlog_ms": self._estimate_backlog_ms(subscriber.queued_bytes, now),
            "closed": subscriber.closed,
            "is_primary_candidate": self._is_primary_candidate(subscriber),
            "is_establishing_candidate": self._subscriber_is_establishing_candidate(subscriber, now),
            "is_primary_healthy": self._subscriber_primary_healthy(subscriber, now),
        }

    def _should_evict_subscriber(self, subscriber: PipelineSubscriber, now: float, incoming_bytes: int) -> bool:
        if subscriber.overflow_started_monotonic is None:
            return False
        if self._is_stable_profile() and self._is_primary_candidate(subscriber):
            return (subscriber.queued_bytes + incoming_bytes) >= self._stable_primary_hard_queue_bytes(subscriber)
        return (now - subscriber.overflow_started_monotonic) * 1000 >= self._policy_for_subscriber(subscriber).overflow_grace_ms

    def _stable_primary_hard_queue_bytes(self, subscriber: PipelineSubscriber) -> int:
        return self._policy_for_subscriber(subscriber).queue_bytes * STABLE_PRIMARY_HARD_EVICTION_QUEUE_MULTIPLIER

    async def _evict_subscriber(self, subscriber: PipelineSubscriber, now: float) -> None:
        async with self._lock:
            if subscriber.closed:
                return
            subscriber.closed = True
            while not subscriber.queue.empty():
                try:
                    subscriber.queue.get_nowait()
                    subscriber.queue.task_done()
                except asyncio.QueueEmpty:
                    break
            subscriber.queued_bytes = 0
            self._client_stall_disconnects_total += 1
            self._last_client_stall_disconnect_monotonic = now
            if subscriber in self._clients:
                self._clients.remove(subscriber)
            self._last_client_detach_monotonic = now
            subscriber.wake_event.set()
        logger.warning(
            "audio_subscriber_evicted session_id=%s subscriber_id=%s role=%s delivery_path_id=%s queued_bytes=%s overflow_events=%s",
            self.session_id,
            subscriber.subscriber_id,
            subscriber.role,
            subscriber.delivery_path_id,
            subscriber.queued_bytes,
            subscriber.overflow_events,
        )

    async def _remove_subscriber(self, subscriber: PipelineSubscriber, *, reason: str) -> None:
        del reason
        async with self._lock:
            already_removed = subscriber not in self._clients
            subscriber.closed = True
            subscriber.wake_event.set()
            if not already_removed:
                self._clients.remove(subscriber)
                self._last_client_detach_monotonic = time.monotonic()

    def _resolve_keepalive_payload(self, now: float) -> tuple[bytes | None, str | None]:
        if not self._keepalive_enabled:
            return None, None

        idle_age_ms = self._get_idle_age_ms(now)
        if idle_age_ms < self._idle_threshold_ms:
            return None, None

        mode = "idle_pending_signal" if self._last_real_frame_monotonic is None else "healthy_but_idle"
        return self._silence_bytes, mode

    def _get_idle_age_ms(self, now: float) -> float:
        if self._last_real_frame_monotonic is not None:
            return (now - self._last_real_frame_monotonic) * 1000
        if self._pipeline_started_monotonic is not None:
            return (now - self._pipeline_started_monotonic) * 1000
        return 0.0

    def _get_outage_age_ms(self, now: float) -> float:
        return self._get_idle_age_ms(now)

    def _get_real_frame_age_ms(self, now: float) -> float | None:
        if self._last_real_frame_monotonic is None:
            return None
        return (now - self._last_real_frame_monotonic) * 1000

    def _set_runtime_mode(self, mode: str | None, now: float) -> None:
        next_mode = mode or "active"
        if self._runtime_mode == next_mode:
            return

        if self._runtime_mode in {"healthy_but_idle", "idle_pending_signal", "source_outage_grace"} and next_mode == "active":
            self._log_keepalive_transition("audio_keepalive_stopped", now)
            if self._keepalive_started_monotonic is not None:
                self._keepalive_to_first_real_frame_ms = (now - self._keepalive_started_monotonic) * 1000
            self._keepalive_started_monotonic = None

        self._runtime_mode = next_mode
        self._keepalive_active = next_mode in {"healthy_but_idle", "idle_pending_signal", "source_outage_grace"}
        self._source_outage_active = next_mode == "source_outage_grace"

        if next_mode in {"healthy_but_idle", "idle_pending_signal"}:
            self._keepalive_activation_count += 1
            self._keepalive_started_monotonic = now
            self._log_keepalive_transition("audio_keepalive_started", now)
        elif next_mode == "source_outage_grace":
            self._source_outage_activation_count += 1
            self._keepalive_started_monotonic = now
            self._log_keepalive_transition("audio_source_outage_started", now)

    def _log_keepalive_transition(self, event_name: str, now: float) -> None:
        logger.info(
            "%s session_id=%s idle_age_ms=%.1f outage_age_ms=%.1f jitter_buffer_size_ms=%.1f real_frames_written=%s silence_frames_written=%s last_encoder_write_monotonic=%s ffmpeg_input_format=%s runtime_mode=%s keepalive_to_first_real_frame_ms=%s",
            event_name,
            self.session_id,
            self._get_idle_age_ms(now),
            self._get_outage_age_ms(now),
            self.jitter_buffer.size_ms,
            self._real_frames_written,
            self._silence_frames_written,
            self._last_encoder_write_monotonic,
            asdict(self._ffmpeg_input_format),
            self._runtime_mode,
            self._keepalive_to_first_real_frame_ms,
        )

    async def _write_encoder_input(self, data: bytes, *, is_silence: bool, now: float) -> None:
        if not self._process or not self._process.stdin:
            raise RuntimeError(f"FFmpeg stdin unavailable for session {self.session_id}")

        try:
            self._process.stdin.write(data)
            await self._process.stdin.drain()
        except Exception:
            self._ffmpeg_stdin_write_errors += 1
            raise

        was_silence_before = self._last_encoder_write_was_silence
        self._encoder_write_count += 1
        self._last_encoder_write_monotonic = now
        self._last_stdin_write_monotonic = now
        self._last_encoder_write_was_silence = is_silence
        if is_silence:
            self._silence_frames_written += 1
            self._awaiting_keepalive_encoded_output = True
        else:
            if self._keepalive_active or self._runtime_mode != "active" or was_silence_before:
                self._awaiting_primary_resume_yield_since_monotonic = now
            self._real_frames_written += 1
            self._awaiting_real_encoded_output = True
        self._write_pre_encoder_debug(data)

    def _maybe_log_pacing_snapshot(self, now: float) -> None:
        if not self._debug_pacing_logs_enabled:
            return
        if self._last_pacing_log_monotonic is not None and (now - self._last_pacing_log_monotonic) < PACING_LOG_INTERVAL_SECONDS:
            return
        self._last_pacing_log_monotonic = now
        diagnostics = self.get_diagnostics_snapshot()
        logger.info(
            "audio_live_latency_snapshot session_id=%s diagnostics=%s",
            self.session_id,
            diagnostics,
        )

    def _log_format_audit(self) -> None:
        logger.info(
            "audio_format_audit session_id=%s sample_format=%s sample_rate=%s channels=%s bytes_per_sample=%s frame_duration_ms=%s bytes_per_frame=%s live_jitter_target_ms=%s transport_heartbeat_window_ms=%s delivery_profile=%s primary_policy=%s aux_policy=%s",
            self.session_id,
            self._ffmpeg_input_format.sample_format,
            self._ffmpeg_input_format.sample_rate,
            self._ffmpeg_input_format.channels,
            self._ffmpeg_input_format.bytes_per_sample,
            self._ffmpeg_input_format.frame_duration_ms,
            self._ffmpeg_input_format.bytes_per_frame,
            self._live_jitter_target_ms,
            self._transport_heartbeat_window_ms,
            self._delivery_profile,
            asdict(self._primary_policy),
            asdict(self._aux_policy),
        )

    def _evict_old_stdout_window_samples(self, now: float) -> None:
        window_seconds = self._transport_heartbeat_window_ms / 1000
        while self._stdout_window_samples and (now - self._stdout_window_samples[0][0]) > window_seconds:
            self._stdout_window_samples.popleft()

    def _encoded_bytes_emitted_last_window(self, now: float) -> int:
        self._evict_old_stdout_window_samples(now)
        return sum(size for _, size in self._stdout_window_samples)

    def _is_transport_alive(self, now: float, encoded_bytes_emitted_last_window: int | None = None) -> bool:
        if not self._active:
            return False
        if encoded_bytes_emitted_last_window is None:
            encoded_bytes_emitted_last_window = self._encoded_bytes_emitted_last_window(now)
        if self._last_stdout_read_monotonic is None:
            return False
        heartbeat_window_seconds = self._transport_heartbeat_window_ms / 1000
        return (now - self._last_stdout_read_monotonic) <= heartbeat_window_seconds and encoded_bytes_emitted_last_window > 0

    def _elapsed_since_start_ms(self, marker: float | None) -> float | None:
        if marker is None or self._pipeline_started_monotonic is None:
            return None
        return (marker - self._pipeline_started_monotonic) * 1000

    def _open_debug_captures(self) -> None:
        if not self._debug_capture_enabled:
            return

        if self._debug_capture_pre_encoder_path:
            pre_path = Path(self._debug_capture_pre_encoder_path)
            pre_path.parent.mkdir(parents=True, exist_ok=True)
            if self._ffmpeg_input_format.sample_format in {"s16le", "s24le", "s32le"}:
                writer = wave.open(str(pre_path), "wb")
                writer.setnchannels(self._ffmpeg_input_format.channels)
                writer.setsampwidth(self._ffmpeg_input_format.bytes_per_sample)
                writer.setframerate(self._ffmpeg_input_format.sample_rate)
                self._pre_encoder_debug_writer = writer
                self._pre_encoder_debug_mode = "wav"
            else:
                self._pre_encoder_debug_writer = pre_path.open("wb")
                self._pre_encoder_debug_mode = "raw"
                self._pre_encoder_sidecar_path = pre_path.with_suffix(f"{pre_path.suffix}.json")
                self._pre_encoder_sidecar_path.write_text(json.dumps(asdict(self._ffmpeg_input_format), indent=2))
            logger.info("audio_debug_capture_started session_id=%s capture=pre_encoder path=%s", self.session_id, pre_path)

        if self._debug_capture_post_encoder_path:
            post_path = Path(self._debug_capture_post_encoder_path)
            post_path.parent.mkdir(parents=True, exist_ok=True)
            self._post_encoder_debug_writer = post_path.open("wb")
            logger.info("audio_debug_capture_started session_id=%s capture=post_encoder path=%s", self.session_id, post_path)

    def _write_pre_encoder_debug(self, data: bytes) -> None:
        if self._pre_encoder_debug_writer is None:
            return
        if self._pre_encoder_debug_mode == "wav":
            self._pre_encoder_debug_writer.writeframesraw(data)
        else:
            self._pre_encoder_debug_writer.write(data)

    def _write_post_encoder_debug(self, data: bytes) -> None:
        if self._post_encoder_debug_writer is None:
            return
        self._post_encoder_debug_writer.write(data)

    def _close_debug_captures(self) -> None:
        if self._pre_encoder_debug_writer is not None:
            try:
                self._pre_encoder_debug_writer.close()
            except Exception:
                logger.exception("Failed closing pre-encoder debug capture for session %s", self.session_id)
            finally:
                logger.info("audio_debug_capture_stopped session_id=%s capture=pre_encoder", self.session_id)
                self._pre_encoder_debug_writer = None
                self._pre_encoder_debug_mode = None

        if self._post_encoder_debug_writer is not None:
            try:
                self._post_encoder_debug_writer.close()
            except Exception:
                logger.exception("Failed closing post-encoder debug capture for session %s", self.session_id)
            finally:
                logger.info("audio_debug_capture_stopped session_id=%s capture=post_encoder", self.session_id)
                self._post_encoder_debug_writer = None


def _bytes_per_sample_for_format(sample_format: str) -> int:
    mapping = {
        "s16le": 2,
        "s24le": 3,
        "s32le": 4,
        "f32le": 4,
    }
    if sample_format not in mapping:
        raise ValueError(f"Unsupported PCM sample format: {sample_format}")
    return mapping[sample_format]
