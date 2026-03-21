"""Windows audio (WASAPI loopback) ingress adapter.

Captures system audio using Windows WASAPI loopback via sounddevice.
Emits canonical PCM 48kHz stereo frames.
"""

import logging
import platform
import sys
import time
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    import sounddevice as sd  # type: ignore[import-untyped]

from ingress_sdk.base import FrameSink, IngressAdapter
from ingress_sdk.types import (
    AdapterCapabilities,
    HealthResult,
    PairingResult,
    PrepareResult,
    SourceCapabilities,
    SourceDescriptor,
    SourceType,
    StartResult,
)

logger = logging.getLogger(__name__)


def _get_sd() -> Any:
    """Helper to get sounddevice module, handled missing library on non-Windows."""
    try:
        import sounddevice as sd

        return sd
    except (ImportError, OSError):
        return None


class WindowsAudioAdapter(IngressAdapter):
    """Adapter for capturing system audio on Windows."""

    def __init__(self) -> None:
        self._session_id: str | None = None
        self._running = False
        self._stream: sd.InputStream | None = None
        self._frame_sink: FrameSink | None = None
        self._start_time: float = 0
        self._samples_captured: int = 0
        self._last_samples_captured: int = 0
        self._last_error: str | None = None
        self._dropped_frames: int = 0

    def id(self) -> str:
        return "windows-audio-adapter"

    def platform(self) -> str:
        return "windows"

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            supports_system_audio=True,
            supports_bluetooth_audio=False,
            supports_line_in=True,
            supports_microphone=True,
            supports_file_replay=False,
            supports_synthetic_test_source=False,
            supports_sample_rates=[44100, 48000],
            supports_channels=[2],
            supports_hotplug_events=False,  # sounddevice doesn't easily expose this
            supports_pairing=False,
        )

    def list_sources(self) -> list[SourceDescriptor]:
        """List Windows audio sources, prioritizing system output."""
        sources: list[SourceDescriptor] = []

        # Always provide the default system sound entry as the first option
        sources.append(
            SourceDescriptor(
                source_id="default",
                source_type=SourceType.SYSTEM_AUDIO,
                display_name="Default System Sound windows",
                platform="windows",
                capabilities=SourceCapabilities(
                    sample_rates=[48000],
                    channels=[2],
                    bit_depths=[16],
                ),
            )
        )

        sd = _get_sd()
        if sd is None:
            return sources

        try:
            devices = sd.query_devices()
            hostapis = sd.query_hostapis()

            wasapi_api_index = -1
            for i, api in enumerate(hostapis):
                if api["name"] == "Windows WASAPI":
                    wasapi_api_index = i
                    break

            render_sources = []
            capture_sources = []

            for i, dev in enumerate(devices):
                if dev["hostapi"] != wasapi_api_index:
                    continue

                source_id = str(i)
                dev_name = dev["name"]
                display_name = dev_name

                # In sounddevice WASAPI, render endpoints (outputs) have max_output_channels > 0
                # Capture endpoints (inputs) have max_input_channels > 0
                # For loopback, we're interested in render endpoints that we can capture from

                if dev["max_output_channels"] > 0:
                    # This is a render endpoint (Output)
                    source_type = SourceType.SYSTEM_AUDIO
                    # If it's the default output, it might be labeled as such
                    if "default" in display_name.lower():
                        source_type = SourceType.SYSTEM_AUDIO

                    render_sources.append(
                        SourceDescriptor(
                            source_id=source_id,
                            source_type=source_type,
                            display_name=display_name,
                            platform="windows",
                            capabilities=SourceCapabilities(
                                sample_rates=[int(dev["default_samplerate"]), 48000],
                                channels=[2],
                                bit_depths=[16],
                            ),
                        )
                    )
                elif dev["max_input_channels"] > 0:
                    # This is a capture endpoint (Input)
                    capture_sources.append(
                        SourceDescriptor(
                            source_id=source_id,
                            source_type=SourceType.MICROPHONE,
                            display_name=display_name,
                            platform="windows",
                            capabilities=SourceCapabilities(
                                sample_rates=[int(dev["default_samplerate"]), 48000],
                                channels=[min(2, dev["max_input_channels"])],
                                bit_depths=[16],
                            ),
                        )
                    )

            # Prioritize: SYSTEM_AUDIO (already first), then other render (SYSTEM_AUDIO), then capture (MICROPHONE)
            # Filter out "default" if it was discovered to avoid duplicates if we want,
            # but keeping it simple: just append them in order.
            sources.extend(render_sources)
            sources.extend(capture_sources)

        except Exception as e:
            logger.error(f"Error listing Windows audio sources: {e}")

        return sources

    def prepare(self, source_id: str) -> PrepareResult:
        if platform.system() != "Windows":
            return PrepareResult(success=False, source_id=source_id, error="Not on Windows platform")

        sd = _get_sd()
        if sd is None:
            return PrepareResult(
                success=False,
                source_id=source_id,
                error="sounddevice library or PortAudio missing",
                code="windows_audio_library_misconfigured",
            )

        if source_id == "default":
            return PrepareResult(success=True, source_id=source_id)

        try:
            device_index = int(source_id)
            sd.query_devices(device_index)
            return PrepareResult(success=True, source_id=source_id)
        except (ValueError, Exception) as e:
            return PrepareResult(success=False, source_id=source_id, error=str(e), code="windows_output_device_not_found")

    def start(self, source_id: str, frame_sink: FrameSink) -> StartResult:
        if self._running:
            return StartResult(success=False, message="Already running")

        sd = _get_sd()
        if sd is None:
            return StartResult(
                success=False,
                message="sounddevice library or PortAudio missing",
                code="windows_audio_library_misconfigured",
            )

        self._frame_sink = frame_sink
        self._session_id = f"win_sess_{int(time.time())}"

        # Determine source type to decide on loopback
        source_type = SourceType.SYSTEM_AUDIO
        if source_id != "default":
            try:
                sources = self.list_sources()
                matching = [s for s in sources if s.source_id == source_id]
                if matching:
                    source_type = matching[0].source_type
                else:
                    return StartResult(success=False, message=f"Source {source_id} not found", code="windows_output_device_not_found")
            except Exception:
                pass  # Fallback to SYSTEM_AUDIO if discovery fails

        try:
            device_index = None if source_id == "default" else int(source_id)

            # If not using "default", we must determine if it's a loopback device or a microphone
            is_loopback = True
            if device_index is not None:
                dev_info = sd.query_devices(device_index)
                # If it's a render endpoint, we want loopback
                is_loopback = dev_info["max_output_channels"] > 0

            # WASAPI Loopback requires specific settings in sounddevice/PortAudio
            extra_settings = None
            if is_loopback:
                try:
                    extra_settings = sd.WasapiSettings(loopback=True)
                except (AttributeError, TypeError):
                    logger.warning("sd.WasapiSettings doesn't support loopback=True, attempting fallback")

            try:
                self._stream = sd.InputStream(
                    device=device_index,
                    channels=2,
                    samplerate=48000,
                    dtype="int16",
                    extra_settings=extra_settings,
                    callback=self._audio_callback,
                )
                self._stream.start()
            except Exception as e:
                err_msg = str(e).lower()
                code = "source_start_failed"
                if "invalid device" in err_msg or "device not found" in err_msg:
                    code = "windows_output_device_not_found"
                elif "access denied" in err_msg or "permission" in err_msg:
                    code = "windows_output_device_access_denied"
                elif source_type == SourceType.SYSTEM_AUDIO:
                    if "not supported" in err_msg or "format" in err_msg:
                        code = "windows_loopback_not_supported"
                    else:
                        code = "windows_loopback_start_failed"

                logger.error(f"Failed to start Windows audio capture: {e}")
                self._last_error = str(e)
                return StartResult(success=False, message=str(e), code=code)

            self._running = True
            self._start_time = time.time()
            self._samples_captured = 0
            self._last_samples_captured = 0

            logger.info(f"Started Windows audio capture on device {source_id} ({source_type})")
            return StartResult(success=True, session_id=self._session_id)

        except Exception as e:
            logger.error(f"Unexpected error starting Windows audio capture: {e}")
            self._last_error = str(e)
            return StartResult(success=False, message=str(e))

    def stop(self, session_id: str) -> None:
        if self._session_id != session_id:
            return

        self._running = False
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as e:
                logger.error(f"Error closing Windows audio stream: {e}")
            self._stream = None

        self._session_id = None
        self._frame_sink = None
        logger.info(f"Stopped Windows audio session {session_id}")

    def probe_health(self, source_id: str) -> HealthResult:
        # Check if samples are actually being captured to determine signal presence
        signal_present = self._running and self._samples_captured > self._last_samples_captured
        self._last_samples_captured = self._samples_captured

        return HealthResult(
            healthy=self._running and self._last_error is None,
            source_state="active" if self._running else "idle",
            signal_present=signal_present,
            dropped_frames=self._dropped_frames,
            last_error=self._last_error,
        )

    def start_pairing(self, timeout_seconds: int = 60, candidate_mac: str | None = None) -> PairingResult:
        return PairingResult(success=False, error="Pairing not supported")

    def stop_pairing(self) -> PairingResult:
        return PairingResult(success=True)

    def _audio_callback(self, indata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:
        """Audio capture callback, ensuring canonical format: PCM s16le, 48kHz, stereo."""
        if not self._running or not self._frame_sink:
            return

        if status:
            logger.warning(f"Sounddevice status: {status}")
            if status.input_overflow:
                self._dropped_frames += 1

        try:
            # Ensure we are in 48kHz stereo signed 16-bit.
            # InputStream is already configured for samplerate=48000, channels=2, dtype='int16'.
            # indata is expected to be (frames, 2) shaped numpy array.

            # If the input was mono or had different channel count, we would normalize here.
            # sounddevice should handle the conversion based on our InputStream config,
            # but we explicitly ensure the layout if needed.

            # Ensure it is exactly 2 channels (stereo)
            if indata.ndim == 1:
                # Mono to stereo: duplicate
                data_stereo = np.column_stack((indata, indata))
            elif indata.shape[1] == 1:
                # Mono to stereo: duplicate
                data_stereo = np.column_stack((indata[:, 0], indata[:, 0]))
            elif indata.shape[1] > 2:
                # Multi-channel to stereo: take first two channels
                data_stereo = indata[:, :2].copy()
            else:
                data_stereo = indata

            # Convert to little-endian bytes if not already
            if data_stereo.dtype.byteorder == ">" or (data_stereo.dtype.byteorder == "=" and sys.byteorder == "big"):
                data_bytes = data_stereo.astype("<i2").tobytes()
            else:
                data_bytes = data_stereo.tobytes()

            # pts_ns and duration_ns
            pts_ns = int(self._samples_captured * 1_000_000_000 / 48000)
            duration_ns = int(frames * 1_000_000_000 / 48000)

            self._frame_sink.on_frame(data_bytes, pts_ns, duration_ns)
            self._samples_captured += frames
        except Exception as e:
            logger.error(f"Error in Windows audio callback: {e}")
            self._last_error = str(e)
            if self._frame_sink:
                self._frame_sink.on_error(e)
