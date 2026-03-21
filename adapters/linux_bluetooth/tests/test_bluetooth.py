"""Unit tests for the Linux Bluetooth ingress adapter."""

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from adapter_linux_bluetooth import LinuxBluetoothAdapter
from ingress_sdk.types import SourceType


@pytest.fixture
def adapter() -> LinuxBluetoothAdapter:
    return LinuxBluetoothAdapter()


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
        assert "Pairing window opened" in result.message

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
        assert "Pairing window closed" in result.message


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
