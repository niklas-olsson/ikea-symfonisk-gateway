"""Linux Bluetooth (BlueALSA) ingress adapter.

Captures A2DP Bluetooth audio streams via BlueALSA and handles pairing via D-Bus BlueZ API.
"""

from uuid import uuid4

from ingress_sdk.base import FrameSink, IngressAdapter
from ingress_sdk.types import (
    AdapterCapabilities,
    HealthResult,
    PrepareResult,
    SourceCapabilities,
    SourceDescriptor,
    SourceType,
    StartResult,
)


class LinuxBluetoothAdapter(IngressAdapter):
    """Adapter for Linux Bluetooth audio using BlueALSA."""

    def __init__(self) -> None:
        self._id = f"linux-bluetooth-{uuid4().hex[:8]}"
        self._session_id: str | None = None
        self._running = False
        self._frame_sink: FrameSink | None = None
        self._source_id = f"bluetooth:{uuid4().hex[:8]}"

    def id(self) -> str:
        return self._id

    def platform(self) -> str:
        return "linux"

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            supports_bluetooth_audio=True,
            supports_sample_rates=[48000],
            supports_channels=[2],
            supports_hotplug_events=True,
            supports_pairing=True,
        )

    def list_sources(self) -> list[SourceDescriptor]:
        # Return a stubbed Bluetooth source for v1
        return [
            SourceDescriptor(
                source_id=self._source_id,
                source_type=SourceType.BLUETOOTH_AUDIO,
                display_name="Bluetooth A2DP Source",
                platform="linux",
                capabilities=SourceCapabilities(
                    sample_rates=[48000],
                    channels=[2],
                    bit_depths=[16],
                ),
            )
        ]

    def prepare(self, source_id: str) -> PrepareResult:
        # Stub preparation (e.g., ensuring D-Bus/BlueZ/BlueALSA is ready)
        return PrepareResult(success=True, source_id=source_id)

    def start(self, source_id: str, frame_sink: FrameSink) -> StartResult:
        # Stub starting capture
        self._session_id = f"sess_bt_{uuid4().hex[:12]}"
        self._frame_sink = frame_sink
        self._running = True
        return StartResult(success=True, session_id=self._session_id)

    def stop(self, session_id: str) -> None:
        # Stub stopping capture
        self._running = False
        self._session_id = None
        self._frame_sink = None

    def probe_health(self, source_id: str) -> HealthResult:
        # Stub health probe
        return HealthResult(
            healthy=True,
            source_state="active" if self._running else "idle",
            signal_present=self._running,
            dropped_frames=0,
            last_error=None,
        )
