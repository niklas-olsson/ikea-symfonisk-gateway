"""Bridge core subsystems."""

from bridge_core.core.auto_play import AutoPlayController
from bridge_core.core.config_store import ConfigStore
from bridge_core.core.event_bus import EventBus
from bridge_core.core.pipeline import PipelineManager
from bridge_core.core.session_manager import SessionManager
from bridge_core.core.source_registry import SourceRegistry
from bridge_core.core.target_registry import TargetRegistry

__all__ = [
    "SessionManager",
    "SourceRegistry",
    "TargetRegistry",
    "PipelineManager",
    "EventBus",
    "ConfigStore",
    "AutoPlayController",
]
