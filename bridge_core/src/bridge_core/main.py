"""FastAPI application entry point."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from adapter_linux_audio import LinuxAudioAdapter
from adapter_synthetic import SyntheticAdapter
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

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
    session_manager = SessionManager(event_bus, source_registry, target_registry, publisher)

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
        adapter_instance=synthetic_adapter,
    )

    # Register Linux Audio adapter
    linux_audio_adapter = LinuxAudioAdapter()
    source_registry.register_adapter(
        adapter_id=linux_audio_adapter.id(),
        platform=linux_audio_adapter.platform(),
        version="0.1.0",
        capabilities=linux_audio_adapter.capabilities(),
        sources=linux_audio_adapter.list_sources(),
        adapter_instance=linux_audio_adapter,
    )

    # Start stream publisher in the background
    publisher_task = asyncio.create_task(publisher.start())

    yield

    # Clean up
    await publisher.stop()
    publisher_task.cancel()
    try:
        await publisher_task
    except (asyncio.CancelledError, SystemExit):
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

# Resolve the path to the web UI
# We expect the ui_web package to be installed or available in the path
try:
    import ui_web

    UI_DIR = Path(ui_web.__file__).parent
except ImportError:
    # Fallback for development if not installed as a package
    UI_DIR = Path(__file__).parent.parent.parent.parent / "ui_web" / "src" / "ui_web"


@app.get("/")
async def root() -> FileResponse:
    """Serve the web UI dashboard."""
    index_path = UI_DIR / "index.html"
    if not index_path.exists():
        # Last resort fallback if UI_DIR is wrong
        return FileResponse(Path("ui_web/src/ui_web/index.html"))
    return FileResponse(index_path)


# Also mount the directory for any potential static assets (if any were added)
if (UI_DIR).exists():
    app.mount("/static", StaticFiles(directory=UI_DIR), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8732)
