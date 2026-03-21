"""Event bus for bridge events."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import uuid4


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
    BLUETOOTH_PAIRING_WINDOW_OPENED = "bluetooth.pairing_window.opened"
    BLUETOOTH_PAIRING_WINDOW_CLOSED = "bluetooth.pairing_window.closed"
    BLUETOOTH_DEVICE_PAIRED = "bluetooth.device.paired"
    BLUETOOTH_DEVICE_PAIRING_REJECTED = "bluetooth.device.pairing_rejected"
    BLUETOOTH_ADAPTER_STATUS_CHANGED = "bluetooth.adapter.status.changed"
    BLUETOOTH_DEVICE_SEEN = "bluetooth.device.seen"
    BLUETOOTH_DEVICE_CONNECTING = "bluetooth.device.connecting"
    BLUETOOTH_DEVICE_CONNECTED = "bluetooth.device.connected"
    BLUETOOTH_DEVICE_DISCONNECTED = "bluetooth.device.disconnected"
    BLUETOOTH_DEVICE_RECONNECT_SCHEDULED = "bluetooth.device.reconnect_scheduled"
    BLUETOOTH_DEVICE_RECONNECT_FAILED = "bluetooth.device.reconnect_failed"
    BLUETOOTH_SOURCE_AVAILABLE = "bluetooth.source.available"
    BLUETOOTH_SOURCE_UNAVAILABLE = "bluetooth.source.unavailable"


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

    def __init__(self) -> None:
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

    async def publish(self, event: BridgeEvent) -> None:
        """Publish an event to all subscribers."""
        # Deliver to queues
        for queue, event_type in self._queues.items():
            if event_type is None or event_type.value == event.type:
                await queue.put(event)

        # Deliver to handlers
        for et in [None, EventType(event.type) if event.type in [e.value for e in EventType] else None]:
            if et in self._handlers:
                for handler in self._handlers[et]:
                    asyncio.create_task(handler(event))  # type: ignore[arg-type]

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
        asyncio.create_task(self.publish(event))
        return event
