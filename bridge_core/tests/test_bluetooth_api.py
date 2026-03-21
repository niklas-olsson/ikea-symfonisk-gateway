"""Tests for Bluetooth API endpoints."""

import sys
from unittest.mock import AsyncMock, MagicMock, patch

sys.modules["sounddevice"] = MagicMock()

import pytest  # noqa: E402
from bridge_core.core.event_bus import EventBus  # noqa: E402
from bridge_core.core.source_registry import SourceRegistry  # noqa: E402
from bridge_core.main import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from ingress_sdk.types import (  # noqa: E402
    AdapterCapabilities,
    SourceCapabilities,
    SourceDescriptor,
    SourceType,
)


@pytest.fixture
def mock_bluetooth_adapter():
    adapter = MagicMock()
    adapter.id.return_value = "linux-bluetooth-adapter"
    adapter.platform.return_value = "linux"
    adapter.get_adapter_status = AsyncMock(
        return_value={
            "healthy": True,
            "properties": {"Powered": True},
            "pairing_window": {"is_open": False},
            "backend": {"type": "PulseAudio"},
        }
    )
    adapter.list_devices = AsyncMock(return_value=[{"mac": "AA:BB:CC:DD:EE:FF", "name": "Test Device", "connected": True}])
    adapter.get_preferred_device = MagicMock(return_value="AA:BB:CC:DD:EE:FF")
    adapter.list_sources = MagicMock(
        return_value=[
            SourceDescriptor(
                source_id="bluetooth:aa:bb:cc:dd:ee:ff",
                source_type=SourceType.BLUETOOTH_AUDIO,
                display_name="Bluetooth: Test Device",
                platform="linux",
                capabilities=SourceCapabilities(sample_rates=[48000], channels=[2], bit_depths=[16]),
            )
        ]
    )
    adapter._running = True
    adapter._session_id = "test_session"
    return adapter


@pytest.fixture
def client(mock_bluetooth_adapter):
    event_bus = EventBus()
    source_registry = SourceRegistry(event_bus)

    caps = AdapterCapabilities(
        supports_bluetooth_audio=True,
        supports_pairing=True,
        supports_hotplug_events=True,
        supports_sample_rates=[48000],
        supports_channels=[2],
    )

    async def dummy_coro():
        pass

    with patch("asyncio.create_task", return_value=MagicMock()):
        source_registry.register_adapter(
            adapter_id="linux-bluetooth-adapter",
            platform="linux",
            version="0.1.0",
            capabilities=caps,
            sources=[],
            adapter_instance=mock_bluetooth_adapter,
        )

    # Mock StreamPublisher.start and stop to return a real coroutine
    with (
        patch("bridge_core.main.StreamPublisher") as mock_pub_class,
        patch("bridge_core.main.LinuxBluetoothAdapter", return_value=mock_bluetooth_adapter),
        patch("bridge_core.main.ConfigStore"),
    ):
        mock_pub = mock_pub_class.return_value
        mock_pub.start.return_value = dummy_coro()
        mock_pub.stop.return_value = dummy_coro()

        # Override the app state with our controlled versions
        app.state.source_registry = source_registry
        app.state.event_bus = event_bus

        with TestClient(app) as c:
            yield c


def test_get_bluetooth_status(client, mock_bluetooth_adapter):
    response = client.get("/v1/bluetooth/status")
    assert response.status_code == 200
    assert response.json()["healthy"] is True
    # We don't check call count because the actual adapter might be called during lifespan or status check
    # But it should be healthy in the response


def test_open_pairing_window(client, mock_bluetooth_adapter):
    mock_bluetooth_adapter.start_pairing.return_value = MagicMock(success=True, message="Opened")
    response = client.post("/v1/bluetooth/pairing/open", json={"timeout_seconds": 60})
    assert response.status_code == 200
    assert response.json()["success"] is True


def test_list_bluetooth_devices(client, mock_bluetooth_adapter):
    response = client.get("/v1/bluetooth/devices")
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["mac"] == "AA:BB:CC:DD:EE:FF"


def test_connect_device(client, mock_bluetooth_adapter):
    mock_bluetooth_adapter.connect_device = AsyncMock(return_value=True)
    response = client.post("/v1/bluetooth/devices/AA:BB:CC:DD:EE:FF/connect")
    assert response.status_code == 200
    assert response.json()["success"] is True


def test_get_bluetooth_source(client, mock_bluetooth_adapter):
    response = client.get("/v1/bluetooth/source")
    assert response.status_code == 200
    assert response.json()["active"] is True
    assert response.json()["session_id"] == "test_session"
