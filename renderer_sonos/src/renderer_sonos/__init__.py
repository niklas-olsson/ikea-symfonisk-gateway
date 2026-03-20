"""Sonos renderer adapter."""

import asyncio
import logging
from collections.abc import Sequence
from typing import Any

import soco  # type: ignore[import-untyped]
from bridge_core.adapters.base import RendererAdapter, TargetDescriptor
from bridge_core.core.event_bus import EventBus, EventType, Severity

logger = logging.getLogger(__name__)


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

    def __init__(self, event_bus: EventBus | None = None) -> None:
        self._event_bus = event_bus
        self._discovered: bool = False
        self._targets: dict[str, SonosTargetDescriptor] = {}
        self._players: dict[str, soco.SoCo] = {}
        # Track last played stream per target for healing
        self._last_played: dict[str, dict[str, Any]] = {}

    def id(self) -> str:
        return "sonos-renderer-v1"

    async def _run_with_retry(
        self,
        func: Any,
        *args: Any,
        max_retries: int = 3,
        initial_delay: float = 1.0,
        **kwargs: Any,
    ) -> Any:
        """Execute a blocking soco call in an executor with exponential backoff."""
        loop = asyncio.get_running_loop()
        last_exception = None

        for attempt in range(max_retries):
            try:
                return await loop.run_in_executor(None, lambda: func(*args, **kwargs))
            except Exception as e:
                last_exception = e
                logger.warning(
                    "Sonos operation %s failed (attempt %d/%d): %s",
                    func.__name__ if hasattr(func, "__name__") else "lambda",
                    attempt + 1,
                    max_retries,
                    e,
                )
                if attempt < max_retries - 1:
                    delay = initial_delay * (2**attempt)
                    await asyncio.sleep(delay)

        if last_exception:
            raise last_exception

    async def list_targets(self) -> Sequence[TargetDescriptor]:
        """Discover Sonos devices and build target topology."""
        loop = asyncio.get_running_loop()
        # soco.discover is a blocking network call
        players = await loop.run_in_executor(None, soco.discover)

        if not players:
            self._targets = {}
            self._players = {}
            self._discovered = True
            return []

        # Update player cache
        self._players = {p.uid: p for p in players}

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
                    # In soco, a stereo pair is often represented as a single visible player,
                    # but it might have 2 members if the hidden member is discovered.
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

        self._targets = groups
        self._discovered = True
        return list(self._targets.values())

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
                for t in self._targets.values()
            ],
            "discovered": self._discovered,
        }

    async def prepare_target(self, target_id: str) -> dict[str, Any]:
        """Prepare a Sonos target for playback."""
        # Always refresh discovery to ensure IP addresses are up-to-date
        await self.list_targets()
        player = self._players.get(target_id)

        if not player:
            return {"success": False, "error": f"Target {target_id} not found after discovery"}

        try:
            # Verify the player is reachable and in a good state by fetching transport info
            def _check_status(p: soco.SoCo) -> dict[str, str]:
                # This will raise an exception if the player is unreachable
                info: dict[str, str] = p.get_current_transport_info()
                # Check if it's still a coordinator
                if not p.is_coordinator:
                    raise RuntimeError("Target is no longer a coordinator")
                return info

            await self._run_with_retry(_check_status, player)
            return {"success": True, "target_id": target_id}
        except Exception as e:
            logger.error("Failed to prepare Sonos target %s: %s", target_id, e)
            return {"success": False, "error": f"Target {target_id} preparation failed: {e}"}

    async def play_stream(
        self,
        target_id: str,
        stream_url: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Start playback of a stream on a target."""
        player = self._players.get(target_id)
        target = self._targets.get(target_id)

        if not player or not target:
            return {"success": False, "error": f"Target {target_id} not found"}

        try:
            # 1. Enforce grouping before playback
            for member_id in target.members:
                if member_id == target.coordinator_id:
                    continue
                member = self._players.get(member_id)
                if not member:
                    continue

                def _join_if_needed(m: soco.SoCo, c: soco.SoCo) -> None:
                    if not m.group or not m.group.coordinator or m.group.coordinator.uid != c.uid:
                        m.join(c)

                await self._run_with_retry(_join_if_needed, member, player)

            # 2. Store playback state for healing
            self._last_played[target_id] = {
                "stream_url": stream_url,
                "metadata": metadata or {},
                "timestamp": asyncio.get_event_loop().time(),
            }

            # 3. Start playback with metadata if provided
            title = (metadata or {}).get("title", "IKEA SYMFONISK Bridge")
            await self._run_with_retry(player.play_uri, stream_url, title=title)

            return {
                "success": True,
                "target_id": target_id,
                "stream_url": stream_url,
            }
        except Exception as e:
            logger.error("Failed to play stream on %s: %s", target_id, e)
            return {"success": False, "error": f"Playback failed: {e}"}

    async def stop(self, target_id: str) -> dict[str, Any]:
        """Stop playback on a target."""
        player = self._players.get(target_id)
        if not player:
            return {"success": False, "error": f"Target {target_id} not found"}

        # Clear last played state to prevent healing from restarting playback
        self._last_played.pop(target_id, None)

        try:
            await self._run_with_retry(player.stop)
            return {"success": True, "target_id": target_id}
        except Exception as e:
            logger.error("Failed to stop playback on %s: %s", target_id, e)
            return {"success": False, "error": f"Stop failed: {e}"}

    async def set_volume(self, target_id: str, volume: float) -> dict[str, Any]:
        """Set volume on a target group (0.0 to 1.0)."""
        player = self._players.get(target_id)
        if not player:
            return {"success": False, "error": f"Target {target_id} not found"}

        try:
            # Sonos volume is 0-100
            sonos_volume = int(max(0.0, min(1.0, volume)) * 100)

            def _set_group_vol(p: soco.SoCo, v: int) -> None:
                # Set volume on the group if available, else individual
                if p.group:
                    p.group.volume = v
                else:
                    p.volume = v

            await self._run_with_retry(_set_group_vol, player, sonos_volume)
            return {"success": True, "target_id": target_id, "volume": volume}
        except Exception as e:
            logger.error("Failed to set volume on %s: %s", target_id, e)
            return {"success": False, "error": f"Set volume failed: {e}"}

    async def heal(self, target_id: str) -> dict[str, Any]:
        """Attempt to heal a target's group/topology."""
        # Always refresh targets to ensure topology is current
        await self.list_targets()

        target = self._targets.get(target_id)
        if not target:
            return {"success": False, "error": f"Target {target_id} not found in topology after refresh"}

        if self._event_bus:
            self._event_bus.emit(
                EventType.HEAL_ATTEMPTED,
                payload={"target_id": target_id, "renderer": "sonos"},
            )

        try:
            coordinator = self._players.get(target.coordinator_id)
            if not coordinator:
                raise RuntimeError(f"Coordinator {target.coordinator_id} not found")

            # 1. Ensure all members are joined to the coordinator
            for member_id in target.members:
                if member_id == target.coordinator_id:
                    continue

                member = self._players.get(member_id)
                if not member:
                    logger.warning("Member %s not found during heal of %s", member_id, target_id)
                    continue

                # Check if already in group
                def _check_and_join(m: soco.SoCo, c: soco.SoCo) -> None:
                    if not m.group or not m.group.coordinator or m.group.coordinator.uid != c.uid:
                        m.join(c)

                await self._run_with_retry(_check_and_join, member, coordinator)

            # 2. Check if we should be playing and restart if needed
            playback_restarted = False
            last_played = self._last_played.get(target_id)

            if last_played:

                def _check_and_restart(p: soco.SoCo, url: str, t: str) -> bool:
                    transport_info = p.get_current_transport_info()
                    if transport_info.get("current_transport_state") != "PLAYING":
                        p.play_uri(url, title=t)
                        return True
                    return False

                stream_url = last_played["stream_url"]
                title = last_played["metadata"].get("title", "IKEA SYMFONISK Bridge")
                playback_restarted = await self._run_with_retry(_check_and_restart, coordinator, stream_url, title)

            if self._event_bus:
                self._event_bus.emit(
                    EventType.HEAL_SUCCEEDED,
                    payload={
                        "target_id": target_id,
                        "renderer": "sonos",
                        "playback_restarted": playback_restarted,
                    },
                )

            return {
                "success": True,
                "target_id": target_id,
                "healed": True,
                "playback_restarted": playback_restarted,
            }

        except Exception as e:
            logger.error("Failed to heal target %s: %s", target_id, e)
            if self._event_bus:
                self._event_bus.emit(
                    EventType.HEAL_FAILED,
                    payload={"target_id": target_id, "renderer": "sonos", "error": str(e)},
                    severity=Severity.ERROR,
                )
            return {"success": False, "error": str(e)}
