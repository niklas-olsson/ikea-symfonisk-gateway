"""PyAudioWPatch-backed Windows loopback capture."""

from __future__ import annotations

import logging
import sys
import time
from typing import Any

import numpy as np
from ingress_sdk.base import FrameSink
from ingress_sdk.types import HealthResult, PrepareResult, SourceCapabilities, SourceDescriptor, SourceType, StartResult

from .models import BackendProbeResult, BackendStartupDiagnostics

logger = logging.getLogger(__name__)

CALLBACK_GRACE_SECONDS = 5.0


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
        self._default_device_info: dict[str, Any] | None = None
        self._default_render_device_info: dict[str, Any] | None = None
        self._selected_host_api_info: dict[str, Any] | None = None
        self._diagnostics = BackendStartupDiagnostics()
        self._non_silent_signal_detected = False
        if self._probe_result.available:
            host_api, render_device, loopback_device = resolve_default_loopback_triplet(self._pawp)
            self._selected_host_api_info = host_api
            self._default_render_device_info = render_device
            self._default_device_info = loopback_device
            self._diagnostics.selected_host_api = host_api
            self._diagnostics.default_render_device = render_device
            self._diagnostics.loopback_device = loopback_device

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
            "status": "ready" if self._probe_result.available else "degraded",
            "startable": self._probe_result.available,
            "degraded": not self._probe_result.available,
            "backend_probe": self._probe_result.model_dump(),
            "selected_host_api": self._diagnostics.selected_host_api,
            "default_render_device": self._diagnostics.default_render_device,
            "loopback_device": self._diagnostics.loopback_device,
            "startup_substate": self._diagnostics.startup_substate,
            "callback_count": self._diagnostics.callback_count,
            "non_empty_buffer_count": self._diagnostics.non_empty_buffer_count,
            "samples_received": self._diagnostics.samples_received,
            "frames_emitted": self._diagnostics.frames_emitted,
            "first_callback_at": self._diagnostics.first_callback_at,
            "start_viability": self._diagnostics.start_viability,
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

        if self._pawp is None:
            return StartResult(
                success=False,
                message=self._probe_result.message or "Windows loopback backend unavailable",
                code=self._probe_result.code,
                backend=self.name(),
            )

        host_api, render_device, device_info = resolve_default_loopback_triplet(self._pawp)
        self._selected_host_api_info = host_api
        self._default_render_device_info = render_device
        self._default_device_info = device_info
        self._reset_diagnostics()
        self._diagnostics.selected_host_api = host_api
        self._diagnostics.default_render_device = render_device
        self._diagnostics.loopback_device = device_info

        if not device_info:
            self._diagnostics.startup_substate = "device_not_found"
            return StartResult(
                success=False,
                message="The default Windows output device was not found for loopback capture.",
                code="windows_loopback_device_not_found",
                backend=self.name(),
            )
        self._diagnostics.start_viability["loopback_device_resolved"] = True

        self._frame_sink = frame_sink
        self._session_id = f"win_sess_{int(time.time())}"
        self._samples_captured = 0
        self._last_samples_captured = 0
        self._dropped_frames = 0
        self._last_error = None
        self._non_silent_signal_detected = False
        self._diagnostics.startup_substate = "starting"

        logger.info(
            "Windows loopback start: backend=%s host_api=%s render_device=%s loopback_device=%s sample_rate=%s channels=%s sample_format=%s frames_per_buffer=%s",
            self.name(),
            _device_log_label(host_api, role="host_api"),
            _device_log_label(render_device, role="render"),
            _device_log_label(device_info, role="loopback_capture"),
            self._diagnostics.sample_rate,
            self._diagnostics.channels,
            self._diagnostics.sample_format,
            self._diagnostics.frames_per_buffer,
        )
        logger.info(
            "Windows loopback viability: backend=%s loopback_device_resolved=%s callback_registered=%s stream_parameters_accepted=%s",
            self.name(),
            self._diagnostics.start_viability["loopback_device_resolved"],
            True,
            False,
        )

        try:
            self._pa = self._pawp.PyAudio()
            self._stream = self._pa.open(
                format=self._pawp.paInt16,
                channels=2,
                rate=48000,
                input=True,
                input_device_index=device_info.get("index"),
                frames_per_buffer=self._diagnostics.frames_per_buffer,
                stream_callback=self._audio_callback,
            )
            self._diagnostics.stream_opened = True
            self._diagnostics.start_viability["callback_registered"] = True
            if hasattr(self._stream, "start_stream"):
                self._stream.start_stream()
            elif hasattr(self._stream, "start"):
                self._stream.start()
            self._diagnostics.stream_started = True
            self._diagnostics.start_viability["stream_parameters_accepted"] = True
            self._diagnostics.startup_substate = "stream_started_no_callbacks"
            logger.info(
                "Windows loopback viability: backend=%s loopback_device_resolved=%s callback_registered=%s stream_parameters_accepted=%s",
                self.name(),
                self._diagnostics.start_viability["loopback_device_resolved"],
                self._diagnostics.start_viability["callback_registered"],
                self._diagnostics.start_viability["stream_parameters_accepted"],
            )
        except Exception as exc:
            self._last_error = str(exc)
            self._diagnostics.last_callback_error = str(exc)
            self._diagnostics.startup_substate = "stream_open_failed"
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
        signal_present = self._running and self._non_silent_signal_detected
        self._last_samples_captured = self._samples_captured
        source_state = self._diagnostics.startup_substate if self._running else "idle"
        return HealthResult(
            healthy=self._running and self._last_error is None,
            source_state=source_state,
            signal_present=signal_present,
            dropped_frames=self._dropped_frames,
            last_error=self._last_error or self._diagnostics.last_callback_error,
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
            host_api, render_device, default_device = resolve_default_loopback_triplet(self._pawp)
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

        if default_device is None or render_device is None or host_api is None:
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

    def _audio_callback(self, in_data: bytes | np.ndarray, frame_count: int, time_info: Any, status_flags: Any) -> tuple[None, int]:
        del time_info
        if not self._running or self._frame_sink is None:
            return (None, self._continue_flag())

        self._diagnostics.callback_count += 1
        if not self._diagnostics.callback_started:
            self._diagnostics.callback_started = True
            self._diagnostics.first_callback_at = time.monotonic()
            logger.info("Windows loopback first callback: backend=%s at=%.6f", self.name(), self._diagnostics.first_callback_at)

        if status_flags:
            self._dropped_frames += 1
            self._diagnostics.last_status_flags = str(status_flags)

        try:
            if isinstance(in_data, np.ndarray):
                audio = in_data
                raw_bytes = int(audio.nbytes)
            else:
                audio = np.frombuffer(in_data, dtype=np.int16)
                raw_bytes = len(in_data)

            self._diagnostics.raw_bytes_received += raw_bytes
            if raw_bytes > 0:
                self._diagnostics.non_empty_buffer_count += 1

            if audio.ndim == 1:
                if frame_count > 0:
                    channels = max(1, int(audio.size / frame_count)) if audio.size > 0 else 1
                    if audio.size > 0:
                        audio = audio.reshape((frame_count, channels))
                    else:
                        audio = np.zeros((frame_count, channels), dtype=np.int16)
                else:
                    audio = np.zeros((0, 2), dtype=np.int16)

            self._diagnostics.samples_received += int(audio.size)

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

            if raw_bytes == 0 or audio.size == 0:
                self._diagnostics.startup_substate = "callbacks_active_no_samples"
            elif not data_bytes:
                self._diagnostics.startup_substate = "samples_received_no_frames_emitted"
            else:
                if np.any(data_stereo):
                    self._non_silent_signal_detected = True
                    self._diagnostics.startup_substate = "active"
                else:
                    self._diagnostics.startup_substate = "healthy_but_idle"

            pts_ns = int(self._samples_captured * 1_000_000_000 / 48000)
            duration_ns = int(frame_count * 1_000_000_000 / 48000)
            self._frame_sink.on_frame(data_bytes, pts_ns, duration_ns)
            self._samples_captured += frame_count
            if data_bytes:
                self._diagnostics.frames_emitted += 1
                if self._diagnostics.startup_substate == "samples_received_no_frames_emitted":
                    self._diagnostics.startup_substate = "healthy_but_idle"
        except Exception as exc:
            self._last_error = str(exc)
            self._diagnostics.last_callback_error = str(exc)
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

    def get_diagnostics_snapshot(self) -> BackendStartupDiagnostics:
        return self._diagnostics.model_copy(deep=True)

    def get_start_viability_snapshot(self) -> dict[str, bool]:
        return dict(self._diagnostics.start_viability)

    def _reset_diagnostics(self) -> None:
        self._diagnostics = BackendStartupDiagnostics()


def resolve_default_loopback_triplet(
    backend_module: Any | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    """Resolve host API, default render device, and matching loopback device."""
    if backend_module is None:
        return None, None, None

    pa = backend_module.PyAudio()
    try:
        host_apis = _enumerate_host_apis(pa)
        devices = _enumerate_devices(pa)
        default_render = _get_default_render_device(pa)
        if default_render is None:
            return None, None, None

        host_api = _resolve_host_api(default_render, host_apis)
        loopback = _resolve_loopback_device(pa, default_render, devices)
        return host_api, default_render, loopback
    finally:
        if hasattr(pa, "terminate"):
            pa.terminate()


def _enumerate_host_apis(pa: Any) -> list[dict[str, Any]]:
    count_getter = getattr(pa, "get_host_api_count", None)
    info_getter = getattr(pa, "get_host_api_info_by_index", None)
    if count_getter is None or info_getter is None:
        return []
    return [dict(info_getter(index)) for index in range(int(count_getter()))]


def _enumerate_devices(pa: Any) -> list[dict[str, Any]]:
    count_getter = getattr(pa, "get_device_count", None)
    info_getter = getattr(pa, "get_device_info_by_index", None)
    if count_getter is None or info_getter is None:
        return []
    return [dict(info_getter(index)) for index in range(int(count_getter()))]


def _get_default_render_device(pa: Any) -> dict[str, Any] | None:
    getter = getattr(pa, "get_default_output_device_info", None)
    if getter is not None:
        device = getter()
        if device is not None:
            return dict(device)

    for getter_name in ("get_default_wasapi_device", "get_default_wasapi_output_device_info"):
        getter = getattr(pa, getter_name, None)
        if getter is not None:
            device = getter()
            if device is not None:
                return dict(device)

    return None


def _resolve_host_api(device: dict[str, Any], host_apis: list[dict[str, Any]]) -> dict[str, Any] | None:
    host_api_index = device.get("hostApi", device.get("hostapi"))
    if host_api_index is None:
        return None
    for host_api in host_apis:
        if host_api.get("index") == host_api_index:
            return host_api
    if 0 <= int(host_api_index) < len(host_apis):
        host_api = dict(host_apis[int(host_api_index)])
        host_api.setdefault("index", int(host_api_index))
        return host_api
    return {"index": host_api_index}


def _resolve_loopback_device(pa: Any, default_render: dict[str, Any], devices: list[dict[str, Any]]) -> dict[str, Any] | None:
    for getter_name in ("get_default_wasapi_loopback", "get_default_wasapi_loopback_device_info"):
        getter = getattr(pa, getter_name, None)
        if getter is not None:
            device = getter()
            if device is not None:
                return dict(device)

    for key in (
        "defaultLoopbackDeviceIndex",
        "default_loopback_device_index",
        "loopbackDeviceIndex",
        "loopback_device_index",
    ):
        if isinstance(default_render.get(key), int):
            loopback = _device_by_index(devices, int(default_render[key]))
            if loopback is not None:
                return loopback

    render_name = str(default_render.get("name", "")).lower()
    render_host_api = default_render.get("hostApi", default_render.get("hostapi"))
    candidates: list[dict[str, Any]] = []
    for device in devices:
        device_name = str(device.get("name", "")).lower()
        device_host_api = device.get("hostApi", device.get("hostapi"))
        if render_host_api is not None and device_host_api != render_host_api:
            continue
        if device.get("isLoopbackDevice") or device.get("loopback") or device.get("defaultLoopbackDevice"):
            candidates.append(device)
            continue
        if render_name and render_name in device_name and "loopback" in device_name:
            candidates.append(device)

    return candidates[0] if candidates else None


def _device_by_index(devices: list[dict[str, Any]], index: int) -> dict[str, Any] | None:
    for device in devices:
        if device.get("index") == index:
            return device
    return None


def _device_log_label(device: dict[str, Any] | None, role: str) -> str:
    if not device:
        return f"{role}=none"
    host_api = device.get("hostApi", device.get("hostapi", "?"))
    return (
        f"{role}[name={device.get('name', 'unknown')},index={device.get('index', '?')},"
        f"host_api={host_api},loopback={device.get('isLoopbackDevice', device.get('loopback', False))}]"
    )
