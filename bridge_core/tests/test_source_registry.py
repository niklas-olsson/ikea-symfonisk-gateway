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
            source_type=SourceType.SYSTEM_AUDIO,
            display_name="Test Source",
            platform="test",
            capabilities=SourceCapabilities(),
        )
    ]

    registry.register_adapter(
        adapter_id="test_adapter", platform="test", version="1.0.0", capabilities=capabilities, sources=sources
    )

    e1 = await asyncio.wait_for(queue.get(), timeout=1.0)
    e2 = await asyncio.wait_for(queue.get(), timeout=1.0)

    event_types = {e1.type, e2.type}
    assert EventType.ADAPTER_REGISTERED.value in event_types
    assert EventType.TOPOLOGY_CHANGED.value in event_types

    adapter = registry.get_adapter("test_adapter")
    assert adapter is not None
    assert "test_adapter:system:test_source" in adapter.sources

    source = registry.get_source("test_source")
    assert source is None

    canonical_source = registry.list_sources()[0]
    assert canonical_source.source_id == "test_adapter:system:test_source"
    assert canonical_source.local_source_id == "test_source"


@pytest.mark.asyncio
async def test_unregister_adapter_async(registry: SourceRegistry, event_bus: EventBus) -> None:
    registry.register_adapter(
        adapter_id="test_adapter",
        platform="test",
        version="1.0.0",
        capabilities=AdapterCapabilities(),
        sources=[
            SourceDescriptor(
                source_id="test_source",
                source_type=SourceType.SYSTEM_AUDIO,
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
    assert registry.get_source("test_adapter:system:test_source") is None


@pytest.mark.asyncio
async def test_update_source_health(registry: SourceRegistry, event_bus: EventBus) -> None:
    registry.register_adapter(
        adapter_id="test_adapter",
        platform="test",
        version="1.0.0",
        capabilities=AdapterCapabilities(),
        sources=[
            SourceDescriptor(
                source_id="test_source",
                source_type=SourceType.SYSTEM_AUDIO,
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
    source_id = "test_adapter:system:test_source"

    registry.update_source_health(source_id, health1)

    e1 = await asyncio.wait_for(queue.get(), timeout=1.0)
    e2 = await asyncio.wait_for(queue.get(), timeout=1.0)

    event_types = {e1.type, e2.type}
    assert EventType.SOURCE_STATE_CHANGED.value in event_types
    assert EventType.SOURCE_SIGNAL_DETECTED.value in event_types

    assert registry.get_source_health(source_id) == health1

    # Signal lost
    health2 = HealthResult(healthy=True, source_state="running", signal_present=False)
    registry.update_source_health(source_id, health2)

    e3 = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert e3.type == EventType.SOURCE_SIGNAL_LOST.value

    # State change
    health3 = HealthResult(healthy=False, source_state="error", signal_present=False)
    registry.update_source_health(source_id, health3)

    e4 = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert e4.type == EventType.SOURCE_STATE_CHANGED.value
    assert e4.payload["state"] == "error"


@pytest.mark.asyncio
async def test_update_adapter_sources_hotplug(registry: SourceRegistry, event_bus: EventBus) -> None:
    registry.register_adapter(
        adapter_id="test_adapter",
        platform="test",
        version="1.0.0",
        capabilities=AdapterCapabilities(),
        sources=[
            SourceDescriptor(
                source_id="source1",
                source_type=SourceType.SYSTEM_AUDIO,
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
            source_type=SourceType.SYSTEM_AUDIO,
            display_name="Source 2",
            platform="test",
            capabilities=SourceCapabilities(),
        )
    ]

    registry.update_adapter_sources("test_adapter", new_sources)

    e1 = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert e1.type == EventType.TOPOLOGY_CHANGED.value

    assert registry.get_source("test_adapter:system:source1") is None
    assert registry.get_source("test_adapter:system:source2") is not None
    adapter = registry.get_adapter("test_adapter")
    assert adapter is not None
    assert "test_adapter:system:source2" in adapter.sources


def test_duplicate_canonical_source_ids_fail_fast(registry: SourceRegistry) -> None:
    with pytest.raises(ValueError, match="duplicate canonical source ID"):
        registry.register_adapter(
            adapter_id="test_adapter",
            platform="test",
            version="1.0.0",
            capabilities=AdapterCapabilities(),
            sources=[
                SourceDescriptor(
                    source_id="shared",
                    source_type=SourceType.SYSTEM_AUDIO,
                    display_name="Shared 1",
                    platform="test",
                    capabilities=SourceCapabilities(),
                ),
                SourceDescriptor(
                    source_id="shared",
                    source_type=SourceType.SYSTEM_AUDIO,
                    display_name="Shared 2",
                    platform="test",
                    capabilities=SourceCapabilities(),
                ),
            ],
        )


def test_platform_mismatch_during_registration_fails_fast(registry: SourceRegistry) -> None:
    with pytest.raises(ValueError, match="does not match source platform"):
        registry.register_adapter(
            adapter_id="test_adapter",
            platform="linux",
            version="1.0.0",
            capabilities=AdapterCapabilities(),
            sources=[
                SourceDescriptor(
                    source_id="mismatched",
                    source_type=SourceType.SYSTEM_AUDIO,
                    display_name="Mismatched",
                    platform="windows",
                    capabilities=SourceCapabilities(),
                )
            ],
        )


def test_empty_local_source_id_fails_fast(registry: SourceRegistry) -> None:
    with pytest.raises(ValueError, match="empty local source ID"):
        registry.register_adapter(
            adapter_id="test_adapter",
            platform="linux",
            version="1.0.0",
            capabilities=AdapterCapabilities(),
            sources=[
                SourceDescriptor(
                    source_id="",
                    source_type=SourceType.SYSTEM_AUDIO,
                    display_name="Empty",
                    platform="linux",
                    capabilities=SourceCapabilities(),
                )
            ],
        )


@pytest.mark.asyncio
async def test_canonical_source_id_escapes_local_source_id(registry: SourceRegistry) -> None:
    registry.register_adapter(
        adapter_id="linux-audio-adapter",
        platform="linux",
        version="1.0.0",
        capabilities=AdapterCapabilities(supports_system_audio=True),
        sources=[
            SourceDescriptor(
                source_id="weird:id/with spaces",
                source_type=SourceType.SYSTEM_AUDIO,
                display_name="Escaped Source",
                platform="linux",
                capabilities=SourceCapabilities(),
            )
        ],
    )

    source = registry.get_source("linux-audio-adapter:system:weird%3Aid%2Fwith%20spaces")
    assert source is not None
    assert source.local_source_id == "weird:id/with spaces"


@pytest.mark.asyncio
async def test_overlapping_local_source_ids_are_canonicalized_per_adapter(registry: SourceRegistry) -> None:
    registry.register_adapter(
        adapter_id="windows-audio-adapter",
        platform="windows",
        version="1.0.0",
        capabilities=AdapterCapabilities(supports_system_audio=True),
        sources=[
            SourceDescriptor(
                source_id="default",
                source_type=SourceType.SYSTEM_AUDIO,
                display_name="Windows Default",
                platform="windows",
                capabilities=SourceCapabilities(),
            )
        ],
    )
    registry.register_adapter(
        adapter_id="linux-audio-adapter",
        platform="linux",
        version="1.0.0",
        capabilities=AdapterCapabilities(supports_system_audio=True),
        sources=[
            SourceDescriptor(
                source_id="default",
                source_type=SourceType.SYSTEM_AUDIO,
                display_name="Linux Default",
                platform="linux",
                capabilities=SourceCapabilities(),
            )
        ],
    )

    windows_source = registry.get_source("windows-audio-adapter:system:default")
    linux_source = registry.get_source("linux-audio-adapter:system:default")

    assert windows_source is not None
    assert windows_source.adapter_id == "windows-audio-adapter"
    assert windows_source.local_source_id == "default"

    assert linux_source is not None
    assert linux_source.adapter_id == "linux-audio-adapter"
    assert linux_source.local_source_id == "default"


@pytest.mark.asyncio
async def test_resolve_source_returns_binding(registry: SourceRegistry) -> None:
    registry.register_adapter(
        adapter_id="test_adapter",
        platform="linux",
        version="1.0.0",
        capabilities=AdapterCapabilities(supports_system_audio=True),
        sources=[
            SourceDescriptor(
                source_id="default",
                source_type=SourceType.SYSTEM_AUDIO,
                display_name="Default",
                platform="linux",
                capabilities=SourceCapabilities(),
            )
        ],
    )

    binding = registry.resolve_source("test_adapter:system:default")
    assert binding is not None
    assert binding.source.source_id == "test_adapter:system:default"
    assert binding.adapter_info.adapter_id == "test_adapter"
    assert binding.local_source_id == "default"
