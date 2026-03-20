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
    SOURCE_STATE_CHANGED = "source.state.changed"
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
    ):
        self.event_id = f"evt_{uuid4().hex[:12]}"
        self.timestamp = datetime.now(UTC).isoformat()
        self.type = event_type.value
        self.severity = severity.value
        self.session_id = session_id
        self.payload = payload or {}

    def to_dict(self) -> dict[str, Any]:
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
        self._handlers: dict[EventType, list[EventHandler]] = {}
        self._queues: dict[asyncio.Queue[BridgeEvent], EventType | None] = {}

    def subscribe(
        self,
        handler: EventHandler,
        event_type: EventType | None = None,
    ) -> asyncio.Queue[BridgeEvent]:
        """Subscribe to events. Returns a queue for consuming events."""
        queue: asyncio.Queue[BridgeEvent] = asyncio.Queue()
        self._queues[queue] = event_type
        return queue

    def unsubscribe(self, queue: asyncio.Queue[BridgeEvent]) -> None:
        """Unsubscribe a queue from events."""
        self._queues.pop(queue, None)

    async def publish(self, event: BridgeEvent) -> None:
        """Publish an event to all subscribers."""
        for queue, event_type in self._queues.items():
            if event_type is None or event_type.value == event.type:
                await queue.put(event)

    def emit(
        self,
        event_type: EventType,
        payload: dict[str, Any] | None = None,
        severity: Severity = Severity.INFO,
        session_id: str | None = None,
    ) -> BridgeEvent:
        """Emit a new event synchronously."""
        event = BridgeEvent(
            event_type=event_type,
            payload=payload,
            severity=severity,
            session_id=session_id,
        )
        asyncio.create_task(self.publish(event))
        return event
