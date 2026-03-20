import pytest
from bridge_core.core.source_registry import SourceRegistry, AdapterInfo
from ingress_sdk.types import AdapterCapabilities, SourceDescriptor, SourceType, SourceCapabilities, HealthResult

@pytest.fixture
def capabilities():
    return AdapterCapabilities(supports_bluetooth_audio=True)

@pytest.fixture
def source_descriptor():
    return SourceDescriptor(
        source_id="src-1",
        source_type=SourceType.BLUETOOTH_AUDIO,
        display_name="Bluetooth Source",
        platform="test",
        capabilities=SourceCapabilities()
    )

@pytest.fixture
def source_registry():
    return SourceRegistry()

def test_register_adapter(source_registry, capabilities, source_descriptor):
    source_registry.register_adapter(
        adapter_id="adapter-1",
        platform="test_platform",
        version="1.0",
        capabilities=capabilities,
        sources=[source_descriptor]
    )

    adapter = source_registry.get_adapter("adapter-1")
    assert adapter is not None
    assert adapter.adapter_id == "adapter-1"
    assert adapter.platform == "test_platform"
    assert len(adapter.sources) == 1

    source = source_registry.get_source("src-1")
    assert source is not None
    assert source.source_id == "src-1"

def test_unregister_adapter(source_registry, capabilities, source_descriptor):
    source_registry.register_adapter(
        adapter_id="adapter-1",
        platform="test_platform",
        version="1.0",
        capabilities=capabilities,
        sources=[source_descriptor]
    )

    # Unregister
    source_registry.unregister_adapter("adapter-1")

    assert source_registry.get_adapter("adapter-1") is None
    assert source_registry.get_source("src-1") is None

def test_source_health(source_registry, capabilities, source_descriptor):
    source_registry.register_adapter(
        adapter_id="adapter-1",
        platform="test_platform",
        version="1.0",
        capabilities=capabilities,
        sources=[source_descriptor]
    )

    health = HealthResult(
        healthy=True,
        source_state="playing",
        signal_present=True
    )

    source_registry.update_source_health("src-1", health)

    retrieved_health = source_registry.get_source_health("src-1")
    assert retrieved_health is not None
    assert retrieved_health.healthy is True
    assert retrieved_health.source_state == "playing"
    assert retrieved_health.signal_present is True

    # Test unregistering clears health
    source_registry.unregister_adapter("adapter-1")
    assert source_registry.get_source_health("src-1") is None

def test_adapter_connected(source_registry, capabilities, source_descriptor):
    assert source_registry.adapter_connected("adapter-1") is False

    source_registry.register_adapter(
        adapter_id="adapter-1",
        platform="test_platform",
        version="1.0",
        capabilities=capabilities,
        sources=[source_descriptor]
    )

    assert source_registry.adapter_connected("adapter-1") is True
