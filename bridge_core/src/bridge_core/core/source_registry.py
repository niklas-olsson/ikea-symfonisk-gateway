"""Source registry - tracks adapters and available sources."""

from typing import Any

from ingress_sdk.types import AdapterCapabilities, SourceDescriptor, HealthResult


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
        return {
            "adapter_id": self.adapter_id,
            "platform": self.platform,
            "version": self.version,
            "capabilities": self.capabilities.model_dump(),
            "sources": [s.model_dump() for s in self.sources.values()],
        }


class SourceRegistry:
    """Registry for ingress adapters and their sources."""

    def __init__(self) -> None:
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

    def update_source_health(self, source_id: str, health: HealthResult) -> None:
        """Update health status for a source."""
        if source_id in self._sources:
            self._source_health[source_id] = health

    def get_source_health(self, source_id: str) -> HealthResult | None:
        """Get health status for a source."""
        return self._source_health.get(source_id)

    def adapter_connected(self, adapter_id: str) -> bool:
        """Check if an adapter is currently connected/registered."""
        return adapter_id in self._adapters

    def unregister_adapter(self, adapter_id: str) -> None:
        """Unregister an adapter and its sources."""
        adapter = self._adapters.pop(adapter_id, None)
        if adapter:
            for source_id in adapter.sources:
                self._sources.pop(source_id, None)
                self._source_health.pop(source_id, None)

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
