import pytest
from unittest.mock import patch, MagicMock
from renderer_sonos.discovery import SonosDiscovery

@pytest.mark.asyncio
async def test_discover_no_devices():
    discovery = SonosDiscovery()

    with patch('soco.discover', return_value=None):
        devices = await discovery.discover(timeout=1)

    assert devices == {}
    assert discovery.devices == {}
    assert discovery.discovered is True

@pytest.mark.asyncio
async def test_discover_devices():
    discovery = SonosDiscovery()

    device1 = MagicMock()
    device1.uid = "RINCON_1"
    device1.player_name = "Living Room"
    device1.ip_address = "192.168.1.10"
    device1.get_speaker_info.return_value = {"model_name": "SYMFONISK Bookshelf"}

    device2 = MagicMock()
    device2.uid = "RINCON_2"
    device2.player_name = "Kitchen"
    device2.ip_address = "192.168.1.11"
    device2.get_speaker_info.return_value = {"model_name": "SYMFONISK Table Lamp"}

    with patch('soco.discover', return_value={device1, device2}):
        devices = await discovery.discover(timeout=1)

    assert len(devices) == 2
    assert "RINCON_1" in devices
    assert "RINCON_2" in devices

    meta1 = discovery.devices["RINCON_1"]
    assert meta1.uid == "RINCON_1"
    assert meta1.name == "Living Room"
    assert meta1.ip == "192.168.1.10"
    assert meta1.model == "SYMFONISK Bookshelf"
    assert meta1.device == device1

    meta2 = discovery.devices["RINCON_2"]
    assert meta2.uid == "RINCON_2"
    assert meta2.name == "Kitchen"
    assert meta2.ip == "192.168.1.11"
    assert meta2.model == "SYMFONISK Table Lamp"
    assert meta2.device == device2

    assert discovery.discovered is True
