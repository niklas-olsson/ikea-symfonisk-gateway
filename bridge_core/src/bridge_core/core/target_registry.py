"""Target registry - tracks renderer adapters and available playback targets."""

import asyncio
import logging
import time
from typing import Any

from shared.normalization import normalize_for_comparison

from bridge_core.adapters.base import RendererAdapter, TargetDescriptor
from bridge_core.core.event_bus import EventBus, EventType

logger = logging.getLogger(__name__)


class TargetRegistry:
    """Registry for renderer adapters and their playback targets."""

    def __init__(self, event_bus: EventBus, config_store: Any | None = None) -> None:
        self._event_bus = event_bus
        self._config_store = config_store
        self._session_manager: Any | None = None
        self._adapters: dict[str, RendererAdapter] = {}
        self._targets: dict[str, TargetDescriptor] = {}
        self._target_to_adapter: dict[str, str] = {}
        self._target_last_seen: dict[str, float] = {}
        self._refresh_task: asyncio.Task[None] | None = None
        self._active = False

    def set_session_manager(self, session_manager: Any) -> None:
        """Set the session manager reference for active state tracking."""
        self._session_manager = session_manager

    def start(self) -> None:
        """Start background refresh task."""
        if self._active:
            return
        self._active = True
        self._refresh_task = asyncio.create_task(self._background_refresh())

    async def stop(self) -> None:
        """Stop background refresh task."""
        self._active = False
        if self._refresh_task:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
            self._refresh_task = None

    def _is_configured(self) -> bool:
        """Check if any preferred devices are configured."""
        if not self._config_store:
            return False
        return bool(self._config_store.get("preferred_source_id") or self._config_store.get("preferred_target_id"))

    async def _background_refresh(self) -> None:
        """Periodically refresh targets from all adapters."""
        while self._active:
            try:
                # Refresh every 5 minutes to reduce idle compute budget
                await asyncio.sleep(300)

                # Stop broad polling once a preferred device is configured
                if self._is_configured():
                    continue

                await self.refresh_targets()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in background target refresh: %s", e)
                await asyncio.sleep(10)  # Wait a bit before retrying on error

    async def register_adapter(self, adapter: RendererAdapter) -> None:
        """Register a new renderer adapter."""
        now = time.time()
        adapter_id = adapter.id()
        self._adapters[adapter_id] = adapter

        # Initial target discovery
        targets = await adapter.list_targets()
        for target in targets:
            setattr(target, "is_available", True)
            self._targets[target.target_id] = target
            self._target_to_adapter[target.target_id] = adapter_id
            self._target_last_seen[target.target_id] = now

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
        now = time.time()
        discovered_targets = {}
        discovered_target_to_adapter = {}

        for adapter_id, adapter in self._adapters.items():
            try:
                targets = await adapter.list_targets()
                for target in targets:
                    discovered_targets[target.target_id] = target
                    discovered_target_to_adapter[target.target_id] = adapter_id
                    self._target_last_seen[target.target_id] = now
            except Exception as e:
                logger.error("Failed to refresh targets from adapter %s: %s", adapter_id, e)

        # Update availability and handle stale targets
        all_target_ids = set(self._targets.keys()) | set(discovered_targets.keys())
        updated_targets = {}
        updated_target_to_adapter = {}

        changed = False
        for tid in all_target_ids:
            if tid in discovered_targets:
                target = discovered_targets[tid]
                setattr(target, "is_available", True)
                updated_targets[tid] = target
                updated_target_to_adapter[tid] = discovered_target_to_adapter[tid]

                # Check if it was previously unavailable or changed
                old_target = self._targets.get(tid)

                # Use normalized comparison for deep property checks
                norm_old = normalize_for_comparison(old_target.to_dict()) if old_target and hasattr(old_target, "to_dict") else None
                norm_new = normalize_for_comparison(target.to_dict()) if hasattr(target, "to_dict") else None

                if not old_target or not getattr(old_target, "is_available", True) or norm_old != norm_new:
                    changed = True
            else:
                # Target missing from discovery
                last_seen = self._target_last_seen.get(tid, 0)
                if now - last_seen > 300:  # 5 minutes stale cleanup
                    logger.info("Removing stale target %s", tid)
                    self._target_last_seen.pop(tid, None)
                    changed = True
                    continue

                # Mark as unavailable but keep in registry
                target = self._targets[tid]
                if getattr(target, "is_available", True):
                    setattr(target, "is_available", False)
                    changed = True
                updated_targets[tid] = target
                updated_target_to_adapter[tid] = self._target_to_adapter[tid]

        self._targets = updated_targets
        self._target_to_adapter = updated_target_to_adapter

        if changed:
            logger.info("Topology changed, emitting event")
            self._event_bus.emit(EventType.TOPOLOGY_CHANGED)

    def get_target(self, target_id: str) -> TargetDescriptor | None:
        """Get a target by ID."""
        return self._targets.get(target_id)

    def list_targets(self) -> list[TargetDescriptor]:
        """List all targets, including preferred ones that might be offline."""
        preferred_id = None
        if self._config_store:
            preferred_id = self._config_store.get("preferred_target_id")

        active_target_ids = set()
        if self._session_manager:
            active_target_ids = {
                sess.target_id
                for sess in self._session_manager.list()
                if sess.state.value in ("playing", "starting", "preparing", "healing", "degraded", "quiesced")
            }

        results = []
        for t in self._targets.values():
            # Inject lifecycle state for API consumption
            setattr(t, "is_preferred", t.target_id == preferred_id)
            setattr(t, "is_active", t.target_id in active_target_ids)
            setattr(t, "is_available", getattr(t, "is_available", True))
            results.append(t)

        return results

    def get_adapter_for_target(self, target_id: str) -> RendererAdapter | None:
        """Get the adapter responsible for a given target."""
        adapter_id = self._target_to_adapter.get(target_id)
        if not adapter_id:
            return None
        return self._adapters.get(adapter_id)

    async def heal_target(self, target_id: str) -> dict[str, Any]:
        """Attempt to heal a target's group/topology."""
        adapter = self.get_adapter_for_target(target_id)
        if not adapter:
            return {"success": False, "error": f"No adapter found for target {target_id}"}
        return await adapter.heal(target_id)

    async def set_volume(self, target_id: str, volume: float) -> dict[str, Any]:
        """Set volume on a target."""
        adapter = self.get_adapter_for_target(target_id)
        if not adapter:
            return {"success": False, "error": f"No adapter found for target {target_id}"}
        return await adapter.set_volume(target_id, volume)

    async def prepare_target(self, target_id: str) -> dict[str, Any]:
        """Prepare a target for playback."""
        adapter = self.get_adapter_for_target(target_id)
        if not adapter:
            return {"success": False, "error": f"No adapter found for target {target_id}"}
        return await adapter.prepare_target(target_id)

    async def play_stream(
        self,
        target_id: str,
        stream_url: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Start playback of a stream on a target."""
        adapter = self.get_adapter_for_target(target_id)
        if not adapter:
            return {"success": False, "error": f"No adapter found for target {target_id}"}
        return await adapter.play_stream(target_id, stream_url, metadata)

    async def stop_target(self, target_id: str) -> dict[str, Any]:
        """Stop playback on a target."""
        adapter = self.get_adapter_for_target(target_id)
        if not adapter:
            return {"success": False, "error": f"No adapter found for target {target_id}"}
        return await adapter.stop(target_id)
