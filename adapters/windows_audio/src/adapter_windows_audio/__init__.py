"""Windows audio (WASAPI loopback) ingress adapter.

Captures system audio using Windows WASAPI loopback via sounddevice.
Emits canonical PCM 48kHz stereo frames.
"""

import asyncio
import logging
import platform
import time
from typing import Any

import numpy as np

try:
    import sounddevice as sd
except ImportError:
    sd = None

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


class WindowsAudioAdapter(IngressAdapter):
    """Adapter for capturing system audio on Windows."""

    def __init__(self) -> None:
        self._session_id: str | None = None
        self._running = False
        self._stream: sd.InputStream | None = None
        self._frame_sink: FrameSink | None = None
        self._start_time: float = 0
        self._samples_captured: int = 0
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
        if platform.system() != "Windows" or sd is None:
            # If not on Windows, we can't really discover WASAPI devices
            # But for the sake of the exercise, we return an empty list or a mock if in dev
            if platform.system() != "Windows":
                logger.warning("WindowsAudioAdapter listed on non-Windows platform")
            return []

        sources = []
        try:
            devices = sd.query_devices()
            hostapis = sd.query_hostapis()

            wasapi_api_index = -1
            for i, api in enumerate(hostapis):
                if api["name"] == "Windows WASAPI":
                    wasapi_api_index = i
                    break

            for i, dev in enumerate(devices):
                # We are looking for WASAPI devices
                if dev["hostapi"] != wasapi_api_index:
                    continue

                source_id = str(i)
                display_name = dev["name"]

                # In WASAPI, loopback devices are often marked or can be inferred
                # sounddevice usually shows them as separate devices if supported
                if dev["max_input_channels"] > 0:
                    source_type = SourceType.MICROPHONE
                    if "loopback" in display_name.lower() or "what u hear" in display_name.lower():
                        source_type = SourceType.SYSTEM_AUDIO

                    sources.append(
                        SourceDescriptor(
                            source_id=source_id,
                            source_type=source_type,
                            display_name=display_name,
                            platform="windows",
                            capabilities=SourceCapabilities(
                                sample_rates=[int(dev["default_samplerate"]), 48000],
                                channels=[min(2, dev["max_input_channels"])],
                                bit_depths=[16],
                            ),
                        )
                    )
        except Exception as e:
            logger.error(f"Error listing Windows audio sources: {e}")

        # If no loopback found but we are on Windows, provide a "Default" entry
        if not sources and platform.system() == "Windows":
            sources.append(
                SourceDescriptor(
                    source_id="default",
                    source_type=SourceType.SYSTEM_AUDIO,
                    display_name="Default System Audio (WASAPI)",
                    platform="windows",
                    capabilities=SourceCapabilities(
                        sample_rates=[48000],
                        channels=[2],
                        bit_depths=[16],
                    ),
                )
            )

        return sources

    def prepare(self, source_id: str) -> PrepareResult:
        if platform.system() != "Windows":
            return PrepareResult(success=False, source_id=source_id, error="Not on Windows platform")

        if source_id == "default":
            return PrepareResult(success=True, source_id=source_id)

        try:
            device_index = int(source_id)
            sd.query_devices(device_index)
            return PrepareResult(success=True, source_id=source_id)
        except (ValueError, Exception) as e:
            return PrepareResult(success=False, source_id=source_id, error=str(e))

    def start(self, source_id: str, frame_sink: FrameSink) -> StartResult:
        if self._running:
            return StartResult(success=False, message="Already running")

        self._frame_sink = frame_sink
        self._session_id = f"win_sess_{int(time.time())}"

        try:
            device_index = None if source_id == "default" else int(source_id)

            # WASAPI Loopback requires specific settings in sounddevice/PortAudio
            # On Windows, we often need to use sd.WasapiSettings
            extra_settings = None
            if platform.system() == "Windows":
                try:
                    extra_settings = sd.WasapiSettings(loopback=True)
                except AttributeError:
                    # Older sounddevice or non-Windows
                    pass

            self._stream = sd.InputStream(
                device=device_index,
                channels=2,
                samplerate=48000,
                dtype="int16",
                extra_settings=extra_settings,
                callback=self._audio_callback,
            )
            self._stream.start()
            self._running = True
            self._start_time = time.time()
            self._samples_captured = 0

            logger.info(f"Started Windows audio capture on device {source_id}")
            return StartResult(success=True, session_id=self._session_id)

        except Exception as e:
            logger.error(f"Failed to start Windows audio capture: {e}")
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
        return HealthResult(
            healthy=self._running and self._last_error is None,
            source_state="active" if self._running else "idle",
            signal_present=self._running,
            dropped_frames=self._dropped_frames,
            last_error=self._last_error,
        )

    def start_pairing(self, timeout_seconds: int = 60) -> PairingResult:
        return PairingResult(success=False, error="Pairing not supported")

    def stop_pairing(self) -> PairingResult:
        return PairingResult(success=True)

    def _audio_callback(self, indata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:
        if not self._running or not self._frame_sink:
            return

        if status:
            logger.warning(f"Sounddevice status: {status}")
            if status.input_overflow:
                self._dropped_frames += 1

        try:
            # indata is already int16 because of dtype='int16'
            # Canonical PCM: signed 16-bit LE, 48kHz, stereo
            # sounddevice provides a numpy array. We convert to bytes.
            data = indata.tobytes()

            # pts_ns and duration_ns
            pts_ns = int(self._samples_captured * 1_000_000_000 / 48000)
            duration_ns = int(frames * 1_000_000_000 / 48000)

            self._frame_sink.on_frame(data, pts_ns, duration_ns)
            self._samples_captured += frames
        except Exception as e:
            logger.error(f"Error in Windows audio callback: {e}")
            self._last_error = str(e)
            self._frame_sink.on_error(e)
