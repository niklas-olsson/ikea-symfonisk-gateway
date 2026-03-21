import asyncio

import pytest
from bridge_core.core.event_bus import BridgeEvent, EventBus, EventType
from bridge_core.core.source_registry import SourceRegistry
from ingress_sdk.types import AdapterCapabilities, HealthResult, SourceCapabilities, SourceDescriptor, SourceType


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture
def registry(event_bus: EventBus) -> SourceRegistry:
    return SourceRegistry(event_bus=event_bus)


async def flush_events(queue: asyncio.Queue[BridgeEvent]) -> None:
    """Wait a bit for events to be processed."""
    await asyncio.sleep(0.01)
    while not queue.empty():
        queue.get_nowait()


@pytest.mark.asyncio
async def test_register_adapter_async(registry: SourceRegistry, event_bus: EventBus) -> None:
    queue = event_bus.subscribe()

    capabilities = AdapterCapabilities(supports_system_audio=True)
    sources = [
        SourceDescriptor(
            source_id="test_source",
            source_type=SourceType.SYSTEM_OUTPUT,
            display_name="Test Source",
            platform="test",
            capabilities=SourceCapabilities(),
        )
    ]

    registry.register_adapter(
        adapter_id="test_adapter", platform="test_platform", version="1.0.0", capabilities=capabilities, sources=sources
    )

    e1 = await asyncio.wait_for(queue.get(), timeout=1.0)
    e2 = await asyncio.wait_for(queue.get(), timeout=1.0)

    event_types = {e1.type, e2.type}
    assert EventType.ADAPTER_REGISTERED.value in event_types
    assert EventType.TOPOLOGY_CHANGED.value in event_types

    adapter = registry.get_adapter("test_adapter")
    assert adapter is not None
    assert "test_source" in adapter.sources

    source = registry.get_source("test_source")
    assert source is not None
    assert source.source_id == "test_source"


@pytest.mark.asyncio
async def test_unregister_adapter_async(registry: SourceRegistry, event_bus: EventBus) -> None:
    registry.register_adapter(
        adapter_id="test_adapter",
        platform="test_platform",
        version="1.0.0",
        capabilities=AdapterCapabilities(),
        sources=[
            SourceDescriptor(
                source_id="test_source",
                source_type=SourceType.SYSTEM_OUTPUT,
                display_name="Test Source",
                platform="test",
                capabilities=SourceCapabilities(),
            )
        ],
    )

    queue = event_bus.subscribe()
    # Flush ADAPTER_REGISTERED and TOPOLOGY_CHANGED
    await flush_events(queue)

    registry.unregister_adapter("test_adapter")

    e1 = await asyncio.wait_for(queue.get(), timeout=1.0)
    e2 = await asyncio.wait_for(queue.get(), timeout=1.0)

    event_types = {e1.type, e2.type}
    assert EventType.ADAPTER_UNREGISTERED.value in event_types
    assert EventType.TOPOLOGY_CHANGED.value in event_types

    assert registry.get_adapter("test_adapter") is None
    assert registry.get_source("test_source") is None


@pytest.mark.asyncio
async def test_update_source_health(registry: SourceRegistry, event_bus: EventBus) -> None:
    registry.register_adapter(
        adapter_id="test_adapter",
        platform="test_platform",
        version="1.0.0",
        capabilities=AdapterCapabilities(),
        sources=[
            SourceDescriptor(
                source_id="test_source",
                source_type=SourceType.SYSTEM_OUTPUT,
                display_name="Test Source",
                platform="test",
                capabilities=SourceCapabilities(),
            )
        ],
    )

    queue = event_bus.subscribe()
    # Flush initial registration events
    await flush_events(queue)

    # First health update
    health1 = HealthResult(healthy=True, source_state="running", signal_present=True)
    registry.update_source_health("test_source", health1)

    e1 = await asyncio.wait_for(queue.get(), timeout=1.0)
    e2 = await asyncio.wait_for(queue.get(), timeout=1.0)

    event_types = {e1.type, e2.type}
    assert EventType.SOURCE_STATE_CHANGED.value in event_types
    assert EventType.SOURCE_SIGNAL_DETECTED.value in event_types

    assert registry.get_source_health("test_source") == health1

    # Signal lost
    health2 = HealthResult(healthy=True, source_state="running", signal_present=False)
    registry.update_source_health("test_source", health2)

    e3 = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert e3.type == EventType.SOURCE_SIGNAL_LOST.value

    # State change
    health3 = HealthResult(healthy=False, source_state="error", signal_present=False)
    registry.update_source_health("test_source", health3)

    e4 = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert e4.type == EventType.SOURCE_STATE_CHANGED.value
    assert e4.payload["state"] == "error"


@pytest.mark.asyncio
async def test_update_adapter_sources_hotplug(registry: SourceRegistry, event_bus: EventBus) -> None:
    registry.register_adapter(
        adapter_id="test_adapter",
        platform="test_platform",
        version="1.0.0",
        capabilities=AdapterCapabilities(),
        sources=[
            SourceDescriptor(
                source_id="source1",
                source_type=SourceType.SYSTEM_OUTPUT,
                display_name="Source 1",
                platform="test",
                capabilities=SourceCapabilities(),
            )
        ],
    )

    queue = event_bus.subscribe()
    # Flush registration events
    await flush_events(queue)

    new_sources = [
        SourceDescriptor(
            source_id="source2",
            source_type=SourceType.SYSTEM_OUTPUT,
            display_name="Source 2",
            platform="test",
            capabilities=SourceCapabilities(),
        )
    ]

    registry.update_adapter_sources("test_adapter", new_sources)

    e1 = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert e1.type == EventType.TOPOLOGY_CHANGED.value

    assert registry.get_source("source1") is None
    assert registry.get_source("source2") is not None
    adapter = registry.get_adapter("test_adapter")
    assert adapter is not None
    assert "source2" in adapter.sources
