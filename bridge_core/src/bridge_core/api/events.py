"""Server-Sent Events for bridge events."""

import asyncio
from collections.abc import AsyncGenerator

from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

router = APIRouter(tags=["events"])


async def event_generator() -> AsyncGenerator[dict[str, str], None]:
    """Generate events for SSE clients."""
    while True:
        yield {"event": "ping", "data": "pong"}
        await asyncio.sleep(5)


@router.get("/events")
async def events() -> EventSourceResponse:
    return EventSourceResponse(event_generator())
