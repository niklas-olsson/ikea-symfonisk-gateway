"""FastAPI application entry point."""

import asyncio
import inspect
import logging
import os
import platform
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from adapter_linux_audio import LinuxAudioAdapter
from adapter_linux_bluetooth import LinuxBluetoothAdapter
from adapter_synthetic import SyntheticAdapter
from adapter_windows_audio import WindowsAudioAdapter
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from shared.metrics import MetricsRegistry
from shared.subprocess import SubprocessRunner

from bridge_core.adapters.mock_renderer import MockRendererAdapter
from bridge_core.api import (
    adapters_router,
    bluetooth_router,
    config_router,
    discovery_router,
    events_router,
    health_router,
    sessions_router,
    sources_router,
    targets_router,
)
from bridge_core.core import (
    AutoPlayController,
    ConfigStore,
    EventBus,
    SessionManager,
    SourceRegistry,
    TargetRegistry,
)
from bridge_core.stream.publisher import StreamPublisher
from renderer_sonos import SonosRendererAdapter

logger = logging.getLogger(__name__)


def _schedule_adapter_startup(startup_result: object, adapter_name: str) -> asyncio.Future[object] | asyncio.Task[object] | None:
    if startup_result is None:
        return None

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.debug("Skipping %s startup scheduling because no running loop exists", adapter_name)
        return None

    if asyncio.isfuture(startup_result):
        return startup_result

    if inspect.iscoroutine(startup_result):
        return loop.create_task(startup_result, name=f"{adapter_name}.startup")

    logger.warning(
        "Skipping %s startup scheduling because on_startup() returned non-coroutine %r",
        adapter_name,
        type(startup_result).__name__,
    )
    return None


def register_ingress_adapters(
    source_registry: SourceRegistry,
    event_bus: EventBus,
    config_store: ConfigStore | None = None,
    host_platform: str | None = None,
) -> None:
    """Register built-in ingress adapters for the current host platform."""
    synthetic_adapter = SyntheticAdapter()
    source_registry.register_adapter(
        adapter_id=synthetic_adapter.id(),
        platform=synthetic_adapter.platform(),
        version="0.1.0",
        capabilities=synthetic_adapter.capabilities(),
        sources=synthetic_adapter.list_sources(),
        adapter_instance=synthetic_adapter,
    )

    normalized_platform = (host_platform or platform.system()).lower()

    if normalized_platform == "linux":
        metrics = getattr(source_registry, "_metrics", None)
        subprocess_runner = SubprocessRunner(metrics=metrics)
        linux_audio_adapter = LinuxAudioAdapter(event_bus, metrics=metrics, runner=subprocess_runner)
        source_registry.register_adapter(
            adapter_id=linux_audio_adapter.id(),
            platform=linux_audio_adapter.platform(),
            version="0.1.0",
            capabilities=linux_audio_adapter.capabilities(),
            sources=linux_audio_adapter.list_sources(),
            adapter_instance=linux_audio_adapter,
        )

        linux_bluetooth_adapter = LinuxBluetoothAdapter(
            event_bus,
            config_store=config_store,
            metrics=metrics,
            runner=subprocess_runner,
        )
        source_registry.register_adapter(
            adapter_id=linux_bluetooth_adapter.id(),
            platform=linux_bluetooth_adapter.platform(),
            version="0.1.0",
            capabilities=linux_bluetooth_adapter.capabilities(),
            sources=linux_bluetooth_adapter.list_sources(),
            adapter_instance=linux_bluetooth_adapter,
        )
        _schedule_adapter_startup(linux_bluetooth_adapter.on_startup(), "linux_bluetooth_adapter")
        return

    if normalized_platform == "windows":
        windows_audio_adapter = WindowsAudioAdapter()
        source_registry.register_adapter(
            adapter_id=windows_audio_adapter.id(),
            platform=windows_audio_adapter.platform(),
            version="0.1.0",
            capabilities=windows_audio_adapter.capabilities(),
            sources=windows_audio_adapter.list_sources(),
            adapter_instance=windows_audio_adapter,
        )
        return

    logger.info("Skipping platform-specific ingress adapters on unsupported host platform: %s", normalized_platform)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application lifespan."""
    # Initialize metrics first
    metrics = MetricsRegistry()
    app.state.metrics = metrics

    # Resolve configuration directory and ports
    config_dir = Path(os.environ.get("BRIDGE_CONFIG_DIR", "config"))
    config_dir.mkdir(parents=True, exist_ok=True)
    stream_port = int(os.environ.get("BRIDGE_STREAM_PORT", 8080))

    # Initialize core components
    config_store = ConfigStore(db_path=config_dir / "config.db")
    event_bus = EventBus(metrics=metrics)
    source_registry = SourceRegistry(event_bus, config_store=config_store, metrics=metrics)
    target_registry = TargetRegistry(event_bus, config_store=config_store)
    publisher = StreamPublisher(port=stream_port)
    session_manager = SessionManager(
        event_bus,
        source_registry,
        target_registry,
        publisher,
        config_store=config_store,
        metrics=metrics,
    )
    source_registry.set_session_manager(session_manager)
    target_registry.set_session_manager(session_manager)
    auto_play_controller = AutoPlayController(event_bus, session_manager, target_registry)

    # Store in app state
    app.state.config_store = config_store
    app.state.event_bus = event_bus
    app.state.source_registry = source_registry
    app.state.target_registry = target_registry
    app.state.stream_publisher = publisher
    app.state.session_manager = session_manager
    app.state.auto_play_controller = auto_play_controller

    # Register adapters
    sonos_adapter = SonosRendererAdapter(event_bus)
    await target_registry.register_adapter(sonos_adapter)

    # Register mock renderer for local benchmarking if Sonos is not available
    mock_renderer = MockRendererAdapter(event_bus)
    await target_registry.register_adapter(mock_renderer)

    register_ingress_adapters(source_registry, event_bus, config_store=config_store)

    # Start registries and publisher
    target_registry.start()
    publisher_task = asyncio.create_task(publisher.start())

    yield

    # Clean up
    await target_registry.stop()
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
app.include_router(discovery_router)
app.include_router(sessions_router)
app.include_router(events_router)
app.include_router(adapters_router)
app.include_router(bluetooth_router)
app.include_router(config_router)


@app.middleware("http")
async def count_api_requests(request: Request, call_next: object) -> object:
    """Middleware to count all incoming API requests."""
    if hasattr(app.state, "metrics"):
        app.state.metrics.increment("api_request_count")
    response = await call_next(request)  # type: ignore[operator]
    return response


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

    host = os.environ.get("BRIDGE_HOST", "0.0.0.0")
    port = int(os.environ.get("BRIDGE_PORT", 8732))
    uvicorn.run(app, host=host, port=port)
