import pytest # type: ignore[import-not-found]
from bridge_core.core.source_registry import SourceRegistry, AdapterInfo # type: ignore[import-untyped]
from ingress_sdk.types import AdapterCapabilities, SourceDescriptor, SourceType, SourceCapabilities, HealthResult # type: ignore[import-untyped]

@pytest.fixture # type: ignore[untyped-decorator]
def capabilities() -> AdapterCapabilities:
    return AdapterCapabilities(supports_bluetooth_audio=True)

@pytest.fixture # type: ignore[untyped-decorator]
def source_descriptor() -> SourceDescriptor:
    return SourceDescriptor(
        source_id="src-1",
        source_type=SourceType.BLUETOOTH_AUDIO,
        display_name="Bluetooth Source",
        platform="test",
        capabilities=SourceCapabilities()
    )

@pytest.fixture # type: ignore[untyped-decorator]
def source_registry() -> SourceRegistry:
    return SourceRegistry()

def test_register_adapter(source_registry: SourceRegistry, capabilities: AdapterCapabilities, source_descriptor: SourceDescriptor) -> None:
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

def test_unregister_adapter(source_registry: SourceRegistry, capabilities: AdapterCapabilities, source_descriptor: SourceDescriptor) -> None:
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

def test_source_health(source_registry: SourceRegistry, capabilities: AdapterCapabilities, source_descriptor: SourceDescriptor) -> None:
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

def test_adapter_connected(source_registry: SourceRegistry, capabilities: AdapterCapabilities, source_descriptor: SourceDescriptor) -> None:
    assert source_registry.adapter_connected("adapter-1") is False

    source_registry.register_adapter(
        adapter_id="adapter-1",
        platform="test_platform",
        version="1.0",
        capabilities=capabilities,
        sources=[source_descriptor]
    )

    assert source_registry.adapter_connected("adapter-1") is True
