"""Linux Bluetooth (BlueALSA) ingress adapter.

Captures audio from Bluetooth A2DP sources (turntables, phones) via PulseAudio/BlueZ.
Emits canonical PCM 48kHz stereo frames.
"""

import asyncio
import logging
import shutil
import subprocess
from typing import Any

from dbus_next.aio import MessageBus  # type: ignore[import-untyped]
from dbus_next.constants import BusType  # type: ignore[import-untyped]
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


class LinuxBluetoothAdapter(IngressAdapter):
    """Adapter for capturing Bluetooth audio on Linux."""

    def __init__(self, event_bus: Any | None = None, config_store: Any | None = None) -> None:
        self._event_bus = event_bus
        self._config_store = config_store
        self._session_id: str | None = None
        self._running = False
        self._process: asyncio.subprocess.Process | None = None
        self._capture_task: asyncio.Task[None] | None = None
        self._frame_sink: FrameSink | None = None
        self._pairing_timeout_task: asyncio.Task[None] | None = None
        self._monitor_task: asyncio.Task[None] | None = None
        self._reconnect_task: asyncio.Task[None] | None = None
        self._dbus_bus: MessageBus | None = None
        self._device_paths: dict[str, str] = {}  # path -> mac

        if self._event_bus:
            try:
                self._monitor_task = asyncio.create_task(self._monitor_devices())
            except RuntimeError:
                # Handle cases where there is no running event loop (e.g. some tests)
                logger.warning("No running event loop, Bluetooth monitoring not started")

    def id(self) -> str:
        return "linux-bluetooth-adapter"

    def __del__(self) -> None:
        """Cleanup background tasks on deletion."""
        if self._monitor_task:
            self._monitor_task.cancel()
        if self._reconnect_task:
            self._reconnect_task.cancel()
        if self._dbus_bus:
            try:
                # dbus-next disconnect is sync
                self._dbus_bus.disconnect()
            except Exception:
                pass

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

        try:
            result = subprocess.run(["pactl", "list", "short", "sources"], capture_output=True, text=True, check=True)
            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue
                parts = line.split("\t")
                if len(parts) < 2:
                    continue

                source_id = parts[1]
                # Bluetooth sources in PulseAudio typically have "bluez" in the name
                if "bluez" in source_id:
                    display_name = f"Bluetooth: {source_id.replace('bluez_source.', '').replace('.a2dp_source', '').replace('_', ':')}"
                    sources.append(
                        SourceDescriptor(
                            source_id=source_id,
                            source_type=SourceType.BLUETOOTH_AUDIO,
                            display_name=display_name,
                            platform="linux",
                            capabilities=SourceCapabilities(
                                sample_rates=[48000],
                                channels=[2],
                                bit_depths=[16],
                            ),
                        )
                    )
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

        self._session_id = f"sess_{id(self)}"
        self._frame_sink = frame_sink
        self._running = True
        self._capture_task = asyncio.create_task(self._capture_loop(source_id))

        logger.info(f"Started Bluetooth capture from {source_id} (session: {self._session_id})")
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
        # Simple health check based on whether the capture loop is running
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

    def start_pairing(self, timeout_seconds: int = 60) -> PairingResult:
        """Start adapter pairing mode using bluetoothctl."""
        if not shutil.which("bluetoothctl"):
            return PairingResult(success=False, error="bluetoothctl not found")

        try:
            # Enable pairing and discoverability
            subprocess.run(["bluetoothctl", "power", "on"], check=True)
            subprocess.run(["bluetoothctl", "pairable", "on"], check=True)
            subprocess.run(["bluetoothctl", "discoverable", "on"], check=True)

            # Start a timer to stop pairing mode
            if self._pairing_timeout_task:
                self._pairing_timeout_task.cancel()
            self._pairing_timeout_task = asyncio.create_task(self._pairing_timeout(timeout_seconds))

            logger.info(f"Bluetooth pairing mode enabled for {timeout_seconds} seconds")
            return PairingResult(success=True, message=f"Pairing mode enabled for {timeout_seconds}s")
        except subprocess.SubprocessError as e:
            logger.error(f"Failed to enable pairing mode: {e}")
            return PairingResult(success=False, error=str(e))

    async def _pairing_timeout(self, seconds: int) -> None:
        """Stop pairing mode after timeout."""
        try:
            await asyncio.sleep(seconds)
            self.stop_pairing()
            logger.info("Bluetooth pairing mode timed out")
        except asyncio.CancelledError:
            pass

    def stop_pairing(self) -> PairingResult:
        """Stop adapter pairing mode."""
        if self._pairing_timeout_task:
            self._pairing_timeout_task.cancel()
            self._pairing_timeout_task = None

        if not shutil.which("bluetoothctl"):
            return PairingResult(success=False, error="bluetoothctl not found")

        try:
            subprocess.run(["bluetoothctl", "pairable", "off"], check=True)
            subprocess.run(["bluetoothctl", "discoverable", "off"], check=True)
            logger.info("Bluetooth pairing mode disabled")
            return PairingResult(success=True, message="Pairing mode disabled")
        except subprocess.SubprocessError as e:
            logger.error(f"Failed to disable pairing mode: {e}")
            return PairingResult(success=False, error=str(e))

    async def _monitor_devices(self) -> None:
        """Watch BlueZ events for device appearance and state changes."""
        try:
            self._dbus_bus = await MessageBus(bus_type=BusType.SYSTEM).connect()

            # Introspect the BlueZ object manager
            introspection = await self._dbus_bus.introspect("org.bluez", "/")
            bluez_proxy = self._dbus_bus.get_proxy_object("org.bluez", "/", introspection)
            object_manager = bluez_proxy.get_interface("org.freedesktop.DBus.ObjectManager")

            # Listen for new interfaces (devices being discovered)
            object_manager.on_interfaces_added(self._on_interfaces_added)

            # Manually find already existing devices and subscribe to their property changes
            managed_objects = await object_manager.call_get_managed_objects()
            for path, interfaces in managed_objects.items():
                if "org.bluez.Device1" in interfaces:
                    self._subscribe_to_device_changes(path)
                    # Check if it's already the preferred device and connection is needed
                    self._check_and_trigger_reconnect(path, interfaces["org.bluez.Device1"])

            # Keep the loop alive
            while True:
                await asyncio.sleep(3600)

        except asyncio.CancelledError:
            if self._dbus_bus:
                self._dbus_bus.disconnect()
        except Exception as e:
            logger.error(f"Error monitoring BlueZ devices: {e}")

    def _on_interfaces_added(self, path: str, interfaces: dict[str, Any]) -> None:
        """Called when a new device or interface is seen."""
        if "org.bluez.Device1" in interfaces:
            device_props = interfaces["org.bluez.Device1"]
            mac = device_props.get("Address").value

            if mac:
                self._device_paths[path] = mac
                logger.info(f"Bluetooth device seen: {mac} at {path}")
                if self._event_bus:
                    self._event_bus.emit("bluetooth.device.seen", payload={"mac": mac, "path": path})

                self._subscribe_to_device_changes(path)
                self._check_and_trigger_reconnect(path, device_props)

    def _subscribe_to_device_changes(self, path: str) -> None:
        """Subscribe to PropertiesChanged on a specific device."""
        asyncio.create_task(self._async_subscribe_to_device(path))

    async def _async_subscribe_to_device(self, path: str) -> None:
        if not self._dbus_bus:
            return

        try:
            introspection = await self._dbus_bus.introspect("org.bluez", path)
            proxy = self._dbus_bus.get_proxy_object("org.bluez", path, introspection)
            properties = proxy.get_interface("org.freedesktop.DBus.Properties")

            def on_properties_changed(interface_name: str, changed_properties: dict[str, Any], invalidated_properties: list[str]) -> None:
                if interface_name == "org.bluez.Device1":
                    self._on_device_properties_changed(path, changed_properties)

            properties.on_properties_changed(on_properties_changed)
        except Exception as e:
            logger.warning(f"Failed to subscribe to device changes at {path}: {e}")

    def _on_device_properties_changed(self, path: str, changed_properties: dict[str, Any]) -> None:
        """Handle property changes for a specific device."""
        if "Connected" in changed_properties:
            is_connected = changed_properties["Connected"].value
            mac = self._device_paths.get(path)

            if not is_connected:
                # Disconnected event
                if self._event_bus:
                    self._event_bus.emit("bluetooth.device.disconnected", payload={"mac": mac, "path": path})

                # Check if we should reconnect
                # We need full properties for _check_and_trigger_reconnect, so let's fetch them
                asyncio.create_task(self._fetch_and_check_reconnect(path))
            else:
                if self._event_bus:
                    self._event_bus.emit("bluetooth.device.connected", payload={"mac": mac, "path": path})

    async def _fetch_and_check_reconnect(self, path: str) -> None:
        if not self._dbus_bus:
            return
        try:
            introspection = await self._dbus_bus.introspect("org.bluez", path)
            proxy = self._dbus_bus.get_proxy_object("org.bluez", path, introspection)

            # This is tricky with dbus-next proxy interfaces as they are not dictionaries.
            # We might need to call GetManagedObjects again or use properties interface.
            properties_iface = proxy.get_interface("org.freedesktop.DBus.Properties")
            all_props = await properties_iface.call_get_all("org.bluez.Device1")
            self._check_and_trigger_reconnect(path, all_props)
        except Exception as e:
            logger.error(f"Failed to fetch properties for {path}: {e}")

    def _check_and_trigger_reconnect(self, path: str, properties: dict[str, Any]) -> None:
        """Decide if we should attempt connection to this device."""
        mac = properties.get("Address").value if properties.get("Address") else None
        is_connected = properties.get("Connected").value if properties.get("Connected") else False
        is_trusted = properties.get("Trusted").value if properties.get("Trusted") else False
        is_paired = properties.get("Paired").value if properties.get("Paired") else False

        if not mac:
            mac = self._device_paths.get(path)

        if not mac:
            return

        self._device_paths[path] = mac

        preferred_mac = None
        if self._config_store:
            preferred_mac = self._config_store.get("bluetooth_preferred_device")

        if mac == preferred_mac and is_trusted and is_paired and not is_connected:
            logger.info(f"Preferred device {mac} seen and not connected. Triggering reconnect.")
            if self._reconnect_task and not self._reconnect_task.done():
                return  # Reconnect already in progress

            self._reconnect_task = asyncio.create_task(self._reconnect_loop(path, mac))

    async def _reconnect_loop(self, path: str, mac: str) -> None:
        """Bounded retry with backoff for device connection."""
        backoff_schedule = [0, 5, 15, 30, 60]
        retry_count = 0

        while True:
            # Determine delay
            delay = backoff_schedule[retry_count] if retry_count < len(backoff_schedule) else 60

            if delay > 0:
                logger.info(f"Scheduling Bluetooth reconnect for {mac} in {delay}s")
                if self._event_bus:
                    self._event_bus.emit(
                        "bluetooth.device.reconnect_scheduled", payload={"mac": mac, "delay": delay, "retry_count": retry_count}
                    )
                await asyncio.sleep(delay)

            logger.info(f"Attempting Bluetooth connection to {mac} (attempt {retry_count + 1})")
            if self._event_bus:
                self._event_bus.emit("bluetooth.device.connecting", payload={"mac": mac})

            try:
                # Use bluetoothctl for connection as it's more robust than raw DBus calls for A2DP
                process = await asyncio.create_subprocess_exec(
                    "bluetoothctl", "connect", mac, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await process.communicate()
                exit_code = process.returncode

                if exit_code == 0:
                    logger.info(f"Successfully connected to Bluetooth device {mac}")
                    # Note: EventType.BLUETOOTH_DEVICE_CONNECTED will be emitted by PropertiesChanged
                    break
                else:
                    error_msg = stdout.decode().strip() + " " + stderr.decode().strip()
                    logger.warning(f"Failed to connect to Bluetooth device {mac}: {error_msg}")

                    # Stop retrying on fatal/auth errors
                    # Common BlueZ error strings for fatal issues
                    fatal_errors = [
                        "Authentication Failed",
                        "Authentication Canceled",
                        "NotReady",
                        "Failed to connect: org.bluez.Error.Failed",
                    ]
                    if any(err in error_msg for err in fatal_errors):
                        logger.error(f"Fatal connection error for {mac}. Stopping retries.")
                        if self._event_bus:
                            self._event_bus.emit(
                                "bluetooth.device.reconnect_failed", payload={"mac": mac, "error": error_msg, "fatal": True}
                            )
                        break

            except Exception as e:
                logger.error(f"Unexpected error connecting to Bluetooth device {mac}: {e}")

            retry_count += 1
            if self._event_bus:
                self._event_bus.emit("bluetooth.device.reconnect_failed", payload={"mac": mac, "retry_count": retry_count, "fatal": False})

            # Check if device is still disconnected before retrying
            # (PropertiesChanged might have already updated connection state)
            # Fetch properties again
            try:
                if not self._dbus_bus:
                    break
                introspection = await self._dbus_bus.introspect("org.bluez", path)
                proxy = self._dbus_bus.get_proxy_object("org.bluez", path, introspection)
                properties_iface = proxy.get_interface("org.freedesktop.DBus.Properties")
                is_connected = (await properties_iface.call_get("org.bluez.Device1", "Connected")).value
                if is_connected:
                    logger.info(f"Device {mac} already connected, stopping reconnect loop.")
                    break
            except Exception:
                pass  # Continue retry if we can't check
