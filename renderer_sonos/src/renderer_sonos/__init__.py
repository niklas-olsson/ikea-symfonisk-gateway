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

        # 1. Try discovery with retries
        players_set: set[soco.SoCo] = set()
        for attempt in range(3):
            discovered = await loop.run_in_executor(None, soco.discover)
            if discovered:
                players_set.update(discovered)
                break
            if attempt < 2:
                await asyncio.sleep(1.0 * (attempt + 1))

        # 2. If discovery found nothing, try to verify existing players
        # This prevents losing all targets during transient network issues
        if not players_set and self._players:
            logger.info("Sonos discovery returned no players, verifying existing cache")
            verified_players = []
            for uid, player in self._players.items():
                try:

                    def _check_reachable(p: soco.SoCo) -> bool:
                        # Simple network check
                        p.get_current_transport_info()
                        return True

                    await self._run_with_retry(_check_reachable, player, max_retries=1)
                    verified_players.append(player)
                except Exception:
                    logger.debug("Cached player %s no longer reachable", uid)

            if verified_players:
                logger.info("Retained %d players from cache after discovery failure", len(verified_players))
                players_set = set(verified_players)

        if not players_set:
            if self._targets:
                self._targets = {}
                self._players = {}
                if self._event_bus:
                    self._event_bus.emit(EventType.RENDERER_DISCOVERY_CHANGED, payload={"count": 0})
            self._discovered = True
            return []

        # Update player cache
        new_players = {p.uid: p for p in players_set}

        # Group players by their coordinator to identify logical targets
        groups: dict[str, SonosTargetDescriptor] = {}

        for player in players_set:
            try:
                # Group access is a blocking network call in soco
                group = await loop.run_in_executor(None, lambda: player.group)
                if not group or not group.coordinator:
                    continue

                coordinator = group.coordinator
                coord_id = coordinator.uid

                if coord_id not in groups:
                    # Identify if it's a stereo pair or a larger group
                    members = [m.uid for m in group.members]
                    target_type = "speaker"
                    if len(members) == 2:
                        target_type = "stereo_pair"
                    elif len(members) > 2:
                        target_type = "group"

                    t = SonosTargetDescriptor(
                        target_id=coord_id,
                        target_type=target_type,
                        display_name=coordinator.player_name,
                        members=members,
                        coordinator_id=coord_id,
                    )
                    setattr(t, "is_available", True)
                    groups[coord_id] = t
            except Exception as e:
                logger.warning("Failed to process player %s during topology build: %s", player.uid, e)
                continue

        # Check for changes to emit event
        old_ids = set(self._targets.keys())
        new_ids = set(groups.keys())

        # Also check if members changed for existing targets
        members_changed = False
        for tid in old_ids.intersection(new_ids):
            if set(self._targets[tid].members) != set(groups[tid].members):
                members_changed = True
                break

        if old_ids != new_ids or members_changed:
            if self._event_bus:
                self._event_bus.emit(
                    EventType.RENDERER_DISCOVERY_CHANGED,
                    payload={
                        "count": len(groups),
                        "added": list(new_ids - old_ids),
                        "removed": list(old_ids - new_ids),
                    },
                )

        self._players = new_players
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
        if self._event_bus:
            self._event_bus.emit(
                EventType.HEAL_ATTEMPTED,
                payload={"target_id": target_id, "renderer": "sonos"},
            )

        # 1. Force a discovery refresh to ensure IP addresses and topology are current
        # This handles IP changes and role changes (e.g. coordinator moved)
        try:
            await self.list_targets()
        except Exception as e:
            logger.error("Discovery failed during heal for %s: %s", target_id, e)

        target = self._targets.get(target_id)
        if not target:
            return {"success": False, "error": f"Target {target_id} not found in topology after refresh"}

        try:
            coordinator = self._players.get(target.coordinator_id)
            if not coordinator:
                raise RuntimeError(f"Coordinator {target.coordinator_id} not found")

            # 2. Verify coordinator is reachable and still a coordinator
            def _verify_coordinator(p: soco.SoCo) -> None:
                # This will raise if unreachable
                p.get_current_transport_info()
                if not p.is_coordinator:
                    raise RuntimeError(f"Player {p.uid} is no longer a coordinator")

            try:
                await self._run_with_retry(_verify_coordinator, coordinator, max_retries=2)
            except Exception as e:
                logger.warning("Coordinator %s check failed: %s. Re-discovering...", target.coordinator_id, e)
                # If coordinator is invalid, we must re-discover and rebuild
                await self.list_targets()
                target = self._targets.get(target_id)
                if not target:
                    raise RuntimeError(f"Target {target_id} lost after re-discovery during heal")
                coordinator = self._players.get(target.coordinator_id)
                if not coordinator:
                    raise RuntimeError(f"Coordinator {target.coordinator_id} still not found after re-discovery")

            # 3. Ensure all members are joined to the coordinator
            for member_id in target.members:
                if member_id == target.coordinator_id:
                    continue

                member = self._players.get(member_id)
                if not member:
                    logger.warning("Member %s not found during heal of %s", member_id, target_id)
                    continue

                # Check if already in group
                def _check_and_join(m: soco.SoCo, c: soco.SoCo) -> None:
                    # In soco, 'join' is relatively safe to call even if already joined
                    # but we check to avoid unnecessary network traffic
                    if not m.group or not m.group.coordinator or m.group.coordinator.uid != c.uid:
                        m.join(c)

                await self._run_with_retry(_check_and_join, member, coordinator)

            # 4. Check if we should be playing and restart if needed
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
