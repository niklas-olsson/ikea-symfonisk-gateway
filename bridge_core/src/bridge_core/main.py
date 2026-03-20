"""FastAPI application entry point."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from bridge_core.api import (
    events_router,
    health_router,
    sessions_router,
    sources_router,
    targets_router,
)
from bridge_core.stream.publisher import StreamPublisher


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application lifespan."""
    # Initialize stream publisher
    publisher = StreamPublisher(port=8080)
    app.state.stream_publisher = publisher

    # Start stream publisher in the background
    publisher_task = asyncio.create_task(publisher.start())

    yield

    # Clean up
    # Note: publisher.start() uses uvicorn.Server.serve() which doesn't have a simple 'stop'
    # but the task will be cancelled when the main loop stops.
    publisher_task.cancel()
    try:
        await publisher_task
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title="IKEA SYMFONISK Bridge",
    description="Local bridge platform for IKEA SYMFONISK speakers",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(health_router)
app.include_router(sources_router)
app.include_router(targets_router)
app.include_router(sessions_router)
app.include_router(events_router)


@app.get("/")
async def root() -> dict[str, str]:
    return {
        "service": "ikea-symfonisk-gateway",
        "version": "0.1.0",
        "status": "running",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8732)
