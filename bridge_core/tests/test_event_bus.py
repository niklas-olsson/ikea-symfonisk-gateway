"""Tests for the EventBus component."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bridge_core.core.event_bus import BridgeEvent, EventBus, EventType


@pytest.mark.anyio
async def test_event_bus_queue_subscription() -> None:
    """Test that events are correctly delivered to a subscriber queue."""
    bus = EventBus()
    queue = bus.subscribe(EventType.SESSION_CREATED)

    event = BridgeEvent(EventType.SESSION_CREATED, payload={"id": "123"})
    await bus.publish(event)

    received = await queue.get()
    assert received.type == EventType.SESSION_CREATED.value
    assert received.payload == {"id": "123"}


@pytest.mark.anyio
async def test_event_bus_handler_subscription() -> None:
    """Test that events are correctly delivered to a callback handler."""
    bus = EventBus()
    handler = AsyncMock()

    bus.subscribe_handler(handler, EventType.CONFIG_CHANGED)
    event = BridgeEvent(EventType.CONFIG_CHANGED, payload={"key": "val"})
    await bus.publish(event)

    # Allow some time for the task to be executed
    await asyncio.sleep(0.1)
    handler.assert_called_once_with(event)


@pytest.mark.anyio
async def test_event_bus_filtering() -> None:
    """Test that events are filtered correctly based on subscription type."""
    bus = EventBus()
    queue = bus.subscribe(EventType.SESSION_CREATED)

    event_ignored = BridgeEvent(EventType.CONFIG_CHANGED)
    event_targeted = BridgeEvent(EventType.SESSION_CREATED)

    await bus.publish(event_ignored)
    await bus.publish(event_targeted)

    received = await queue.get()
    assert received.type == EventType.SESSION_CREATED.value
    assert queue.empty()


@pytest.mark.anyio
async def test_event_bus_unsubscribe() -> None:
    """Test that unsubscribed consumers do not receive events."""
    bus = EventBus()
    queue = bus.subscribe()
    bus.unsubscribe(queue)

    await bus.publish(BridgeEvent(EventType.SESSION_CREATED))
    assert queue.empty()


@pytest.mark.anyio
async def test_event_bus_unsubscribe_handler() -> None:
    """Test that unsubscribed handlers do not receive events."""
    bus = EventBus()
    handler = AsyncMock()

    bus.subscribe_handler(handler)
    bus.unsubscribe_handler(handler)

    await bus.publish(BridgeEvent(EventType.SESSION_CREATED))
    await asyncio.sleep(0.1)
    handler.assert_not_called()


@pytest.mark.anyio
async def test_event_bus_emit_with_running_loop_schedules_publish() -> None:
    bus = EventBus()
    with patch.object(bus, "publish", new_callable=AsyncMock) as publish:
        event = bus.emit(EventType.SESSION_CREATED)
        await asyncio.sleep(0)

    publish.assert_awaited_once()
    assert event.type == EventType.SESSION_CREATED.value


def test_event_bus_emit_without_running_loop_skips_async_handlers(caplog: pytest.LogCaptureFixture) -> None:
    bus = EventBus()
    handler = AsyncMock()
    bus.subscribe_handler(handler, EventType.SESSION_CREATED)

    caplog.set_level("DEBUG")
    event = bus.emit(EventType.SESSION_CREATED)

    assert event.type == EventType.SESSION_CREATED.value
    handler.assert_not_called()
    assert "no running loop exists" in caplog.text


def test_event_bus_emit_without_running_loop_runs_sync_handlers() -> None:
    bus = EventBus()
    seen: list[BridgeEvent] = []

    def sync_handler(event: BridgeEvent) -> None:
        seen.append(event)

    bus.subscribe_handler(sync_handler, EventType.SESSION_CREATED)  # type: ignore[arg-type]

    event = bus.emit(EventType.SESSION_CREATED)

    assert seen == [event]


def test_event_bus_emit_without_running_loop_does_not_deliver_queue() -> None:
    bus = EventBus()
    queue = bus.subscribe(EventType.SESSION_CREATED)

    bus.emit(EventType.SESSION_CREATED)

    assert queue.empty()
