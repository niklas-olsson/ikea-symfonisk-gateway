"""Tests for Bluetooth adapter components."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from adapter_linux_bluetooth.dbus_adapter import BlueZAdapterController
from adapter_linux_bluetooth.store import TrustedDeviceStore
from adapter_linux_bluetooth.window import PairingWindowManager
from bridge_core.core.event_bus import EventBus, EventType


@pytest.fixture
def store(tmp_path):
    path = tmp_path / "test_trusted_devices.json"
    return TrustedDeviceStore(storage_path=path)


def test_store_operations(store):
    mac = "00:11:22:33:44:55"
    metadata = {"alias": "Test Turntable"}

    assert not store.is_trusted(mac)
    store.trust_device(mac, metadata)
    assert store.is_trusted(mac)
    assert store.get_device_metadata(mac) == metadata

    store.set_preferred_device(mac)
    assert store.get_preferred_device() == mac

    store.untrust_device(mac)
    assert not store.is_trusted(mac)
    assert store.get_preferred_device() is None


@pytest.mark.asyncio
async def test_adapter_controller_properties():
    with patch("adapter_linux_bluetooth.dbus_adapter.MessageBus") as mock_bus_cls:
        mock_bus = MagicMock()
        mock_bus.connect = AsyncMock(return_value=mock_bus)
        mock_bus_cls.return_value = mock_bus

        controller = BlueZAdapterController("hci0")

        # Mock introspection and proxy object
        mock_bus.introspect = AsyncMock(return_value="fake introspection")
        mock_proxy = MagicMock()
        mock_bus.get_proxy_object.return_value = mock_proxy
        mock_iface = MagicMock()
        mock_iface.call_get_all = AsyncMock()
        mock_iface.call_set = AsyncMock()
        mock_proxy.get_interface.return_value = mock_iface

        # Mock call_get_all
        from dbus_fast import Variant
        mock_iface.call_get_all.return_value = {
            "Powered": Variant("b", True),
            "Alias": Variant("s", "Bridge"),
        }

        props = await controller.get_properties()
        assert props["Powered"] is True
        assert props["Alias"] == "Bridge"

        # Test setting property
        await controller.set_powered(False)
        mock_iface.call_set.assert_called_with(
            "org.bluez.Adapter1", "Powered", pytest.approx(Variant("b", False))
        )


@pytest.mark.asyncio
async def test_pairing_window_auto_close_and_trust(store):
    event_bus = EventBus()
    controller = MagicMock(spec=BlueZAdapterController)
    controller.set_powered = AsyncMock(return_value=True)
    controller.set_pairable = AsyncMock(return_value=True)
    controller.set_discoverable = AsyncMock(return_value=True)
    controller.set_discoverable_timeout = AsyncMock(return_value=True)
    controller.set_pairable_timeout = AsyncMock(return_value=True)

    window = PairingWindowManager(controller, store, event_bus)

    # Mock DBus MessageBus and agent registration
    with (
        patch("adapter_linux_bluetooth.window.MessageBus") as mock_bus_cls,
        patch("adapter_linux_bluetooth.window.register_agent", new_callable=AsyncMock) as mock_reg,
        patch("adapter_linux_bluetooth.window.unregister_agent", new_callable=AsyncMock) as mock_unreg,
    ):
        mock_bus = MagicMock()
        mock_bus.connect = AsyncMock(return_value=mock_bus)
        mock_bus_cls.return_value = mock_bus

        # Open window
        await window.open_window(timeout_seconds=90)
        assert window.is_open is True

        # Simulate pairing success from agent
        test_mac = "AA:BB:CC:DD:EE:FF"
        window._handle_pairing_completed(test_mac, True)

        # Check if device is trusted
        assert store.is_trusted(test_mac)

        # Wait for the task to finish due to completion event
        await asyncio.wait_for(window._pairing_task, timeout=1.0)
        assert window.is_open is False
        mock_unreg.assert_called()


@pytest.mark.asyncio
async def test_pairing_window_timeout(store):
    event_bus = EventBus()
    controller = MagicMock(spec=BlueZAdapterController)
    controller.set_powered = AsyncMock(return_value=True)
    controller.set_pairable = AsyncMock(return_value=True)
    controller.set_discoverable = AsyncMock(return_value=True)
    controller.set_discoverable_timeout = AsyncMock(return_value=True)
    controller.set_pairable_timeout = AsyncMock(return_value=True)

    window = PairingWindowManager(controller, store, event_bus)

    with (
        patch("adapter_linux_bluetooth.window.MessageBus") as mock_bus_cls,
        patch("adapter_linux_bluetooth.window.register_agent", new_callable=AsyncMock) as mock_reg,
        patch("adapter_linux_bluetooth.window.unregister_agent", new_callable=AsyncMock) as mock_unreg,
    ):
        mock_bus = MagicMock()
        mock_bus.connect = AsyncMock(return_value=mock_bus)
        mock_bus_cls.return_value = mock_bus

        # Open window with very short timeout
        await window.open_window(timeout_seconds=0.1)
        assert window.is_open is True

        # Wait for timeout
        await asyncio.wait_for(window._pairing_task, timeout=1.0)
        assert window.is_open is False
        mock_unreg.assert_called()
