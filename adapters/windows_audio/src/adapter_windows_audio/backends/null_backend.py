"""Degraded Windows backend used when loopback is unavailable."""

from __future__ import annotations

from ingress_sdk.types import HealthResult, PrepareResult, SourceCapabilities, SourceDescriptor, SourceType, StartResult

from .models import BackendProbeResult


class NullWindowsBackend:
    """Expose a degraded source and fail prepare/start with probe-derived errors."""

    def __init__(self, probe_result: BackendProbeResult) -> None:
        self._probe_result = probe_result

    def name(self) -> str:
        return self._probe_result.backend

    def probe(self) -> BackendProbeResult:
        return self._probe_result

    def list_sources(self) -> list[SourceDescriptor]:
        metadata = {
            "backend": self._probe_result.backend,
            "backend_state": self._probe_result.state,
            "loopback_supported": self._probe_result.loopback_supported,
            "default_output_detected": self._probe_result.default_output_detected,
            "status": "degraded",
            "startable": False,
            "degraded": True,
            "backend_probe": self._probe_result.model_dump(),
            "selected_host_api": None,
            "default_render_device": None,
            "loopback_device": None,
            "startup_substate": "degraded",
            "callback_count": 0,
            "non_empty_buffer_count": 0,
            "samples_received": 0,
            "frames_emitted": 0,
            "first_callback_at": None,
        }
        if self._probe_result.code:
            metadata["error_code"] = self._probe_result.code
        if self._probe_result.message:
            metadata["error_message"] = self._probe_result.message

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
        return PrepareResult(
            success=False,
            source_id=local_source_id,
            error=self._probe_result.message,
            code=self._probe_result.code,
        )

    def start(self, local_source_id: str, frame_sink: object) -> StartResult:
        del frame_sink
        return StartResult(
            success=False,
            message=self._probe_result.message or "Windows loopback backend unavailable",
            code=self._probe_result.code,
            backend=self._probe_result.backend,
        )

    def stop(self, session_id: str) -> None:
        del session_id

    def probe_health(self, local_source_id: str) -> HealthResult:
        del local_source_id
        return HealthResult(
            healthy=False,
            source_state="degraded",
            signal_present=False,
            last_error=self._probe_result.message,
            details=self.get_diagnostics_snapshot(),
        )

    def get_diagnostics_snapshot(self) -> dict[str, object]:
        diagnostics: dict[str, object] = {
            "startup_substate": "degraded",
            "callback_count": 0,
            "non_empty_buffer_count": 0,
            "samples_received": 0,
            "frames_emitted": 0,
            "first_callback_at": None,
            "selected_host_api": None,
            "default_render_device": None,
            "loopback_device": None,
            "start_viability": {
                "stream_opened": False,
                "stream_started": False,
                "callback_registered": False,
            },
        }
        if self._probe_result.code:
            diagnostics["error_code"] = self._probe_result.code
        if self._probe_result.message:
            diagnostics["error_message"] = self._probe_result.message
        return diagnostics
