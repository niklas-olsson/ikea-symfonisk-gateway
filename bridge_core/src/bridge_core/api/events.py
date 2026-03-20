"""Server-Sent Events for bridge events."""

import asyncio

from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

router = APIRouter(tags=["events"])


async def event_generator() -> None:
    """Generate events for SSE clients."""
    while True:
        await asyncio.sleep(5)


@router.get("/events")
async def events() -> EventSourceResponse:
    return EventSourceResponse(event_generator())  # type: ignore[arg-type]
