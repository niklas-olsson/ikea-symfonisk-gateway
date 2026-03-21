"""Linux Bluetooth (BlueALSA) ingress adapter.

Captures audio from Bluetooth A2DP sources (turntables, phones) via PulseAudio/BlueZ.
Emits canonical PCM 48kHz stereo frames.
"""

import asyncio
import logging
import platform
import shutil
import subprocess
from typing import Any, Protocol, cast

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

from .store import TrustedDeviceStore


class _BlueZAdapterController(Protocol):
    async def get_properties(self) -> dict[str, Any]: ...

    async def check_readiness(self) -> list[str]: ...

    async def is_available(self) -> bool: ...

    async def set_alias(self, alias: str) -> bool: ...

    async def set_discoverable_timeout(self, timeout: int) -> None: ...

    async def set_discoverable(self, enabled: bool) -> bool: ...

    async def set_pairable_timeout(self, timeout: int) -> None: ...

    async def set_pairable(self, enabled: bool) -> bool: ...

    async def get_managed_objects(self) -> dict[str, dict[str, dict[str, Any]]]: ...

    def get_device_path(self, mac: str) -> str: ...

    async def connect_device(self, device_path: str) -> bool: ...

    async def disconnect_device(self, device_path: str) -> bool: ...

    async def remove_device(self, device_path: str) -> None: ...


