"""Sonos renderer adapter."""

from collections.abc import Sequence

from bridge_core.adapters.base import RendererAdapter, TargetDescriptor

from .heal import HealController
from .playback import PlaybackController


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
        self._playback = PlaybackController()
        self._heal = HealController()

    def id(self) -> str:
        return "sonos-renderer-v1"

    async def list_targets(self) -> Sequence[TargetDescriptor]:
        return self._targets

    async def get_topology(self) -> dict[str, str | list[str] | bool]:
        return {
            "renderer": "sonos",
            "targets": [t.target_id for t in self._targets],
            "discovered": self._discovered,
        }

    async def prepare_target(self, target_id: str) -> dict[str, str]:
        return {"success": "true", "target_id": target_id}

    async def play_stream(
        self,
        target_id: str,
        stream_url: str,
        metadata: dict[str, str] | None = None,
    ) -> dict[str, str]:
        try:
            self._playback.play_stream(target_id, stream_url, metadata)
            return {
                "success": "true",
                "target_id": target_id,
                "stream_url": stream_url,
            }
        except Exception as e:
            return {
                "success": "false",
                "target_id": target_id,
                "stream_url": stream_url,
                "error": str(e),
            }

    async def stop(self, target_id: str) -> dict[str, str]:
        try:
            self._playback.stop(target_id)
            return {"success": "true", "target_id": target_id}
        except Exception as e:
            return {"success": "false", "target_id": target_id, "error": str(e)}

    async def set_volume(self, target_id: str, volume: float) -> dict[str, str | float]:
        try:
            self._playback.set_volume(target_id, volume)
            return {"success": "true", "target_id": target_id, "volume": volume}
        except Exception as e:
            return {"success": "false", "target_id": target_id, "volume": volume, "error": str(e)}

    async def heal(self, target_id: str) -> dict[str, str | bool]:
        # For full integration, this would retrieve the specific SoCo device.
        # Here we attempt to heal using the available tools, assuming the playback
        # controller has the device registered.
        device = self._playback.get_device(target_id)
        if not device:
            return {"success": "false", "target_id": target_id, "error": "Device not found"}

        expected_coord_id = self._heal.get_expected_coordinator(target_id)
        coordinator_device = None
        if expected_coord_id:
            coordinator_device = self._playback.get_device(expected_coord_id)

        healed = await self._heal.heal_group_membership(target_id, device, coordinator_device)
        return {"success": str(healed).lower(), "target_id": target_id, "healed": healed}
