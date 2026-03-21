"""Unit tests for the Linux Bluetooth ingress adapter."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from adapter_linux_bluetooth import LinuxBluetoothAdapter
from bridge_core.core.event_bus import EventType
from ingress_sdk.types import SourceType


@pytest.fixture
def event_bus() -> MagicMock:
    return MagicMock()


@pytest.fixture
def config_store() -> MagicMock:
    store = MagicMock()
    store.get.return_value = "XX:XX:XX:XX:XX:XX"
    return store


@pytest.fixture
async def adapter(event_bus: MagicMock, config_store: MagicMock) -> LinuxBluetoothAdapter:
    with patch("dbus_next.aio.MessageBus.connect", return_value=AsyncMock()):
        adapter = LinuxBluetoothAdapter(event_bus=event_bus, config_store=config_store)
        # Mock _device_paths for tests
        adapter._device_paths["/org/bluez/hci0/dev_XX_XX_XX_XX_XX_XX"] = "XX:XX:XX:XX:XX:XX"
        yield adapter
        if adapter._monitor_task:
            adapter._monitor_task.cancel()
            try:
                await adapter._monitor_task
            except asyncio.CancelledError:
                pass
        if adapter._reconnect_task:
            adapter._reconnect_task.cancel()
            try:
                await adapter._reconnect_task
            except asyncio.CancelledError:
                pass


@pytest.mark.asyncio
async def test_capabilities(adapter: LinuxBluetoothAdapter) -> None:
    caps = adapter.capabilities()
    assert caps.supports_bluetooth_audio is True
    assert caps.supports_pairing is True
    assert adapter.platform() == "linux"


@pytest.mark.asyncio
async def test_list_sources_success(adapter: LinuxBluetoothAdapter) -> None:
    mock_pactl_output = "1\tbluez_source.XX_XX_XX_XX_XX_XX.a2dp_source\tmodule-bluez5-device.c\ts16le 2ch 48000Hz\tIDLE"

    with (
        patch("shutil.which", return_value="/usr/bin/pactl"),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(stdout=mock_pactl_output, returncode=0)

        sources = adapter.list_sources()

        assert len(sources) == 1
        assert sources[0].source_id == "bluez_source.XX_XX_XX_XX_XX_XX.a2dp_source"
        assert sources[0].source_type == SourceType.BLUETOOTH_AUDIO
        assert "XX:XX:XX:XX:XX:XX" in sources[0].display_name


@pytest.mark.asyncio
async def test_start_pairing_success(adapter: LinuxBluetoothAdapter) -> None:
    with (
        patch("shutil.which", return_value="/usr/bin/bluetoothctl"),
        patch("subprocess.run") as mock_run,
    ):
        result = adapter.start_pairing(timeout_seconds=1)

        assert result.success is True
        assert "Pairing mode enabled" in result.message
        assert mock_run.call_count == 3  # power, pairable, discoverable

        # Cleanup pairing task
        adapter.stop_pairing()


@pytest.mark.asyncio
async def test_stop_pairing(adapter: LinuxBluetoothAdapter) -> None:
    with (
        patch("shutil.which", return_value="/usr/bin/bluetoothctl"),
        patch("subprocess.run") as mock_run,
    ):
        result = adapter.stop_pairing()

        assert result.success is True
        assert "Pairing mode disabled" in result.message
        assert mock_run.call_count == 2  # pairable off, discoverable off


@pytest.mark.asyncio
async def test_lifecycle(adapter: LinuxBluetoothAdapter) -> None:
    source_id = "bluez_source.test"
    mock_sink = MagicMock()

    with (
        patch("shutil.which", return_value="/usr/bin/parec"),
        patch("asyncio.create_subprocess_exec") as mock_exec,
    ):
        # Mock subprocess
        mock_process = MagicMock()
        mock_process.stdout.readexactly.side_effect = asyncio.CancelledError()
        mock_exec.return_value = mock_process

        # Start
        start_result = adapter.start(source_id, mock_sink)
        assert start_result.success is True
        assert adapter._running is True

        # Stop
        adapter.stop(start_result.session_id)
        assert adapter._running is False
        assert adapter._session_id is None


@pytest.mark.asyncio
async def test_reconnect_logic(adapter: LinuxBluetoothAdapter, event_bus: MagicMock) -> None:
    mac = "XX:XX:XX:XX:XX:XX"
    path = "/org/bluez/hci0/dev_XX_XX_XX_XX_XX_XX"

    # Mock dbus bus
    adapter._dbus_bus = MagicMock()
    mock_introspection = AsyncMock()
    adapter._dbus_bus.introspect = AsyncMock(return_value=mock_introspection)
    mock_proxy = MagicMock()
    adapter._dbus_bus.get_proxy_object = MagicMock(return_value=mock_proxy)
    mock_properties = AsyncMock()
    mock_proxy.get_interface = MagicMock(return_value=mock_properties)

    # Mock connection check to return False (not connected)
    mock_connected_val = MagicMock()
    mock_connected_val.value = False
    mock_properties.call_get = AsyncMock(return_value=mock_connected_val)

    # Mock subprocess for bluetoothctl connect
    with patch("asyncio.create_subprocess_exec") as mock_exec:
        mock_process = AsyncMock()
        mock_process.communicate.return_value = (b"Connected", b"")
        mock_process.returncode = 0
        mock_exec.return_value = mock_process

        # Trigger reconnect loop
        await adapter._reconnect_loop(path, mac)

        # Verify events

        # Should have emitted connecting event
        event_bus.emit.assert_any_call("bluetooth.device.connecting", payload={"mac": mac})

        # Verify bluetoothctl was called
        mock_exec.assert_called_with(
            "bluetoothctl", "connect", mac,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )


@pytest.mark.asyncio
async def test_reconnect_backoff(adapter: LinuxBluetoothAdapter, event_bus: MagicMock) -> None:
    mac = "XX:XX:XX:XX:XX:XX"
    path = "/org/bluez/hci0/dev_XX_XX_XX_XX_XX_XX"

    # Mock dbus bus
    adapter._dbus_bus = MagicMock()
    mock_introspection = AsyncMock()
    adapter._dbus_bus.introspect = AsyncMock(return_value=mock_introspection)
    mock_proxy = MagicMock()
    adapter._dbus_bus.get_proxy_object = MagicMock(return_value=mock_proxy)
    mock_properties = AsyncMock()
    mock_proxy.get_interface = MagicMock(return_value=mock_properties)

    # Mock connection check to always return False (keep retrying)
    mock_connected_val = MagicMock()
    mock_connected_val.value = False
    mock_properties.call_get = AsyncMock(return_value=mock_connected_val)

    # Mock asyncio.sleep to avoid waiting but still yield
    real_sleep = asyncio.sleep
    async def fast_sleep(delay):
        await real_sleep(0.0001)

    with (
        patch("asyncio.create_subprocess_exec") as mock_exec,
        patch("asyncio.sleep", side_effect=fast_sleep) as mock_sleep,
    ):
        mock_process = AsyncMock()
        mock_process.communicate.return_value = (b"", b"Connection Failed")
        mock_process.returncode = 1
        mock_exec.return_value = mock_process

        # Run reconnect loop for a few iterations then cancel it
        task = asyncio.create_task(adapter._reconnect_loop(path, mac))

        # We need to wait enough for multiple iterations
        # Each iteration has one fast_sleep(delay) and potentially one fast_sleep(0.001) via side_effect
        # and some other async calls.
        for _ in range(20):
            await asyncio.sleep(0.005)
            if len([call for call in event_bus.emit.call_args_list
                    if call.args[0] == EventType.BLUETOOTH_DEVICE_RECONNECT_SCHEDULED]) >= 2:
                break

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # Verify backoff sequence was scheduled

        # Check some expected delay values
        expected_delays = [0, 5, 15]
        actual_delays = []
        for call in event_bus.emit.call_args_list:
            if call.args[0] == "bluetooth.device.reconnect_scheduled":
                payload = call.kwargs.get("payload") or (call.args[1] if len(call.args) > 1 else {})
                actual_delays.append(payload.get("delay"))

        for delay in expected_delays:
            if delay > 0: # 0 delay might not be explicitly scheduled with sleep
                assert delay in actual_delays


@pytest.mark.asyncio
async def test_reconnect_stops_on_fatal(adapter: LinuxBluetoothAdapter, event_bus: MagicMock) -> None:
    mac = "XX:XX:XX:XX:XX:XX"
    path = "/org/bluez/hci0/dev_XX_XX_XX_XX_XX_XX"

    # Mock dbus bus
    adapter._dbus_bus = MagicMock()
    mock_introspection = AsyncMock()
    adapter._dbus_bus.introspect = AsyncMock(return_value=mock_introspection)
    mock_proxy = MagicMock()
    adapter._dbus_bus.get_proxy_object = MagicMock(return_value=mock_proxy)
    mock_properties = AsyncMock()
    mock_proxy.get_interface = MagicMock(return_value=mock_properties)

    # Mock connection check
    mock_connected_val = MagicMock()
    mock_connected_val.value = False
    mock_properties.call_get = AsyncMock(return_value=mock_connected_val)

    with (
        patch("asyncio.create_subprocess_exec") as mock_exec,
        patch("asyncio.sleep", AsyncMock()),
    ):
        mock_process = AsyncMock()
        mock_process.communicate.return_value = (b"", b"Authentication Failed")
        mock_process.returncode = 1
        mock_exec.return_value = mock_process

        # Run reconnect loop
        await adapter._reconnect_loop(path, mac)

        # Verify it stopped and emitted fatal error

        event_bus.emit.assert_any_call(
            "bluetooth.device.reconnect_failed",
            payload={"mac": mac, "error": " Authentication Failed", "fatal": True}
        )
        # Should only have called connect once
        assert mock_exec.call_count == 1
