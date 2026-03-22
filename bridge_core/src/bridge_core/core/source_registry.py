"""Source registry - tracks adapters and available sources."""

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from ingress_sdk.base import FrameSink, IngressAdapter
from ingress_sdk.types import (
    AdapterCapabilities,
    HealthResult,
    PairingResult,
    PrepareResult,
    SourceCapabilities,
    SourceDescriptor,
    SourceType,
    StartResult,
)

from bridge_core.core.errors import SOURCE_ADAPTER_PLATFORM_MISMATCH
from bridge_core.core.event_bus import BridgeEvent, EventBus, EventType

logger = logging.getLogger(__name__)


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


@dataclass(frozen=True)
class SourceBinding:
    """Resolved source binding information."""

    source: SourceDescriptor
    adapter_info: AdapterInfo
    local_source_id: str


class SourceRegistry:
    """Registry for ingress adapters and their sources."""

    _TYPE_SLUGS = {
        "system_audio": "system",
        "system_output": "system",
        "bluetooth_audio": "bluetooth",
        "line_in": "line-in",
        "microphone": "microphone",
        "file_replay": "file",
        "synthetic_test_source": "synthetic",
    }

    def __init__(self, event_bus: EventBus, config_store: Any | None = None, metrics: Any | None = None) -> None:
        self._event_bus = event_bus
        self._config_store = config_store
        self._metrics = metrics
        self._session_manager: Any | None = None
        self._adapters: dict[str, AdapterInfo] = {}
        self._sources: dict[str, SourceDescriptor] = {}
        self._source_to_adapter: dict[str, str] = {}
        self._source_health: dict[str, HealthResult] = {}
        self._event_bus.subscribe_handler(self._handle_topology_changed, EventType.TOPOLOGY_CHANGED)

    def set_session_manager(self, session_manager: Any) -> None:
        """Set the session manager reference for active state tracking."""
        self._session_manager = session_manager

    def refresh_sources(self) -> None:
        """Force a refresh of sources from all adapters."""
        for adapter_id, adapter_info in self._adapters.items():
            if adapter_info.adapter:
                sources = adapter_info.adapter.list_sources()
                self.update_adapter_sources(adapter_id, sources)

    async def _handle_topology_changed(self, event: BridgeEvent) -> None:
        """Handle topology changed events from adapters."""
        if self._metrics:
            self._metrics.increment("topology_event_received_count")
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
        adapter.sources = self._build_registered_source_map(adapter_id, platform, sources)
        self._adapters[adapter_id] = adapter
        self._sources.update(adapter.sources)
        self._source_to_adapter.update({source_id: adapter_id for source_id in adapter.sources})
        self._log_registered_sources(adapter_id, adapter.sources.values())

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
                self._source_to_adapter.pop(source_id, None)
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

        new_source_map = self._build_registered_source_map(adapter_id, adapter.platform, sources)

        # Check if sources actually changed to avoid infinite loop
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
            self._source_to_adapter.pop(s_id, None)
            self._source_health.pop(s_id, None)

        # Update adapter and global map
        adapter.sources = new_source_map
        self._sources.update(adapter.sources)
        self._source_to_adapter.update({source_id: adapter_id for source_id in adapter.sources})
        self._log_registered_sources(adapter_id, adapter.sources.values())

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
                payload={
                    "source_id": source_id,
                    "state": health.source_state,
                    "health": health.model_dump(),
                },
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

    def probe_source_health(self, source_id: str) -> HealthResult | None:
        """Probes source health and updates the registry."""
        binding = self.resolve_source(source_id)
        if not binding or not binding.adapter_info.adapter:
            return None

        health = binding.adapter_info.adapter.probe_health(binding.local_source_id)
        self.update_source_health(source_id, health)
        return health

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
        """List all sources, including preferred ones that might be offline."""
        preferred_id = None
        if self._config_store:
            preferred_id = self._config_store.get("preferred_source_id")

        active_source_ids = set()
        if self._session_manager:
            active_source_ids = {
                sess.source_id for sess in self._session_manager.list()
                if sess.state.value in ("playing", "starting", "preparing", "healing", "degraded")
            }

        results = []
        for s in self._sources.values():
            copy = s.model_copy()
            copy.is_preferred = (s.source_id == preferred_id)
            copy.is_active = (s.source_id in active_source_ids)
            copy.is_available = True
            results.append(copy)

        # If the preferred source is missing, add a stub for it
        if preferred_id and preferred_id not in self._sources:
            # We don't have the full descriptor, but we can provide the ID and a hint
            results.append(
                SourceDescriptor(
                    source_id=preferred_id,
                    source_type=SourceType.SYSTEM_AUDIO,
                    display_name=f"Missing Preferred Source ({preferred_id})",
                    platform="any",
                    capabilities=SourceCapabilities(sample_rates=[48000], channels=[2], bit_depths=[16]),
                    is_preferred=True,
                    is_active=False,
                    is_available=False,
                )
            )

        return results

    def get_source_adapter(self, source_id: str) -> AdapterInfo | None:
        """Find which adapter owns this source."""
        adapter_id = self._source_to_adapter.get(source_id)
        if adapter_id is None:
            return None
        return self._adapters.get(adapter_id)

    def resolve_source(self, source_id: str) -> SourceBinding | None:
        """Resolve a registered source into its descriptor and owning adapter."""
        source = self.get_source(source_id)
        adapter_info = self.get_source_adapter(source_id)
        if not source or not adapter_info:
            return None
        return SourceBinding(
            source=source,
            adapter_info=adapter_info,
            local_source_id=self._get_adapter_local_source_id(source),
        )

    def _validate_platform(self, source: SourceDescriptor, adapter_info: AdapterInfo) -> tuple[bool, str | None]:
        """Validate that the source and adapter platforms match."""
        source_platform = source.platform
        adapter_platform = adapter_info.platform

        # If either is 'any', it's always valid
        if source_platform == "any" or adapter_platform == "any":
            return True, None

        if source_platform != adapter_platform:
            msg = f"{source_platform.capitalize()} source ({source.source_id}) was bound to {adapter_platform.capitalize()} audio adapter."
            return False, msg

        return True, None

    def prepare_source(self, source_id: str) -> PrepareResult:
        """Prepare a source for capture."""
        binding = self.resolve_source(source_id)
        if not binding:
            return PrepareResult(success=False, source_id=source_id, error="Source not found")

        # Platform validation
        is_valid, error_msg = self._validate_platform(binding.source, binding.adapter_info)
        if not is_valid:
            return PrepareResult(
                success=False,
                source_id=source_id,
                code=SOURCE_ADAPTER_PLATFORM_MISMATCH,
                message=error_msg or "Platform mismatch",
            )

        if binding.adapter_info.adapter:
            return binding.adapter_info.adapter.prepare(binding.local_source_id)
        else:
            return PrepareResult(
                success=False,
                source_id=source_id,
                error=f"Adapter {binding.adapter_info.adapter_id} does not support direct prepare",
            )

    def start_source(self, source_id: str, frame_sink: FrameSink) -> StartResult:
        """Start capturing from a source."""
        binding = self.resolve_source(source_id)
        if not binding:
            return StartResult(success=False, message="Source not found")
        if not binding.adapter_info.adapter:
            return StartResult(success=False, message="Adapter not found or not connected")

        # Platform validation
        is_valid, error_msg = self._validate_platform(binding.source, binding.adapter_info)
        if not is_valid:
            return StartResult(
                success=False,
                code=SOURCE_ADAPTER_PLATFORM_MISMATCH,
                message=error_msg or "Platform mismatch",
            )

        return binding.adapter_info.adapter.start(binding.local_source_id, frame_sink)

    def stop_source(self, source_id: str, adapter_session_id: str) -> None:
        """Stop capturing from a source."""
        binding = self.resolve_source(source_id)
        if binding and binding.adapter_info.adapter:
            binding.adapter_info.adapter.stop(adapter_session_id)

    def _build_registered_source_map(
        self, adapter_id: str, adapter_platform: str, sources: list[SourceDescriptor]
    ) -> dict[str, SourceDescriptor]:
        """Canonicalize adapter-local source descriptors for registry use."""
        registered_sources: dict[str, SourceDescriptor] = {}
        for source in sources:
            registered_source = self._canonicalize_source(adapter_id, adapter_platform, source)
            if registered_source.source_id in registered_sources:
                raise ValueError(f"Adapter {adapter_id} produced duplicate canonical source ID {registered_source.source_id}")
            existing_owner = self._source_to_adapter.get(registered_source.source_id)
            if existing_owner is not None and existing_owner != adapter_id:
                raise ValueError(f"Canonical source ID {registered_source.source_id} is already owned by adapter {existing_owner}")
            registered_sources[registered_source.source_id] = registered_source
        return registered_sources

    def _canonicalize_source(self, adapter_id: str, adapter_platform: str, source: SourceDescriptor) -> SourceDescriptor:
        """Convert an adapter-local descriptor into a registry descriptor."""
        self._validate_source_descriptor(adapter_id, adapter_platform, source)
        local_source_id = source.local_source_id or source.source_id
        type_slug = self._get_type_slug(source)
        registered_source = source.model_copy(deep=True)
        registered_source.adapter_id = adapter_id
        registered_source.local_source_id = local_source_id
        registered_source.source_id = self._build_canonical_source_id(adapter_id, type_slug, local_source_id)
        return registered_source

    def _get_adapter_local_source_id(self, source: SourceDescriptor) -> str:
        """Return the adapter-local ID for a registered source."""
        return source.local_source_id or source.source_id

    def _build_canonical_source_id(self, adapter_id: str, type_slug: str, local_source_id: str) -> str:
        """Build a canonical registry source ID."""
        return f"{adapter_id}:{type_slug}:{quote(local_source_id, safe='')}"

    def _get_type_slug(self, source: SourceDescriptor) -> str:
        """Resolve the shared canonical type slug for a source."""
        type_slug = self._TYPE_SLUGS.get(source.source_type.value)
        if type_slug is None:
            raise ValueError(f"Unsupported source type {source.source_type!r} for source {source.source_id}")
        return type_slug

    def _validate_source_descriptor(self, adapter_id: str, adapter_platform: str, source: SourceDescriptor) -> None:
        """Fail fast on invalid adapter-provided source descriptors."""
        local_source_id = source.local_source_id or source.source_id
        if not local_source_id:
            raise ValueError(f"Adapter {adapter_id} provided an empty local source ID")

        self._get_type_slug(source)

        source_platform = source.platform
        if source_platform != "any" and adapter_platform != "any" and source_platform != adapter_platform:
            raise ValueError(f"Adapter {adapter_id} platform {adapter_platform} does not match source platform {source_platform}")

    def _log_registered_sources(self, adapter_id: str, sources: Any) -> None:
        """Log resolved source ownership for diagnostics."""
        for source in sources:
            logger.info(
                "Registered source binding: canonical_source_id=%s adapter_id=%s local_source_id=%s source_type=%s platform=%s",
                source.source_id,
                adapter_id,
                source.local_source_id,
                source.source_type.value,
                source.platform,
            )

    def start_pairing(self, adapter_id: str, timeout_seconds: int = 60, candidate_mac: str | None = None) -> PairingResult:
        """Start pairing mode on an adapter."""
        adapter_info = self.get_adapter(adapter_id)
        if not adapter_info:
            return PairingResult(success=False, error=f"Adapter {adapter_id} not found")

        if not adapter_info.adapter:
            return PairingResult(success=False, error=f"Adapter {adapter_id} not active")

        if not adapter_info.capabilities.supports_pairing:
            return PairingResult(success=False, error=f"Adapter {adapter_id} does not support pairing")

        return adapter_info.adapter.start_pairing(timeout_seconds, candidate_mac=candidate_mac)

    def stop_pairing(self, adapter_id: str) -> PairingResult:
        """Stop pairing mode on an adapter."""
        adapter_info = self.get_adapter(adapter_id)
        if not adapter_info:
            return PairingResult(success=False, error=f"Adapter {adapter_id} not found")

        if not adapter_info.adapter:
            return PairingResult(success=False, error=f"Adapter {adapter_id} not active")

        return adapter_info.adapter.stop_pairing()
