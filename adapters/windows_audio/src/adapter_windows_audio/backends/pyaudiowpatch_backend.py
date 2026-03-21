"""PyAudioWPatch-backed Windows loopback capture."""

from __future__ import annotations

import logging
import sys
import time
from typing import Any

import numpy as np
from ingress_sdk.base import FrameSink
from ingress_sdk.types import HealthResult, PrepareResult, SourceCapabilities, SourceDescriptor, SourceType, StartResult

from .models import BackendProbeResult

logger = logging.getLogger(__name__)


def load_pyaudiowpatch() -> Any | None:
    """Import the optional Windows loopback backend."""
    try:
        import pyaudiowpatch as pawp  # type: ignore[import-not-found]

        return pawp
    except ImportError:
        return None


class PyAudioWPatchBackend:
    """Real Windows system-output capture backed by PyAudioWPatch."""

    def __init__(self, backend_module: Any | None = None) -> None:
        self._pawp = backend_module or load_pyaudiowpatch()
        self._probe_result = self._probe_backend()
        self._pa: Any | None = None
        self._stream: Any | None = None
        self._frame_sink: FrameSink | None = None
        self._session_id: str | None = None
        self._running = False
        self._samples_captured = 0
        self._last_samples_captured = 0
        self._dropped_frames = 0
        self._last_error: str | None = None
        self._default_device_info: dict[str, Any] | None = self._resolve_default_loopback_device() if self._probe_result.available else None

    def name(self) -> str:
        return "pyaudiowpatch"

    def probe(self) -> BackendProbeResult:
        return self._probe_result

    def list_sources(self) -> list[SourceDescriptor]:
        metadata = {
            "backend": self.name(),
            "backend_state": self._probe_result.state,
            "loopback_supported": self._probe_result.loopback_supported,
            "default_output_detected": self._probe_result.default_output_detected,
            "degraded": not self._probe_result.available,
        }
        return [
            SourceDescriptor(
                source_id="default",
                source_type=SourceType.SYSTEM_OUTPUT,
                display_name="Default System Sound (windows)",
                platform="windows",
                capabilities=SourceCapabilities(sample_rates=[48000], channels=[2], bit_depths=[16]),
                metadata=metadata,
            )
        ]

    def prepare(self, local_source_id: str) -> PrepareResult:
        if local_source_id != "default":
            return PrepareResult(success=False, source_id=local_source_id, error="Source not found", code="source_not_found")
        if not self._probe_result.available:
            return PrepareResult(
                success=False,
                source_id=local_source_id,
                error=self._probe_result.message,
                code=self._probe_result.code,
            )
        return PrepareResult(success=True, source_id=local_source_id)

    def start(self, local_source_id: str, frame_sink: FrameSink) -> StartResult:
        if self._running:
            return StartResult(success=False, message="Already running", backend=self.name())
        if local_source_id != "default":
            return StartResult(success=False, message="Source not found", code="source_not_found", backend=self.name())
        if not self._probe_result.available:
            return StartResult(
                success=False,
                message=self._probe_result.message or "Windows loopback backend unavailable",
                code=self._probe_result.code,
                backend=self.name(),
            )

        device_info = self._default_device_info or self._resolve_default_loopback_device()
        if not device_info:
            return StartResult(
                success=False,
                message="The default Windows output device was not found for loopback capture.",
                code="windows_loopback_device_not_found",
                backend=self.name(),
            )
        assert self._pawp is not None

        self._frame_sink = frame_sink
        self._session_id = f"win_sess_{int(time.time())}"
        self._samples_captured = 0
        self._last_samples_captured = 0
        self._dropped_frames = 0
        self._last_error = None

        try:
            self._pa = self._pawp.PyAudio()
            self._stream = self._pa.open(
                format=self._pawp.paInt16,
                channels=2,
                rate=48000,
                input=True,
                input_device_index=device_info.get("index"),
                frames_per_buffer=480,
                stream_callback=self._audio_callback,
            )
            if hasattr(self._stream, "start_stream"):
                self._stream.start_stream()
            elif hasattr(self._stream, "start"):
                self._stream.start()
        except Exception as exc:
            self._last_error = str(exc)
            logger.exception("Failed to start PyAudioWPatch loopback")
            self._close_stream()
            return StartResult(
                success=False,
                message=str(exc),
                code="windows_loopback_start_failed",
                backend=self.name(),
            )

        self._running = True
        return StartResult(success=True, session_id=self._session_id, backend=self.name())

    def stop(self, session_id: str) -> None:
        if self._session_id != session_id:
            return
        self._running = False
        self._close_stream()
        self._session_id = None
        self._frame_sink = None

    def probe_health(self, local_source_id: str) -> HealthResult:
        del local_source_id
        signal_present = self._running and self._samples_captured > self._last_samples_captured
        self._last_samples_captured = self._samples_captured
        return HealthResult(
            healthy=self._running and self._last_error is None,
            source_state="active" if self._running else "idle",
            signal_present=signal_present,
            dropped_frames=self._dropped_frames,
            last_error=self._last_error,
        )

    def _probe_backend(self) -> BackendProbeResult:
        if self._pawp is None:
            return BackendProbeResult(
                backend=self.name(),
                available=False,
                state="missing_dependency",
                loopback_supported=False,
                default_output_detected=False,
                code="windows_loopback_backend_missing",
                message="No Windows loopback-capable backend is installed.",
            )

        try:
            default_device = self._resolve_default_loopback_device()
        except Exception as exc:
            logger.exception("Failed probing PyAudioWPatch backend")
            return BackendProbeResult(
                backend=self.name(),
                available=False,
                state="probe_failed",
                loopback_supported=True,
                default_output_detected=False,
                code="windows_loopback_probe_failed",
                message=str(exc),
            )

        if default_device is None:
            return BackendProbeResult(
                backend=self.name(),
                available=False,
                state="probe_failed",
                loopback_supported=True,
                default_output_detected=False,
                code="windows_loopback_device_not_found",
                message="The default Windows output device was not found for loopback capture.",
            )

        return BackendProbeResult(
            backend=self.name(),
            available=True,
            state="available",
            loopback_supported=True,
            default_output_detected=True,
        )

    def _resolve_default_loopback_device(self) -> dict[str, Any] | None:
        if self._pawp is None:
            return None

        pa = self._pawp.PyAudio()
        try:
            for getter_name in ("get_default_wasapi_loopback", "get_default_wasapi_loopback_device_info"):
                getter = getattr(pa, getter_name, None)
                if getter is not None:
                    device = getter()
                    return dict(device) if device is not None else None
        finally:
            if hasattr(pa, "terminate"):
                pa.terminate()

        return None

    def _audio_callback(self, in_data: bytes | np.ndarray, frame_count: int, time_info: Any, status_flags: Any) -> tuple[None, int]:
        del time_info
        if not self._running or self._frame_sink is None:
            return (None, self._continue_flag())

        if status_flags:
            self._dropped_frames += 1

        try:
            if isinstance(in_data, np.ndarray):
                audio = in_data
            else:
                audio = np.frombuffer(in_data, dtype=np.int16)

            if audio.ndim == 1:
                if frame_count > 0:
                    channels = max(1, int(audio.size / frame_count))
                    audio = audio.reshape((frame_count, channels))
                else:
                    audio = np.zeros((0, 2), dtype=np.int16)

            if audio.ndim == 1:
                data_stereo = np.column_stack((audio, audio))
            elif audio.shape[1] == 1:
                data_stereo = np.column_stack((audio[:, 0], audio[:, 0]))
            elif audio.shape[1] > 2:
                data_stereo = audio[:, :2].copy()
            else:
                data_stereo = audio

            if data_stereo.dtype.byteorder == ">" or (data_stereo.dtype.byteorder == "=" and sys.byteorder == "big"):
                data_bytes = data_stereo.astype("<i2").tobytes()
            else:
                data_bytes = data_stereo.astype(np.int16, copy=False).tobytes()

            pts_ns = int(self._samples_captured * 1_000_000_000 / 48000)
            duration_ns = int(frame_count * 1_000_000_000 / 48000)
            self._frame_sink.on_frame(data_bytes, pts_ns, duration_ns)
            self._samples_captured += frame_count
        except Exception as exc:
            self._last_error = str(exc)
            logger.exception("Error in PyAudioWPatch callback")
            self._frame_sink.on_error(exc)

        return (None, self._continue_flag())

    def _continue_flag(self) -> int:
        return int(getattr(self._pawp, "paContinue", 0))

    def _close_stream(self) -> None:
        if self._stream is not None:
            try:
                if hasattr(self._stream, "stop_stream"):
                    self._stream.stop_stream()
                elif hasattr(self._stream, "stop"):
                    self._stream.stop()
                self._stream.close()
            except Exception:
                logger.exception("Error closing PyAudioWPatch stream")
            self._stream = None
        if self._pa is not None:
            try:
                if hasattr(self._pa, "terminate"):
                    self._pa.terminate()
            except Exception:
                logger.exception("Error terminating PyAudioWPatch")
            self._pa = None
