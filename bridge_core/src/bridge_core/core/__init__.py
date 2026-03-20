"""Bridge core subsystems."""

from bridge_core.core.config_store import ConfigStore
from bridge_core.core.event_bus import EventBus
from bridge_core.core.pipeline import PipelineManager
from bridge_core.core.session_manager import SessionManager
from bridge_core.core.source_registry import SourceRegistry

__all__ = [
    "SessionManager",
    "SourceRegistry",
    "PipelineManager",
    "EventBus",
    "ConfigStore",
]
