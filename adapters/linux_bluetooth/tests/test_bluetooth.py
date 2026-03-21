"""Unit tests for the Linux Bluetooth ingress adapter."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from adapter_linux_bluetooth import LinuxBluetoothAdapter
from ingress_sdk.types import SourceType


@pytest.fixture
def event_bus() -> MagicMock:
    return MagicMock()


from collections.abc import AsyncGenerator

@pytest.fixture
async def adapter(event_bus: MagicMock) -> AsyncGenerator[LinuxBluetoothAdapter, None]:
    adapter = LinuxBluetoothAdapter(event_bus=event_bus)
    adapter._store._data["blocked_devices"] = []
    yield adapter
    if adapter._status_monitoring_task:
        adapter._status_monitoring_task.cancel()
        try:
            await adapter._status_monitoring_task
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
        patch("adapter_linux_bluetooth.shutil.which", side_effect=lambda x: "/usr/bin/pactl" if x == "pactl" else None),
        patch("adapter_linux_bluetooth.subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(stdout=mock_pactl_output, returncode=0)

        sources = adapter.list_sources()

        assert len(sources) == 1
        assert sources[0].source_id == "bluetooth:xx:xx:xx:xx:xx:xx"
        assert sources[0].source_type == SourceType.BLUETOOTH_AUDIO
        assert "XX:XX:XX:XX:XX:XX" in sources[0].display_name
        assert sources[0].metadata["pa_source_id"] == "bluez_source.XX_XX_XX_XX_XX_XX.a2dp_source"


@pytest.mark.asyncio
async def test_start_pairing_success(adapter: LinuxBluetoothAdapter) -> None:
    with patch("adapter_linux_bluetooth.shutil.which", return_value="/usr/bin/bluetoothctl"):
        result = adapter.start_pairing(timeout_seconds=1)

        assert result.success is True
        assert "Pairing window opened" in result.message

        adapter.stop_pairing()


@pytest.mark.asyncio
async def test_stop_pairing(adapter: LinuxBluetoothAdapter) -> None:
    with patch("adapter_linux_bluetooth.shutil.which", return_value="/usr/bin/bluetoothctl"):
        result = adapter.stop_pairing()

        assert result.success is True
        assert "Pairing window closed" in result.message


@pytest.mark.asyncio
async def test_lifecycle(adapter: LinuxBluetoothAdapter) -> None:
    source_id = "bluez_source.test"
    mock_sink = MagicMock()

    with (
        patch("adapter_linux_bluetooth.shutil.which", return_value="/usr/bin/parec"),
        patch("asyncio.create_subprocess_exec") as mock_exec,
    ):
        mock_process = MagicMock()
        mock_process.stdout.readexactly.side_effect = asyncio.CancelledError()
        mock_exec.return_value = mock_process

        start_result = adapter.start(source_id, mock_sink)
        assert start_result.success is True
        assert adapter._running is True

        adapter.stop(start_result.session_id)
        assert adapter._running is False
        assert adapter._session_id is None


@pytest.mark.asyncio
async def test_adapter_status(adapter: LinuxBluetoothAdapter) -> None:
    with (
        patch.object(adapter._adapter_controller, "get_properties", new_callable=AsyncMock) as mock_props,
        patch.object(adapter._adapter_controller, "check_readiness", new_callable=AsyncMock) as mock_ready,
    ):
        mock_props.return_value = {"Powered": True, "Discoverable": False}
        mock_ready.return_value = []

        status = await adapter.get_adapter_status()

        assert status["healthy"] is True
        assert "properties" in status
        assert "readiness_errors" in status


@pytest.mark.asyncio
async def test_trust_device(adapter: LinuxBluetoothAdapter) -> None:
    mac = "AA:BB:CC:DD:EE:FF"
    alias = "Test Device"

    adapter.trust_device(mac, alias=alias)

    assert adapter._store.is_trusted(mac)
    metadata = adapter._store.get_device_metadata(mac)
    assert metadata is not None
    assert metadata.get("alias") == alias


@pytest.mark.asyncio
async def test_forget_device(adapter: LinuxBluetoothAdapter) -> None:
    mac = "AA:BB:CC:DD:EE:FF"

    adapter.trust_device(mac)
    adapter.forget_device(mac)

    assert not adapter._store.is_trusted(mac)
