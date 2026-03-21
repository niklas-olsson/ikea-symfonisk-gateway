"""Internal protocol for Windows system-output backends."""

from __future__ import annotations

from typing import Protocol

from ingress_sdk.base import FrameSink
from ingress_sdk.types import HealthResult, PrepareResult, SourceDescriptor, StartResult

from .models import BackendProbeResult


class WindowsSystemAudioBackend(Protocol):
    """Internal sync interface used by the Windows adapter."""

    def name(self) -> str: ...

    def probe(self) -> BackendProbeResult: ...

    def list_sources(self) -> list[SourceDescriptor]: ...

    def prepare(self, local_source_id: str) -> PrepareResult: ...

    def start(self, local_source_id: str, frame_sink: FrameSink) -> StartResult: ...

    def stop(self, session_id: str) -> None: ...

    def probe_health(self, local_source_id: str) -> HealthResult: ...

    def get_diagnostics_snapshot(self) -> dict[str, object]: ...
