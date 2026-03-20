"""Tests for the EventBus component."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from bridge_core.core.event_bus import BridgeEvent, EventBus, EventType


@pytest.mark.anyio
async def test_event_bus_queue_subscription():
    """Test that events are correctly delivered to a subscriber queue."""
    bus = EventBus()
    queue = bus.subscribe(EventType.SESSION_CREATED)

    event = BridgeEvent(EventType.SESSION_CREATED, payload={"id": "123"})
    await bus.publish(event)

    received = await queue.get()
    assert received.type == EventType.SESSION_CREATED.value
    assert received.payload == {"id": "123"}


@pytest.mark.anyio
async def test_event_bus_handler_subscription():
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
async def test_event_bus_filtering():
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
async def test_event_bus_unsubscribe():
    """Test that unsubscribed consumers do not receive events."""
    bus = EventBus()
    queue = bus.subscribe()
    bus.unsubscribe(queue)

    await bus.publish(BridgeEvent(EventType.SESSION_CREATED))
    assert queue.empty()


@pytest.mark.anyio
async def test_event_bus_unsubscribe_handler():
    """Test that unsubscribed handlers do not receive events."""
    bus = EventBus()
    handler = AsyncMock()

    bus.subscribe_handler(handler)
    bus.unsubscribe_handler(handler)

    await bus.publish(BridgeEvent(EventType.SESSION_CREATED))
    await asyncio.sleep(0.1)
    handler.assert_not_called()
