"""Target registry - tracks renderer adapters and available playback targets."""

from bridge_core.adapters.base import RendererAdapter, TargetDescriptor
from bridge_core.core.event_bus import EventBus, EventType


class TargetRegistry:
    """Registry for renderer adapters and their playback targets."""

    def __init__(self, event_bus: EventBus) -> None:
        self._event_bus = event_bus
        self._adapters: dict[str, RendererAdapter] = {}
        self._targets: dict[str, TargetDescriptor] = {}
        self._target_to_adapter: dict[str, str] = {}

    async def register_adapter(self, adapter: RendererAdapter) -> None:
        """Register a new renderer adapter."""
        adapter_id = adapter.id()
        self._adapters[adapter_id] = adapter

        # Initial target discovery
        targets = await adapter.list_targets()
        for target in targets:
            self._targets[target.target_id] = target
            self._target_to_adapter[target.target_id] = adapter_id

        self._event_bus.emit(
            EventType.ADAPTER_REGISTERED,
            payload={"adapter_id": adapter_id, "type": "renderer"},
        )
        self._event_bus.emit(EventType.TOPOLOGY_CHANGED)

    def unregister_adapter(self, adapter_id: str) -> None:
        """Unregister a renderer adapter."""
        if adapter_id in self._adapters:
            self._adapters.pop(adapter_id)
            # Cleanup targets
            targets_to_remove = [t_id for t_id, a_id in self._target_to_adapter.items() if a_id == adapter_id]
            for t_id in targets_to_remove:
                self._targets.pop(t_id, None)
                self._target_to_adapter.pop(t_id, None)

            self._event_bus.emit(
                EventType.ADAPTER_UNREGISTERED,
                payload={"adapter_id": adapter_id, "type": "renderer"},
            )
            self._event_bus.emit(EventType.TOPOLOGY_CHANGED)

    async def refresh_targets(self) -> None:
        """Refresh targets from all registered adapters."""
        all_targets = {}
        new_target_to_adapter = {}
        for adapter_id, adapter in self._adapters.items():
            targets = await adapter.list_targets()
            for target in targets:
                all_targets[target.target_id] = target
                new_target_to_adapter[target.target_id] = adapter_id

        self._targets = all_targets
        self._target_to_adapter = new_target_to_adapter
        self._event_bus.emit(EventType.TOPOLOGY_CHANGED)

    def get_target(self, target_id: str) -> TargetDescriptor | None:
        """Get a target by ID."""
        return self._targets.get(target_id)

    def list_targets(self) -> list[TargetDescriptor]:
        """List all available targets."""
        return list(self._targets.values())

    def get_adapter_for_target(self, target_id: str) -> RendererAdapter | None:
        """Get the adapter responsible for a given target."""
        adapter_id = self._target_to_adapter.get(target_id)
        if not adapter_id:
            return None
        return self._adapters.get(adapter_id)
