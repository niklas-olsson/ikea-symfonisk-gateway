"""Sonos renderer adapter."""

from collections.abc import Sequence
import asyncio

from bridge_core.adapters.base import RendererAdapter, TargetDescriptor

from .discovery import SonosDiscovery
from .topology import SonosTopology


class SonosTargetDescriptor(TargetDescriptor):
    """Describes a Sonos playback target."""

    def __init__(
        self,
        target_id: str,
        target_type: str,
        display_name: str,
        members: list[str],
        coordinator_id: str,
    ):
        self._target_id = target_id
        self._target_type = target_type
        self._display_name = display_name
        self._members = members
        self._coordinator_id = coordinator_id

    @property
    def target_id(self) -> str:
        return self._target_id

    @property
    def renderer(self) -> str:
        return "sonos"

    @property
    def target_type(self) -> str:
        return self._target_type

    @property
    def display_name(self) -> str:
        return self._display_name

    @property
    def members(self) -> list[str]:
        return self._members

    @property
    def coordinator_id(self) -> str:
        return self._coordinator_id


class SonosRendererAdapter(RendererAdapter):
    """Renderer adapter for Sonos/SYMFONISK speakers."""

    def __init__(self) -> None:
        self._discovery = SonosDiscovery()
        self._topology = SonosTopology(self._discovery)
        self._bg_started = False

    def id(self) -> str:
        return "sonos-renderer-v1"

    async def _ensure_discovery_started(self) -> None:
        """Start background discovery if not already running."""
        if not self._bg_started:
            self._discovery.start_background_discovery(interval=30)
            self._bg_started = True

    async def list_targets(self) -> Sequence[TargetDescriptor]:
        await self._ensure_discovery_started()

        topology_data = await self._topology.build()
        targets = []

        for t in topology_data.get("targets", []):
            descriptor = SonosTargetDescriptor(
                target_id=t["target_id"],
                target_type=t["target_type"],
                display_name=t["display_name"],
                members=t["members"],
                coordinator_id=t["coordinator_id"],
            )
            targets.append(descriptor)

        return targets

    from typing import Any
    async def get_topology(self) -> dict[str, Any]:
        await self._ensure_discovery_started()
        return await self._topology.build()

    async def prepare_target(self, target_id: str) -> dict[str, str]:
        return {"success": "true", "target_id": target_id}

    async def play_stream(
        self,
        target_id: str,
        stream_url: str,
        metadata: dict[str, str] | None = None,
    ) -> dict[str, str]:
        return {
            "success": "true",
            "target_id": target_id,
            "stream_url": stream_url,
        }

    async def stop(self, target_id: str) -> dict[str, str]:
        return {"success": "true", "target_id": target_id}

    async def set_volume(self, target_id: str, volume: float) -> dict[str, str | float]:
        return {"success": "true", "target_id": target_id, "volume": volume}

    async def heal(self, target_id: str) -> dict[str, str | bool]:
        return {"success": "true", "target_id": target_id, "healed": True}
