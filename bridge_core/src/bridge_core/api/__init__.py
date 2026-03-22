"""Bridge core API routes."""

from bridge_core.api.adapters import router as adapters_router
from bridge_core.api.bluetooth import router as bluetooth_router
from bridge_core.api.config import router as config_router
from bridge_core.api.discovery import router as discovery_router
from bridge_core.api.events import router as events_router
from bridge_core.api.health import router as health_router
from bridge_core.api.play import router as play_router
from bridge_core.api.sessions import router as sessions_router
from bridge_core.api.sources import router as sources_router
from bridge_core.api.targets import router as targets_router

__all__ = [
    "health_router",
    "sources_router",
    "targets_router",
    "sessions_router",
    "play_router",
    "events_router",
    "adapters_router",
    "bluetooth_router",
    "config_router",
    "discovery_router",
]
