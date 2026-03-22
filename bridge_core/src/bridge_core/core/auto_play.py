import asyncio
import logging

from bridge_core.core.event_bus import BridgeEvent, EventBus, EventType
from bridge_core.core.session_manager import STOP_REASON_PREFERRED, SessionManager
from bridge_core.core.target_registry import TargetRegistry

logger = logging.getLogger(__name__)


class AutoPlayController:
    """Listens for new audio sources and automatically starts playback sessions."""

    def __init__(
        self,
        event_bus: EventBus,
        session_manager: SessionManager,
        target_registry: TargetRegistry,
    ):
        self._event_bus = event_bus
        self._session_manager = session_manager
        self._target_registry = target_registry

        # Subscribe to new Bluetooth audio sources
        self._event_bus.subscribe_handler(
            self._on_bluetooth_source_available,
            EventType.BLUETOOTH_SOURCE_AVAILABLE,
        )

    async def _on_bluetooth_source_available(self, event: BridgeEvent) -> None:
        """Handle new Bluetooth sources by starting a session to the first available target."""
        source_id = event.payload.get("source_id")
        if not source_id:
            logger.error("BLUETOOTH_SOURCE_AVAILABLE event missing source_id payload")
            return

        # Give SourceRegistry a moment to process the concurrent TOPOLOGY_CHANGED event
        # and look for the canonical source ID that match this local source ID
        canonical_source_id = None
        for _ in range(5):
            for s in self._session_manager._source_registry.list_sources():
                if s.adapter_id == "linux-bluetooth-adapter" and s.local_source_id == source_id:
                    canonical_source_id = s.source_id
                    break
            if canonical_source_id:
                break
            await asyncio.sleep(0.3)
        else:
            logger.warning(f"SourceRegistry did not register {source_id} in time for auto-play")
            return

        source_id = canonical_source_id

        targets = self._target_registry.list_targets()
        if not targets:
            logger.warning(f"No playback target available to auto-play source {source_id}")
            return

        # Pick the first target available (could be improved with preferred target logic)
        target_id = targets[0].target_id

        logger.info(f"Auto-playing newly available Bluetooth source {source_id} to target {target_id}")

        try:
            # Create and start a new session, taking over if necessary
            session = await self._session_manager.create(
                source_id=source_id,
                target_id=target_id,
                takeover=True,
                takeover_reason=STOP_REASON_PREFERRED,
            )
            await self._session_manager.start_session(session.session_id)
        except Exception as e:
            logger.error(f"Failed to auto-play source {source_id}: {e}")
