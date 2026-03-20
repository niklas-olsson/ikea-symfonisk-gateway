"""FastAPI application entry point."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from adapter_synthetic import SyntheticAdapter
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from bridge_core.api import (
    events_router,
    health_router,
    sessions_router,
    sources_router,
    targets_router,
)
from bridge_core.core import EventBus, SessionManager, SourceRegistry, TargetRegistry
from bridge_core.stream.publisher import StreamPublisher
from renderer_sonos import SonosRendererAdapter


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application lifespan."""
    # Initialize core components
    event_bus = EventBus()
    source_registry = SourceRegistry(event_bus)
    target_registry = TargetRegistry(event_bus)
    publisher = StreamPublisher(port=8080)
    session_manager = SessionManager(event_bus, publisher)

    # Store in app state
    app.state.event_bus = event_bus
    app.state.source_registry = source_registry
    app.state.target_registry = target_registry
    app.state.stream_publisher = publisher
    app.state.session_manager = session_manager

    # Register adapters
    sonos_adapter = SonosRendererAdapter(event_bus)
    await target_registry.register_adapter(sonos_adapter)

    synthetic_adapter = SyntheticAdapter()
    source_registry.register_adapter(
        adapter_id=synthetic_adapter.id(),
        platform=synthetic_adapter.platform(),
        version="0.1.0",
        capabilities=synthetic_adapter.capabilities(),
        sources=synthetic_adapter.list_sources(),
    )

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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
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
