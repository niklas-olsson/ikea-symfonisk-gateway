"""Linux Bluetooth (BlueALSA) ingress adapter.

Captures audio from Bluetooth A2DP sources (turntables, phones) via PulseAudio/BlueZ.
Emits canonical PCM 48kHz stereo frames.
"""

import asyncio
import logging
import shutil
import subprocess
from typing import Any

from bridge_core.core.event_bus import EventBus, EventType
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

from .dbus_adapter import BlueZAdapterController
from .store import TrustedDeviceStore
from .window import PairingWindowManager

logger = logging.getLogger(__name__)


class LinuxBluetoothAdapter(IngressAdapter):
    """Adapter for capturing Bluetooth audio on Linux."""

    def __init__(self, event_bus: EventBus | None = None) -> None:
        self._event_bus = event_bus
        self._session_id: str | None = None
        self._running = False
        self._process: asyncio.subprocess.Process | None = None
        self._capture_task: asyncio.Task[None] | None = None
        self._frame_sink: FrameSink | None = None

        self._store = TrustedDeviceStore()
        self._adapter_controller = BlueZAdapterController()
        self._pairing_window = PairingWindowManager(self._adapter_controller, self._store, event_bus)

        self._status_monitoring_task: asyncio.Task[None] | None = None
        self._last_status: dict[str, Any] = {}
        self._source_id_map: dict[str, str] = {}  # virtual_id -> pa_id

        if self._event_bus:
            self._status_monitoring_task = asyncio.create_task(self._monitor_status())

    def id(self) -> str:
        return "linux-bluetooth-adapter"

    def platform(self) -> str:
        return "linux"

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            supports_system_audio=False,
            supports_bluetooth_audio=True,
            supports_line_in=False,
            supports_microphone=False,
            supports_file_replay=False,
            supports_synthetic_test_source=False,
            supports_sample_rates=[48000],
            supports_channels=[2],
            supports_hotplug_events=True,
            supports_pairing=True,
        )

    def list_sources(self) -> list[SourceDescriptor]:
        """Discover connected Bluetooth A2DP sources using pactl."""
        sources: list[SourceDescriptor] = []
        if not shutil.which("pactl"):
            return sources

        new_source_id_map: dict[str, str] = {}

        try:
            backend_type = "PulseAudio"
            if shutil.which("pw-cli"):
                backend_type = "PipeWire"

            result = subprocess.run(["pactl", "list", "short", "sources"], capture_output=True, text=True, check=True)
            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue
                parts = line.split("\t")
                if len(parts) < 2:
                    continue

                source_id = parts[1]
                if "bluez" in source_id:
                    mac = source_id.replace("bluez_source.", "").replace(".a2dp_source", "").replace("_", ":")
                    if self._store.is_blocked(mac):
                        logger.info(f"Ignoring blocked Bluetooth source: {source_id}")
                        continue

                    display_name = f"Bluetooth: {mac}"
                    metadata = self._store.get_device_metadata(mac)
                    if metadata is None:
                        metadata = {}
                    if "alias" in metadata:
                        display_name = f"Bluetooth: {metadata['alias']} ({mac})"

                    virtual_id = f"bluetooth:{mac.lower()}"
                    metadata["backend_type"] = backend_type
                    metadata["pa_source_id"] = source_id

                    sources.append(
                        SourceDescriptor(
                            source_id=virtual_id,
                            source_type=SourceType.BLUETOOTH_AUDIO,
                            display_name=display_name,
                            platform="linux",
                            capabilities=SourceCapabilities(
                                sample_rates=[48000],
                                channels=[2],
                                bit_depths=[16],
                            ),
                            metadata=metadata,
                        )
                    )
                    new_source_id_map[virtual_id] = source_id

            self._source_id_map = new_source_id_map

        except (subprocess.SubprocessError, FileNotFoundError) as e:
            logger.error(f"Failed to list Bluetooth sources: {e}")

        return sources

    def prepare(self, source_id: str) -> PrepareResult:
        """Verify the specified Bluetooth source exists."""
        sources = self.list_sources()
        for s in sources:
            if s.source_id == source_id:
                return PrepareResult(success=True, source_id=source_id)

        return PrepareResult(success=False, source_id=source_id, error=f"Bluetooth source {source_id} not found")

    def start(self, source_id: str, frame_sink: FrameSink) -> StartResult:
        """Start capturing audio from the Bluetooth source."""
        if self._running:
            return StartResult(success=False, message="Already running")

        pa_source_id = self._source_id_map.get(source_id, source_id)

        self._session_id = f"sess_{id(self)}"
        self._frame_sink = frame_sink
        self._running = True
        self._capture_task = asyncio.create_task(self._capture_loop(pa_source_id))

        logger.info(f"Started Bluetooth capture from {source_id} ({pa_source_id}) (session: {self._session_id})")
        return StartResult(success=True, session_id=self._session_id)

    def stop(self, session_id: str) -> None:
        """Stop the active Bluetooth capture session."""
        if self._session_id != session_id:
            return

        self._running = False

        if self._capture_task:
            self._capture_task.cancel()
            self._capture_task = None

        self._session_id = None
        self._frame_sink = None

        logger.info(f"Stopped Bluetooth capture session {session_id}")

    def probe_health(self, source_id: str) -> HealthResult:
        """Probe the health of the Bluetooth source."""
        return HealthResult(
            healthy=self._running,
            source_state="active" if self._running else "idle",
            signal_present=self._running,
            dropped_frames=0,
            last_error=None,
        )

    async def _capture_loop(self, source_id: str) -> None:
        """Audio capture loop using parec."""
        if not shutil.which("parec"):
            logger.error("parec not found, cannot capture audio")
            self._running = False
            return

        cmd = ["parec", f"--device={source_id}", "--format=s16le", "--rate=48000", "--channels=2"]

        try:
            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            if self._process.stdout is None:
                logger.error("Failed to open stdout for capture process")
                return

            chunk_size = 1920  # 10ms at 48kHz, 16-bit stereo
            pts_ns = 0
            duration_ns = 10_000_000  # 10ms

            while self._running:
                try:
                    data = await self._process.stdout.readexactly(chunk_size)
                    if not data:
                        break

                    if self._frame_sink:
                        try:
                            self._frame_sink.on_frame(data, pts_ns, duration_ns)
                        except Exception as e:
                            logger.error(f"Error in frame sink: {e}")
                            self._frame_sink.on_error(e)

                    pts_ns += duration_ns
                except asyncio.IncompleteReadError:
                    break

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error capturing Bluetooth audio: {e}")
            if self._frame_sink:
                self._frame_sink.on_error(e)
        finally:
            self._running = False
            if self._process:
                try:
                    self._process.terminate()
                    await self._process.wait()
                except ProcessLookupError:
                    pass
                self._process = None

    def start_pairing(self, timeout_seconds: int = 90, candidate_mac: str | None = None) -> PairingResult:
        """Start adapter pairing mode via Window Manager."""
        asyncio.create_task(self._pairing_window.open_window(timeout_seconds, candidate_mac=candidate_mac))
        return PairingResult(success=True, message=f"Pairing window opened for {timeout_seconds}s")

    def stop_pairing(self) -> PairingResult:
        """Stop adapter pairing mode."""
        asyncio.create_task(self._pairing_window.close_window())
        return PairingResult(success=True, message="Pairing window closed")

    async def get_adapter_status(self) -> dict[str, Any]:
        """Get status of the Bluetooth adapter."""
        props = await self._adapter_controller.get_properties()
        errors = await self._adapter_controller.check_readiness()
        return {
            "properties": props,
            "readiness_errors": errors,
            "healthy": len(errors) == 0,
        }

    async def set_adapter_alias(self, alias: str) -> bool:
        """Set the adapter alias."""
        return await self._adapter_controller.set_alias(alias)

    async def set_discoverable(self, enabled: bool, timeout: int = 0) -> bool:
        """Set discoverable mode."""
        if enabled and timeout > 0:
            await self._adapter_controller.set_discoverable_timeout(timeout)
        return await self._adapter_controller.set_discoverable(enabled)

    async def set_pairable(self, enabled: bool, timeout: int = 0) -> bool:
        """Set pairable mode."""
        if enabled and timeout > 0:
            await self._adapter_controller.set_pairable_timeout(timeout)
        return await self._adapter_controller.set_pairable(enabled)

    def trust_device(self, mac: str, alias: str | None = None) -> None:
        """Add device to trusted store."""
        metadata: dict[str, Any] = {}
        try:
            metadata = {"timestamp": asyncio.get_event_loop().time()}
        except RuntimeError:
            metadata = {"timestamp": 0}
        if alias:
            metadata["alias"] = alias
        self._store.trust_device(mac, metadata)

    def forget_device(self, mac: str) -> None:
        """Remove device from trusted store."""
        self._store.forget_device(mac)

    async def _monitor_status(self) -> None:
        """Periodically check adapter status and emit events on change."""
        try:
            while True:
                status = await self.get_adapter_status()
                if status != self._last_status:
                    if self._event_bus:
                        self._event_bus.emit(
                            EventType.BLUETOOTH_ADAPTER_STATUS_CHANGED,
                            payload=status,
                        )
                    self._last_status = status
                await asyncio.sleep(5)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error monitoring Bluetooth status: {e}")
