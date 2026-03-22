"""Event bus for bridge events."""

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)


class EventType(str, Enum):
    SESSION_CREATED = "session.created"
    SESSION_STARTING = "session.starting"
    SESSION_STARTED = "session.started"
    SESSION_STOPPING = "session.stopping"
    SESSION_STOPPED = "session.stopped"
    SESSION_FAILED = "session.failed"
    PUBLISHER_ACTIVE = "publisher.active"
    SOURCE_STATE_CHANGED = "source.state.changed"
    SOURCE_STARTED = "source.started"
    SOURCE_SIGNAL_DETECTED = "source.signal.detected"
    SOURCE_SIGNAL_LOST = "source.signal.lost"
    RENDERER_DISCOVERY_CHANGED = "renderer.discovery.changed"
    RENDERER_PLAYBACK_STARTED = "renderer.playback.started"
    RENDERER_PLAYBACK_FAILED = "renderer.playback.failed"
    TOPOLOGY_CHANGED = "topology.changed"
    HEAL_ATTEMPTED = "heal.attempted"
    HEAL_SUCCEEDED = "heal.succeeded"
    HEAL_FAILED = "heal.failed"
    STREAM_UNDERRUN = "stream.underrun"
    STREAM_OVERRUN = "stream.overrun"
    ADAPTER_REGISTERED = "adapter.registered"
    ADAPTER_UNREGISTERED = "adapter.unregistered"
    CONFIG_CHANGED = "config.changed"
    BLUETOOTH_ADAPTER_READY = "bluetooth.adapter.ready"
    BLUETOOTH_ADAPTER_FAILED = "bluetooth.adapter.failed"
    BLUETOOTH_PAIRING_WINDOW_OPENED = "bluetooth.pairing_window.opened"
    BLUETOOTH_PAIRING_WINDOW_CLOSED = "bluetooth.pairing_window.closed"
    BLUETOOTH_DEVICE_DISCOVERED = "bluetooth.device.discovered"
    BLUETOOTH_DEVICE_PAIRING_REQUESTED = "bluetooth.device.pairing_requested"
    BLUETOOTH_DEVICE_PAIRED = "bluetooth.device.paired"
    BLUETOOTH_DEVICE_PAIRING_REJECTED = "bluetooth.device.pairing_rejected"
    BLUETOOTH_DEVICE_TRUSTED = "bluetooth.device.trusted"
    BLUETOOTH_DEVICE_UNTRUSTED = "bluetooth.device.untrusted"
    BLUETOOTH_ADAPTER_STATUS_CHANGED = "bluetooth.adapter.status.changed"
    BLUETOOTH_DEVICE_SEEN = "bluetooth.device.seen"
    BLUETOOTH_DEVICE_CONNECTING = "bluetooth.device.connecting"
    BLUETOOTH_DEVICE_CONNECTED = "bluetooth.device.connected"
    BLUETOOTH_DEVICE_DISCONNECTED = "bluetooth.device.disconnected"
    BLUETOOTH_DEVICE_RECONNECTING = "bluetooth.device.reconnecting"
    BLUETOOTH_DEVICE_RECONNECT_SCHEDULED = "bluetooth.device.reconnect_scheduled"
    BLUETOOTH_DEVICE_RECONNECT_FAILED = "bluetooth.device.reconnect_failed"
    BLUETOOTH_SOURCE_AVAILABLE = "bluetooth.source.available"
    BLUETOOTH_SOURCE_UNAVAILABLE = "bluetooth.source.unavailable"
    BLUETOOTH_BACKEND_ERROR = "bluetooth.backend.error"


