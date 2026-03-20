"""Source registry - tracks adapters and available sources."""

from typing import Any

from bridge_core.core.event_bus import EventBus, EventType
from ingress_sdk.types import AdapterCapabilities, HealthResult, SourceDescriptor


class AdapterInfo:
    """Information about a connected ingress adapter."""

    def __init__(
        self,
        adapter_id: str,
        platform: str,
        version: str,
        capabilities: AdapterCapabilities,
    ):
        self.adapter_id = adapter_id
        self.platform = platform
        self.version = version
        self.capabilities = capabilities
        self.sources: dict[str, SourceDescriptor] = {}

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "adapter_id": self.adapter_id,
            "platform": self.platform,
            "version": self.version,
            "capabilities": self.capabilities.model_dump(),
            "sources": [s.model_dump() for s in self.sources.values()],
        }


class SourceRegistry:
    """Registry for ingress adapters and their sources."""

    def __init__(self, event_bus: EventBus) -> None:
        self._event_bus = event_bus
        self._adapters: dict[str, AdapterInfo] = {}
        self._sources: dict[str, SourceDescriptor] = {}
        self._source_health: dict[str, HealthResult] = {}

    def register_adapter(
        self,
        adapter_id: str,
        platform: str,
        version: str,
        capabilities: AdapterCapabilities,
        sources: list[SourceDescriptor],
    ) -> None:
        """Register a new adapter and its sources."""
        adapter = AdapterInfo(adapter_id, platform, version, capabilities)
        adapter.sources = {s.source_id: s for s in sources}
        self._adapters[adapter_id] = adapter
        self._sources.update(adapter.sources)

        self._event_bus.emit(
            EventType.ADAPTER_REGISTERED,
            payload={"adapter_id": adapter_id, "platform": platform},
        )
        self._event_bus.emit(EventType.TOPOLOGY_CHANGED)

    def unregister_adapter(self, adapter_id: str) -> None:
        """Unregister an adapter and its sources."""
        adapter = self._adapters.pop(adapter_id, None)
        if adapter:
            for source_id in adapter.sources:
                self._sources.pop(source_id, None)
                self._source_health.pop(source_id, None)

            self._event_bus.emit(
                EventType.ADAPTER_UNREGISTERED,
                payload={"adapter_id": adapter_id},
            )
            self._event_bus.emit(EventType.TOPOLOGY_CHANGED)

    def update_adapter_sources(self, adapter_id: str, sources: list[SourceDescriptor]) -> None:
        """Update the sources for an adapter (hotplug)."""
        adapter = self._adapters.get(adapter_id)
        if not adapter:
            return

        # Identify removed sources for cleanup
        new_source_ids = {s.source_id for s in sources}
        removed_source_ids = set(adapter.sources.keys()) - new_source_ids

        for s_id in removed_source_ids:
            self._sources.pop(s_id, None)
            self._source_health.pop(s_id, None)

        # Update adapter and global map
        adapter.sources = {s.source_id: s for s in sources}
        self._sources.update(adapter.sources)

        self._event_bus.emit(EventType.TOPOLOGY_CHANGED)

    def update_source_health(self, source_id: str, health: HealthResult) -> None:
        """Update health status for a source."""
        if source_id not in self._sources:
            return

        old_health = self._source_health.get(source_id)
        self._source_health[source_id] = health

        if not old_health or old_health.source_state != health.source_state:
            self._event_bus.emit(
                EventType.SOURCE_STATE_CHANGED,
                payload={"source_id": source_id, "state": health.source_state},
            )

        if not old_health or old_health.signal_present != health.signal_present:
            event_type = (
                EventType.SOURCE_SIGNAL_DETECTED
                if health.signal_present
                else EventType.SOURCE_SIGNAL_LOST
            )
            self._event_bus.emit(
                event_type,
                payload={"source_id": source_id},
            )

    def get_source_health(self, source_id: str) -> HealthResult | None:
        """Get the latest health status for a source."""
        return self._source_health.get(source_id)

    def get_adapter(self, adapter_id: str) -> AdapterInfo | None:
        """Get adapter info."""
        return self._adapters.get(adapter_id)

    def get_source(self, source_id: str) -> SourceDescriptor | None:
        """Get source info."""
        return self._sources.get(source_id)

    def list_adapters(self) -> list[AdapterInfo]:
        """List all registered adapters."""
        return list(self._adapters.values())

    def list_sources(self) -> list[SourceDescriptor]:
        """List all available sources."""
        return list(self._sources.values())
