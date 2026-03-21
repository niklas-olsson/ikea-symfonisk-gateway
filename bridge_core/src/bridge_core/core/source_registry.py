"""Source registry - tracks adapters and available sources."""

from typing import Any

from ingress_sdk.base import FrameSink, IngressAdapter
from ingress_sdk.types import (
    AdapterCapabilities,
    HealthResult,
    PairingResult,
    PrepareResult,
    SourceDescriptor,
    StartResult,
)

from bridge_core.core.event_bus import BridgeEvent, EventBus, EventType


class AdapterInfo:
    """Information about a connected ingress adapter."""

    def __init__(
        self,
        adapter_id: str,
        platform: str,
        version: str,
        capabilities: AdapterCapabilities,
        adapter: IngressAdapter | None = None,
    ):
        self.adapter_id = adapter_id
        self.platform = platform
        self.version = version
        self.capabilities = capabilities
        self.adapter = adapter
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
        self._event_bus.subscribe_handler(self._handle_topology_changed, EventType.TOPOLOGY_CHANGED)

    async def _handle_topology_changed(self, event: BridgeEvent) -> None:
        """Handle topology changed events from adapters."""
        if not event.payload:
            return

        adapter_id = event.payload.get("adapter_id")
        if not adapter_id:
            return

        adapter_info = self._adapters.get(adapter_id)
        if adapter_info and adapter_info.adapter and adapter_info.capabilities.supports_hotplug_events:
            sources = adapter_info.adapter.list_sources()
            self.update_adapter_sources(adapter_id, sources)

    def register_adapter(
        self,
        adapter_id: str,
        platform: str,
        version: str,
        capabilities: AdapterCapabilities,
        sources: list[SourceDescriptor],
        adapter_instance: IngressAdapter | None = None,
    ) -> None:
        """Register a new adapter and its sources."""
        adapter = AdapterInfo(adapter_id, platform, version, capabilities, adapter_instance)
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

        # Check if sources actually changed to avoid infinite loop
        new_source_map = {s.source_id: s for s in sources}
        if adapter.sources.keys() == new_source_map.keys():
            # Basic check for same source IDs.
            # In a more complete system we might also compare metadata/content.
            # For now, let's also check if display names changed.
            if all(adapter.sources[sid].display_name == s.display_name for sid, s in new_source_map.items()):
                return

        # Identify removed sources for cleanup
        new_source_ids = set(new_source_map.keys())
        removed_source_ids = set(adapter.sources.keys()) - new_source_ids

        for s_id in removed_source_ids:
            self._sources.pop(s_id, None)
            self._source_health.pop(s_id, None)

        # Update adapter and global map
        adapter.sources = new_source_map
        self._sources.update(adapter.sources)

        # Emit without adapter_id to avoid infinite recursion
        # (self._handle_topology_changed only reacts if adapter_id is present)
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
            event_type = EventType.SOURCE_SIGNAL_DETECTED if health.signal_present else EventType.SOURCE_SIGNAL_LOST
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

    def _get_adapter_info_for_source(self, source_id: str) -> AdapterInfo | None:
        """Find which adapter owns this source."""
        for adapter_info in self._adapters.values():
            if source_id in adapter_info.sources:
                return adapter_info
        return None

    def prepare_source(self, source_id: str) -> PrepareResult:
        """Prepare a source for capture."""
        source = self.get_source(source_id)
        if not source:
            return PrepareResult(success=False, source_id=source_id, error="Source not found")

        adapter_info = self._get_adapter_info_for_source(source_id)
        if not adapter_info:
            return PrepareResult(success=False, source_id=source_id, error="Adapter not found for source")

        if adapter_info.adapter:
            return adapter_info.adapter.prepare(source_id)
        else:
            return PrepareResult(
                success=False,
                source_id=source_id,
                error=f"Adapter {adapter_info.adapter_id} does not support direct prepare",
            )

    def start_source(self, source_id: str, frame_sink: FrameSink) -> StartResult:
        """Start capturing from a source."""
        source = self.get_source(source_id)
        if not source:
            return StartResult(success=False, message="Source not found")

        adapter_info = self._get_adapter_info_for_source(source_id)
        if not adapter_info or not adapter_info.adapter:
            return StartResult(success=False, message="Adapter not found or not connected")

        return adapter_info.adapter.start(source_id, frame_sink)

    def stop_source(self, source_id: str, adapter_session_id: str) -> None:
        """Stop capturing from a source."""
        adapter_info = self._get_adapter_info_for_source(source_id)
        if adapter_info and adapter_info.adapter:
            adapter_info.adapter.stop(adapter_session_id)

    def start_pairing(self, adapter_id: str, timeout_seconds: int = 60) -> PairingResult:
        """Start pairing mode on an adapter."""
        adapter_info = self.get_adapter(adapter_id)
        if not adapter_info:
            return PairingResult(success=False, error=f"Adapter {adapter_id} not found")

        if not adapter_info.adapter:
            return PairingResult(success=False, error=f"Adapter {adapter_id} not active")

        if not adapter_info.capabilities.supports_pairing:
            return PairingResult(success=False, error=f"Adapter {adapter_id} does not support pairing")

        return adapter_info.adapter.start_pairing(timeout_seconds)

    def stop_pairing(self, adapter_id: str) -> PairingResult:
        """Stop pairing mode on an adapter."""
        adapter_info = self.get_adapter(adapter_id)
        if not adapter_info:
            return PairingResult(success=False, error=f"Adapter {adapter_id} not found")

        if not adapter_info.adapter:
            return PairingResult(success=False, error=f"Adapter {adapter_id} not active")

        return adapter_info.adapter.stop_pairing()
