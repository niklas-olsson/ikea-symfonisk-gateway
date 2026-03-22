"""Server-Sent Events for bridge events."""

import asyncio
import json
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from bridge_core.core import EventBus

router = APIRouter(tags=["events"])


async def event_generator(request: Request) -> AsyncGenerator[dict[str, str], None]:
    """Generate events for SSE clients."""
    event_bus: EventBus = request.app.state.event_bus
    queue = event_bus.subscribe()

    metrics = getattr(request.app.state, "metrics", None)
    try:
        while True:
            # Check if client disconnected
            if await request.is_disconnected():
                break

            try:
                # Wait for an event from the bus
                event = await asyncio.wait_for(queue.get(), timeout=1.0)
                if metrics:
                    metrics.increment("sse_event_count")
                yield {
                    "id": event.event_id,
                    "event": event.type,
                    "data": json.dumps(event.to_dict()),  # SSE data must be a string
                }
            except TimeoutError:
                # Send keep-alive comment
                yield {"comment": "keep-alive"}

    finally:
        event_bus.unsubscribe(queue)


@router.get("/events")
async def events(request: Request) -> EventSourceResponse:
    return EventSourceResponse(event_generator(request))