class Severity(str, Enum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class BridgeEvent:
    """A structured bridge event."""

    def __init__(
        self,
        event_type: EventType,
        payload: dict[str, Any] | None = None,
        severity: Severity = Severity.INFO,
        session_id: str | None = None,
        event_id: str | None = None,
        timestamp: str | None = None,
    ):
        self.event_id = event_id or f"evt_{uuid4().hex[:12]}"
        self.timestamp = timestamp or datetime.now(UTC).isoformat()
        self.type = event_type.value
        self.severity = severity.value
        self.session_id = session_id
        self.payload = payload or {}

    def to_dict(self) -> dict[str, Any]:
        """Convert the event to a dictionary."""
        return {
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            "type": self.type,
            "severity": self.severity,
            "session_id": self.session_id,
            "payload": self.payload,
        }


EventHandler = Callable[[BridgeEvent], Awaitable[None]]


class EventBus:
    """Publish-subscribe event bus for the bridge."""

    def __init__(self, metrics: Any | None = None) -> None:
        self._metrics = metrics
        self._handlers: dict[EventType | None, list[EventHandler]] = {}
        self._queues: dict[asyncio.Queue[BridgeEvent], EventType | None] = {}

    def subscribe(
        self,
        event_type: EventType | None = None,
    ) -> asyncio.Queue[BridgeEvent]:
        """Subscribe to events. Returns a queue for consuming events."""
        queue: asyncio.Queue[BridgeEvent] = asyncio.Queue()
        self._queues[queue] = event_type
        return queue

    def unsubscribe(self, queue: asyncio.Queue[BridgeEvent]) -> None:
        """Unsubscribe a queue from events."""
        self._queues.pop(queue, None)

    def subscribe_handler(
        self,
        handler: EventHandler,
        event_type: EventType | None = None,
    ) -> None:
        """Subscribe a callback handler to events."""
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        if handler not in self._handlers[event_type]:
            self._handlers[event_type].append(handler)

    def unsubscribe_handler(
        self,
        handler: EventHandler,
        event_type: EventType | None = None,
    ) -> None:
        """Unsubscribe a callback handler from events."""
        if event_type in self._handlers:
            if handler in self._handlers[event_type]:
                self._handlers[event_type].remove(handler)

    def _matching_handlers(self, event: BridgeEvent) -> list[EventHandler]:
        matching: list[EventHandler] = []
        concrete_type = EventType(event.type) if event.type in {member.value for member in EventType} else None
        for event_type in (None, concrete_type):
            if event_type in self._handlers:
                matching.extend(self._handlers[event_type])
        return matching

    async def _deliver_to_queues_async(self, event: BridgeEvent) -> None:
        for queue, event_type in self._queues.items():
            if event_type is None or event_type.value == event.type:
                await queue.put(event)

    def _dispatch_handler_tasks(self, event: BridgeEvent, loop: asyncio.AbstractEventLoop) -> None:
        for handler in self._matching_handlers(event):
            result = handler(event)
            if asyncio.isfuture(result):
                continue
            if inspect.iscoroutine(result):
                loop.create_task(result)

    def _emit_without_loop(self, event: BridgeEvent) -> None:
        for handler in self._matching_handlers(event):
            if asyncio.iscoroutinefunction(handler):
                logger.debug("Skipping async handler for event %s because no running loop exists", event.type)
                continue
            result = handler(event)
            if asyncio.isfuture(result):
                logger.debug("Skipping future-like handler result for event %s because no running loop exists", event.type)
                continue
            if inspect.iscoroutine(result):
                logger.debug("Skipping coroutine handler result for event %s because no running loop exists", event.type)
                result.close()

    async def publish(self, event: BridgeEvent) -> None:
        """Publish an event to all subscribers."""
        if self._metrics:
            self._metrics.increment("event_emitted_count")
        await self._deliver_to_queues_async(event)
        loop = asyncio.get_running_loop()
        self._dispatch_handler_tasks(event, loop)

    def emit(
        self,
        event_type: EventType | str,
        payload: dict[str, Any] | None = None,
        severity: Severity = Severity.INFO,
        session_id: str | None = None,
    ) -> BridgeEvent:
        """Emit a new event synchronously."""
        if isinstance(event_type, str):
            try:
                # Try to convert to EventType if possible for internal consistency
                event_type = EventType(event_type)
            except ValueError:
                # Keep as string if not a known EventType
                pass

        event = BridgeEvent(
            event_type=event_type,  # type: ignore[arg-type]
            payload=payload,
            severity=severity,
            session_id=session_id,
        )
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._emit_without_loop(event)
            return event

        loop.create_task(self.publish(event))
        return event
