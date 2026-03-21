"""Audio pipeline implementation with jitter buffer, keepalive, and FFmpeg encoding."""

import asyncio
import heapq
import json
import logging
import time
import wave
from collections.abc import AsyncGenerator, Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ingress_sdk.protocol import AudioFrame

from bridge_core.stream.profiles import STREAM_PROFILES

logger = logging.getLogger(__name__)
DEFAULT_PCM_SAMPLE_FORMAT = "s16le"
DEFAULT_KEEPALIVE_IDLE_THRESHOLD_MS = 200
DEFAULT_KEEPALIVE_FRAME_DURATION_MS = 20
DEFAULT_SOURCE_OUTAGE_GRACE_MS = 5000
PACING_LOG_INTERVAL_SECONDS = 2.0


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


class StreamPipeline:
    """Active audio pipeline for a session using FFmpeg for encoding."""

    def __init__(
        self,
        session_id: str,
        profile_id: str,
        ffmpeg_path: str = "ffmpeg",
        on_error: Any | None = None,
        keepalive_enabled: bool = True,
        keepalive_idle_threshold_ms: int = DEFAULT_KEEPALIVE_IDLE_THRESHOLD_MS,
        source_outage_grace_ms: int = DEFAULT_SOURCE_OUTAGE_GRACE_MS,
        keepalive_frame_duration_ms: int = DEFAULT_KEEPALIVE_FRAME_DURATION_MS,
        debug_capture_enabled: bool = False,
        debug_capture_pre_encoder_path: str | None = None,
        debug_capture_post_encoder_path: str | None = None,
        debug_pacing_logs_enabled: bool = False,
        source_health_provider: Callable[[], Any | None] | None = None,
        input_format: PipelineInputFormat | None = None,
    ):
        self.session_id = session_id
        self.profile_id = profile_id
        self.ffmpeg_path = ffmpeg_path
        self.on_error = on_error
        self.profile = STREAM_PROFILES[profile_id]
        self._ffmpeg_input_format = input_format or self._build_default_input_format(keepalive_frame_duration_ms)
        self.jitter_buffer = JitterBuffer(sample_rate=self._ffmpeg_input_format.sample_rate)
        self._process: asyncio.subprocess.Process | None = None
        self._clients: list[asyncio.Queue[bytes]] = []
        self._active = False
        self._lock = asyncio.Lock()
        self._feed_task: asyncio.Task[None] | None = None
        self._read_task: asyncio.Task[None] | None = None

        self._source_health_provider = source_health_provider
        self._keepalive_enabled = keepalive_enabled
        self._idle_threshold_ms = keepalive_idle_threshold_ms
        self._source_outage_grace_ms = source_outage_grace_ms
        self._frame_duration_ms = self._ffmpeg_input_format.frame_duration_ms
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
        self._ffmpeg_stdin_write_errors = 0
        self._jitter_buffer_underrun_count = 0
        self._last_pacing_log_monotonic: float | None = None
        self._runtime_mode: str = "active"
        self._silence_bytes = b"\x00" * self._ffmpeg_input_format.bytes_per_frame

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
        if self._feed_task:
            self._feed_task.cancel()
        if self._read_task:
            self._read_task.cancel()

        if self._process:
            try:
                self._process.terminate()
                await self._process.wait()
            except Exception:
                pass
            self._process = None

        self._close_debug_captures()

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
                    self._set_runtime_mode(None, now, None)
                else:
                    self._jitter_buffer_underrun_count += 1
                    keepalive_payload, keepalive_mode = self._resolve_keepalive_payload(now)
                    if keepalive_payload is not None:
                        await self._write_encoder_input(keepalive_payload, is_silence=True, now=now)
                        self._set_runtime_mode(keepalive_mode, now, self._get_source_health())

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

                    self._write_post_encoder_debug(data)
                    async with self._lock:
                        for client_queue in self._clients:
                            try:
                                client_queue.put_nowait(data)
                            except asyncio.QueueFull:
                                pass
                else:
                    await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error reading from FFmpeg for session %s", self.session_id)
                raise

    async def subscribe(self) -> AsyncGenerator[bytes, None]:
        """Subscribe to encoded audio data. Yields chunks of encoded data."""
        queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=100)
        async with self._lock:
            self._clients.append(queue)

        try:
            while True:
                data = await queue.get()
                yield data
        finally:
            async with self._lock:
                self._clients.remove(queue)

    def get_diagnostics_snapshot(self) -> dict[str, Any]:
        """Return a diagnostic snapshot for logging and tests."""
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
            "ffmpeg_input_format": {
                **asdict(self._ffmpeg_input_format),
                "samples_per_channel_per_frame": self._ffmpeg_input_format.samples_per_channel_per_frame,
                "bytes_per_frame": self._ffmpeg_input_format.bytes_per_frame,
                "duration_ns": self._ffmpeg_input_format.duration_ns,
            },
        }

    def _resolve_keepalive_payload(self, now: float) -> tuple[bytes | None, str | None]:
        if not self._keepalive_enabled:
            return None, None

        idle_age_ms = self._get_idle_age_ms(now)
        if idle_age_ms < self._idle_threshold_ms:
            return None, None

        health = self._get_source_health()
        if self._is_healthy_idle(health):
            return self._silence_bytes, "healthy_but_idle"

        outage_age_ms = self._get_outage_age_ms(now)
        if outage_age_ms < self._source_outage_grace_ms:
            return self._silence_bytes, "source_outage_grace"

        raise RuntimeError(self._build_outage_failure_message(health, outage_age_ms))

    def _is_healthy_idle(self, health: Any | None) -> bool:
        return bool(health and health.healthy and not health.signal_present)

    def _build_outage_failure_message(self, health: Any | None, outage_age_ms: float) -> str:
        state = getattr(health, "source_state", "unknown") if health is not None else "missing_health"
        return (
            f"Source outage grace exceeded for session {self.session_id}: "
            f"state={state} outage_age_ms={outage_age_ms:.1f}"
        )

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

    def _get_source_health(self) -> Any | None:
        if self._source_health_provider is None:
            return None
        try:
            return self._source_health_provider()
        except Exception:
            logger.exception("Error probing source health for session %s", self.session_id)
            return None

    def _set_runtime_mode(self, mode: str | None, now: float, health: Any | None) -> None:
        next_mode = mode or "active"
        if self._runtime_mode == next_mode:
            return

        if self._runtime_mode == "healthy_but_idle" and next_mode != "healthy_but_idle":
            self._log_keepalive_transition("audio_keepalive_stopped", now, health)
        if self._runtime_mode == "source_outage_grace" and next_mode != "source_outage_grace":
            self._log_keepalive_transition("audio_source_outage_cleared", now, health)

        self._runtime_mode = next_mode
        self._keepalive_active = next_mode in {"healthy_but_idle", "source_outage_grace"}
        self._source_outage_active = next_mode == "source_outage_grace"

        if next_mode == "healthy_but_idle":
            self._keepalive_activation_count += 1
            self._log_keepalive_transition("audio_keepalive_started", now, health)
        elif next_mode == "source_outage_grace":
            self._source_outage_activation_count += 1
            self._log_keepalive_transition("audio_source_outage_started", now, health)

    def _log_keepalive_transition(self, event_name: str, now: float, health: Any | None) -> None:
        logger.info(
            "%s session_id=%s idle_age_ms=%.1f outage_age_ms=%.1f jitter_buffer_size_ms=%.1f real_frames_written=%s silence_frames_written=%s last_encoder_write_monotonic=%s ffmpeg_input_format=%s source_state=%s",
            event_name,
            self.session_id,
            self._get_idle_age_ms(now),
            self._get_outage_age_ms(now),
            self.jitter_buffer.size_ms,
            self._real_frames_written,
            self._silence_frames_written,
            self._last_encoder_write_monotonic,
            asdict(self._ffmpeg_input_format),
            getattr(health, "source_state", None),
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

        self._encoder_write_count += 1
        self._last_encoder_write_monotonic = now
        self._last_encoder_write_was_silence = is_silence
        if is_silence:
            self._silence_frames_written += 1
        else:
            self._real_frames_written += 1
        self._write_pre_encoder_debug(data)

    def _maybe_log_pacing_snapshot(self, now: float) -> None:
        if not self._debug_pacing_logs_enabled:
            return
        if self._last_pacing_log_monotonic is not None and (now - self._last_pacing_log_monotonic) < PACING_LOG_INTERVAL_SECONDS:
            return
        self._last_pacing_log_monotonic = now
        diagnostics = self.get_diagnostics_snapshot()
        logger.info(
            "audio_pacing_snapshot session_id=%s diagnostics=%s",
            self.session_id,
            diagnostics,
        )

    def _log_format_audit(self) -> None:
        logger.info(
            "audio_format_audit session_id=%s sample_format=%s sample_rate=%s channels=%s bytes_per_sample=%s frame_duration_ms=%s bytes_per_frame=%s",
            self.session_id,
            self._ffmpeg_input_format.sample_format,
            self._ffmpeg_input_format.sample_rate,
            self._ffmpeg_input_format.channels,
            self._ffmpeg_input_format.bytes_per_sample,
            self._ffmpeg_input_format.frame_duration_ms,
            self._ffmpeg_input_format.bytes_per_frame,
        )

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
