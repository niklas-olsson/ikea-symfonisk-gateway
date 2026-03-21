"""Windows audio ingress adapter backed by pluggable loopback backends."""

from __future__ import annotations

import platform

from ingress_sdk.base import FrameSink, IngressAdapter
from ingress_sdk.types import AdapterCapabilities, HealthResult, PairingResult, PrepareResult, SourceDescriptor, StartResult

from .backends import BackendProbeResult, NullWindowsBackend, PyAudioWPatchBackend, WindowsSystemAudioBackend, load_pyaudiowpatch


class WindowsAudioAdapter(IngressAdapter):
    """Adapter coordinator for Windows system-output capture."""

    def __init__(self) -> None:
        self._backend: WindowsSystemAudioBackend = self._select_backend()

    def id(self) -> str:
        return "windows-audio-adapter"

    def platform(self) -> str:
        return "windows"

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            supports_system_audio=True,
            supports_bluetooth_audio=False,
            supports_line_in=False,
            supports_microphone=False,
            supports_file_replay=False,
            supports_synthetic_test_source=False,
            supports_sample_rates=[48000],
            supports_channels=[2],
            supports_hotplug_events=False,
            supports_pairing=False,
        )

    def list_sources(self) -> list[SourceDescriptor]:
        self._refresh_backend_if_needed()
        return self._backend.list_sources()

    def prepare(self, source_id: str) -> PrepareResult:
        self._refresh_backend_if_needed()
        return self._backend.prepare(source_id)

    def start(self, source_id: str, frame_sink: FrameSink) -> StartResult:
        return self._backend.start(source_id, frame_sink)

    def stop(self, session_id: str) -> None:
        self._backend.stop(session_id)

    def probe_health(self, source_id: str) -> HealthResult:
        return self._backend.probe_health(source_id)

    def start_pairing(self, timeout_seconds: int = 60, candidate_mac: str | None = None) -> PairingResult:
        del timeout_seconds, candidate_mac
        return PairingResult(success=False, error="Pairing not supported")

    def stop_pairing(self) -> PairingResult:
        return PairingResult(success=True)

    def _refresh_backend_if_needed(self) -> None:
        probe = self._backend.probe()
        if probe.available or platform.system() != "Windows":
            return
        self._backend = self._select_backend()

    def _select_backend(self) -> WindowsSystemAudioBackend:
        if platform.system() != "Windows":
            return NullWindowsBackend(
                BackendProbeResult(
                    backend="pyaudiowpatch",
                    available=False,
                    state="unsupported_host",
                    loopback_supported=False,
                    default_output_detected=False,
                    code="windows_loopback_probe_failed",
                    message="Windows system audio capture is only available on Windows hosts.",
                )
            )

        backend_module = load_pyaudiowpatch()
        backend = PyAudioWPatchBackend(backend_module=backend_module)
        if backend.probe().available:
            return backend
        return NullWindowsBackend(backend.probe())
