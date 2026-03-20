"""Sonos renderer adapter."""

import asyncio
from collections.abc import Sequence
from typing import Any

import soco  # type: ignore[import-untyped]
from bridge_core.adapters.base import RendererAdapter, TargetDescriptor


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
        self._discovered: bool = False
        self._targets: list[SonosTargetDescriptor] = []

    def id(self) -> str:
        return "sonos-renderer-v1"

    async def list_targets(self) -> Sequence[TargetDescriptor]:
        """Discover Sonos devices and build target topology."""
        if self._discovered and self._targets:
            return self._targets

        loop = asyncio.get_running_loop()
        # soco.discover is a blocking network call
        players = await loop.run_in_executor(None, soco.discover)

        if not players:
            self._targets = []
            self._discovered = True
            return self._targets

        # Group players by their coordinator to identify logical targets
        groups: dict[str, SonosTargetDescriptor] = {}

        for player in players:
            group = player.group
            if not group or not group.coordinator:
                continue

            coordinator = group.coordinator
            coord_id = coordinator.uid

            if coord_id not in groups:
                # Identify if it's a stereo pair or a larger group
                members = [m.uid for m in group.members]
                target_type = "speaker"
                if len(members) == 2:
                    # In Sonos, stereo pairs are often reported as a single group
                    # with two members. We can call it a stereo_pair.
                    target_type = "stereo_pair"
                elif len(members) > 2:
                    target_type = "group"

                groups[coord_id] = SonosTargetDescriptor(
                    target_id=coord_id,
                    target_type=target_type,
                    display_name=coordinator.player_name,
                    members=members,
                    coordinator_id=coord_id,
                )

        self._targets = list(groups.values())
        self._discovered = True
        return self._targets

    async def get_topology(self) -> dict[str, Any]:
        return {
            "renderer": "sonos",
            "targets": [
                {
                    "target_id": t.target_id,
                    "display_name": t.display_name,
                    "type": t.target_type,
                    "members": t.members,
                    "coordinator": t.coordinator_id,
                }
                for t in self._targets
            ],
            "discovered": self._discovered,
        }

    async def prepare_target(self, target_id: str) -> dict[str, Any]:
        return {"success": "true", "target_id": target_id}

    async def play_stream(
        self,
        target_id: str,
        stream_url: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "success": "true",
            "target_id": target_id,
            "stream_url": stream_url,
        }

    async def stop(self, target_id: str) -> dict[str, Any]:
        return {"success": "true", "target_id": target_id}

    async def set_volume(self, target_id: str, volume: float) -> dict[str, Any]:
        return {"success": "true", "target_id": target_id, "volume": volume}

    async def heal(self, target_id: str) -> dict[str, Any]:
        return {"success": "true", "target_id": target_id, "healed": True}