class _PairingWindowManager(Protocol):
    is_open: bool
    agent_path: str | None

    async def open_window(self, timeout_seconds: int, candidate_mac: str | None = None) -> None: ...

    async def close_window(self) -> None: ...

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

        is_linux = platform.system() == "Linux"
        self._adapter_controller: _BlueZAdapterController | None = None
        self._pairing_window: _PairingWindowManager | None = None
        if is_linux:
            from .dbus_adapter import BlueZAdapterController
            from .window import PairingWindowManager

            self._adapter_controller = cast(_BlueZAdapterController, BlueZAdapterController())
            self._pairing_window = cast(
                _PairingWindowManager,
                PairingWindowManager(cast(Any, self._adapter_controller), self._store, event_bus),
            )

        self._status_monitoring_task: asyncio.Task[None] | None = None
        self._last_status: dict[str, Any] = {}
        self._source_id_map: dict[str, str] = {}  # virtual_id -> pa_id

        if self._event_bus and is_linux:
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

    async def on_startup(self) -> None:
        """Attempt to reconnect trusted/preferred devices on startup."""
        if not self._adapter_controller:
            return

        logger.info("Bluetooth adapter: starting auto-reconnect routine...")

        # Wait for adapter to be available
        for _ in range(10):
            if await self._adapter_controller.is_available():
                break
            await asyncio.sleep(1)
        else:
            logger.error("Bluetooth adapter not available for auto-reconnect")
            return

        trusted = self._store.list_trusted()
        preferred_mac = self._store.get_preferred_device()

        # Prioritize preferred device
        macs_to_connect = []
        if preferred_mac and preferred_mac in trusted:
            macs_to_connect.append(preferred_mac)

        for mac in trusted:
            if mac != preferred_mac:
                macs_to_connect.append(mac)

        for mac in macs_to_connect:
            device_path = self._adapter_controller.get_device_path(mac)
            logger.info(f"Attempting auto-reconnect to {mac} ({device_path})")
            success = await self._adapter_controller.connect_device(device_path)
            if success:
                logger.info(f"Successfully auto-reconnected to {mac}")
            else:
                logger.debug(f"Auto-reconnect failed for {mac} (maybe not in range)")

    def list_sources(self) -> list[SourceDescriptor]:
        """Discover connected Bluetooth A2DP sources using pactl."""
        sources: list[SourceDescriptor] = []
        if not shutil.which("pactl"):
            return sources

        new_source_id_map: dict[str, str] = {}

        try:
            backend_type = "PulseAudio"
            default_source = "auto_null.monitor"
            info_res = subprocess.run(["pactl", "info"], capture_output=True, text=True)
            for line in info_res.stdout.split("\n"):
                if "PipeWire" in line:
                    backend_type = "PipeWire"
                if line.startswith("Default Source:"):
                    default_source = line.split(":", 1)[1].strip()

            macs_found = set()

            # 1. Check for explicit pulse sources
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
                    macs_found.add((mac.upper(), source_id))

            # 2. Check for explicit pulse cards (PipeWire Loopback fallback)
            if backend_type == "PipeWire":
                res = subprocess.run(["pactl", "list", "short", "cards"], capture_output=True, text=True)
                for line in res.stdout.strip().split("\n"):
                    if not line:
                        continue
                    parts = line.split("\t")
                    if len(parts) >= 2 and "bluez_card." in parts[1]:
                        cid = parts[1]
                        mac = cid.replace("bluez_card.", "").replace("_", ":")
                        # Add it with the default source monitor
                        macs_found.add((mac.upper(), default_source))

            for mac, pa_source in macs_found:
                if self._store.is_blocked(mac):
                    continue

                virtual_id = f"bluetooth:{mac.lower()}"

                # If we already have this virtual_id, prefer the direct source over the default monitor fallback
                if virtual_id in new_source_id_map and new_source_id_map[virtual_id] != default_source:
                    continue

                display_name = f"Bluetooth: {mac}"
                metadata = self._store.get_device_metadata(mac) or {}
                if "alias" in metadata:
                    display_name = f"Bluetooth: {metadata['alias']} ({mac})"

                metadata["backend_type"] = backend_type
                metadata["pa_source_id"] = pa_source

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
                new_source_id_map[virtual_id] = pa_source

            # Check for available/unavailable sources
            if self._event_bus:
                old_sources = set(self._source_id_map.keys())
                new_sources = set(new_source_id_map.keys())

                for sid in new_sources - old_sources:
                    self._event_bus.emit(EventType.BLUETOOTH_SOURCE_AVAILABLE, payload={"source_id": sid})
                for sid in old_sources - new_sources:
                    self._event_bus.emit(EventType.BLUETOOTH_SOURCE_UNAVAILABLE, payload={"source_id": sid})

                if new_sources != old_sources:
                    self._event_bus.emit(EventType.TOPOLOGY_CHANGED, payload={"adapter_id": self.id()})

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
            stdout = self._process.stdout
            stderr = self._process.stderr
            if stdout is None or stderr is None:
                logger.error("parec did not provide stdout/stderr pipes")
                self._running = False
                return

            async def log_stderr(stderr: asyncio.StreamReader) -> None:
                while True:
                    line = await stderr.readline()
                    if not line:
                        break
                    logger.error(f"parec stderr: {line.decode().strip()}")

            stderr_task = asyncio.create_task(log_stderr(stderr))

            chunk_size = 1920  # 10ms at 48kHz, 16-bit stereo
            pts_ns = 0
            duration_ns = 10_000_000  # 10ms

            frame_count = 0
            while self._running:
                try:
                    data = await stdout.readexactly(chunk_size)
                    if not data:
                        break

                    frame_count += 1
                    if frame_count % 100 == 0:
                        logger.info(f"Bluetooth capture: {frame_count} frames sent to sink")

                    if self._frame_sink:
                        try:
                            self._frame_sink.on_frame(data, pts_ns, duration_ns)
                        except Exception as e:
                            logger.error(f"Error in frame sink: {e}")
                            self._frame_sink.on_error(e)

                    pts_ns += duration_ns
                except asyncio.IncompleteReadError:
                    break
                except Exception as e:
                    logger.error(f"Error reading from parec: {e}")
                    break

            stderr_task.cancel()

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
        if self._pairing_window is None:
            return PairingResult(success=False, error="Bluetooth not available on this platform")
        assert self._pairing_window is not None
        asyncio.create_task(self._pairing_window.open_window(timeout_seconds, candidate_mac=candidate_mac))
        return PairingResult(success=True, message=f"Pairing window opened for {timeout_seconds}s")

    def stop_pairing(self) -> PairingResult:
        """Stop adapter pairing mode."""
        if self._pairing_window is None:
            return PairingResult(success=False, error="Bluetooth not available on this platform")
        assert self._pairing_window is not None
        asyncio.create_task(self._pairing_window.close_window())
        return PairingResult(success=True, message="Pairing window closed")

    async def get_adapter_status(self) -> dict[str, Any]:
        """Get status of the Bluetooth adapter."""
        if self._adapter_controller is None:
            return {
                "adapter_id": self.id(),
                "healthy": False,
                "error": "Bluetooth not available on this platform",
                "trusted_devices": self._store.list_trusted(),
            }

        props = await self._adapter_controller.get_properties()
        errors = await self._adapter_controller.check_readiness()

        backend_type = "PulseAudio"
        if shutil.which("pw-cli"):
            backend_type = "PipeWire"

        return {
            "adapter_id": self.id(),
            "properties": props,
            "readiness_errors": errors,
            "healthy": len(errors) == 0,
            "pairing_window": {
                "is_open": self._pairing_window.is_open if self._pairing_window else False,
                "agent_path": self._pairing_window.agent_path if self._pairing_window else None,
            },
            "backend": {
                "type": backend_type,
                "profile": "a2dp_source",
            },
            "trusted_devices": self._store.list_trusted(),
            "preferred_device": self._store.get_preferred_device(),
        }

    async def set_adapter_alias(self, alias: str) -> bool:
        """Set the adapter alias."""
        if self._adapter_controller is None:
            return False
        return await self._adapter_controller.set_alias(alias)

    async def set_discoverable(self, enabled: bool, timeout: int = 0) -> bool:
        """Set discoverable mode."""
        if self._adapter_controller is None:
            return False
        if enabled and timeout > 0:
            await self._adapter_controller.set_discoverable_timeout(timeout)
        return await self._adapter_controller.set_discoverable(enabled)

    async def set_pairable(self, enabled: bool, timeout: int = 0) -> bool:
        """Set pairable mode."""
        if self._adapter_controller is None:
            return False
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
        if self._event_bus:
            self._event_bus.emit(EventType.BLUETOOTH_DEVICE_TRUSTED, payload={"mac": mac, "alias": alias})

    def forget_device(self, mac: str) -> None:
        """Remove device from trusted store."""
        self._store.forget_device(mac)
        if self._event_bus:
            self._event_bus.emit(EventType.BLUETOOTH_DEVICE_UNTRUSTED, payload={"mac": mac})

    async def list_devices(self) -> list[dict[str, Any]]:
        """List all known Bluetooth devices."""
        if self._adapter_controller is None:
            return []
        objects = await self._adapter_controller.get_managed_objects()
        devices = []
        for path, interfaces in objects.items():
            if "org.bluez.Device1" in interfaces:
                props = interfaces["org.bluez.Device1"]
                mac = props.get("Address", "")
                devices.append(
                    {
                        "mac": mac,
                        "name": props.get("Name", props.get("Alias", "Unknown")),
                        "alias": props.get("Alias"),
                        "paired": props.get("Paired", False),
                        "trusted": props.get("Trusted", False) or self._store.is_trusted(mac),
                        "connected": props.get("Connected", False),
                        "blocked": props.get("Blocked", False) or self._store.is_blocked(mac),
                        "rssi": props.get("RSSI"),
                        "path": path,
                    }
                )
        return devices

    async def connect_device(self, mac: str) -> bool:
        """Connect to a Bluetooth device."""
        if self._adapter_controller is None:
            return False
        device_path = self._adapter_controller.get_device_path(mac)
        if self._event_bus:
            self._event_bus.emit(EventType.BLUETOOTH_DEVICE_CONNECTING, payload={"mac": mac})

        try:
            success = await self._adapter_controller.connect_device(device_path)
            if success:
                if self._event_bus:
                    self._event_bus.emit(EventType.BLUETOOTH_DEVICE_CONNECTED, payload={"mac": mac})
                return True
            return False
        except Exception as e:
            if self._event_bus:
                self._event_bus.emit(EventType.BLUETOOTH_BACKEND_ERROR, payload={"mac": mac, "error": str(e)})
            return False

    async def disconnect_device(self, mac: str) -> bool:
        """Disconnect from a Bluetooth device."""
        if self._adapter_controller is None:
            return False
        device_path = self._adapter_controller.get_device_path(mac)
        success = await self._adapter_controller.disconnect_device(device_path)

        if success and self._event_bus:
            self._event_bus.emit(EventType.BLUETOOTH_DEVICE_DISCONNECTED, payload={"mac": mac})
        return success

    async def remove_device(self, mac: str) -> bool:
        """Remove (unpair) and forget a Bluetooth device."""
        if self._adapter_controller is not None:
            device_path = self._adapter_controller.get_device_path(mac)
            await self._adapter_controller.remove_device(device_path)
        self.forget_device(mac)
        return True

    def get_preferred_device(self) -> str | None:
        """Get the preferred device MAC."""
        return self._store.get_preferred_device()

    def set_preferred_device(self, mac: str | None) -> None:
        """Set the preferred device MAC."""
        self._store.set_preferred_device(mac)

    async def _monitor_status(self) -> None:
        """Periodically check adapter status and emit events on change."""
        try:
            while True:
                # Discover sources periodically to automatically emit BLUETOOTH_SOURCE_AVAILABLE
                await asyncio.to_thread(self.list_sources)

                status = await self.get_adapter_status()

                # Check for readiness errors to emit READY/FAILED events
                if status["healthy"] and not self._last_status.get("healthy", False):
                    if self._event_bus:
                        self._event_bus.emit(EventType.BLUETOOTH_ADAPTER_READY)
                elif not status["healthy"] and self._last_status.get("healthy", True):
                    if self._event_bus:
                        self._event_bus.emit(EventType.BLUETOOTH_ADAPTER_FAILED, payload={"errors": status["readiness_errors"]})

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
            if self._event_bus:
                self._event_bus.emit(EventType.BLUETOOTH_BACKEND_ERROR, payload={"error": str(e)})
