"""Mock renderer adapter for benchmarking when no real speakers are available."""

import asyncio
from collections.abc import Sequence
from typing import Any
from bridge_core.adapters.base import RendererAdapter, TargetDescriptor

class MockTargetDescriptor(TargetDescriptor):
    def __init__(self, target_id: str, display_name: str):
        self._target_id = target_id
        self._display_name = display_name
        self.is_available = True
        self.is_active = False
        self.is_preferred = False

    @property
    def target_id(self) -> str: return self._target_id
    @property
    def renderer(self) -> str: return "mock"
    @property
    def target_type(self) -> str: return "speaker"
    @property
    def display_name(self) -> str: return self._display_name
    @property
    def members(self) -> list[str]: return [self._target_id]
    @property
    def coordinator_id(self) -> str: return self._target_id

class MockRendererAdapter(RendererAdapter):
    def __init__(self, event_bus=None):
        self._event_bus = event_bus
        self._targets = {
            "mock-speaker": MockTargetDescriptor("mock-speaker", "Mock Speaker")
        }

    def id(self) -> str: return "mock-renderer"

    async def list_targets(self) -> Sequence[TargetDescriptor]:
        return list(self._targets.values())

    async def prepare_target(self, target_id: str) -> dict[str, Any]:
        return {"success": True}

    async def play_stream(self, target_id: str, stream_url: str, metadata=None) -> dict[str, Any]:
        return {"success": True}

    async def stop(self, target_id: str) -> dict[str, Any]:
        return {"success": True}

    async def set_volume(self, target_id: str, volume: float) -> dict[str, Any]:
        return {"success": True}

    async def heal(self, target_id: str) -> dict[str, Any]:
        return {"success": True}
