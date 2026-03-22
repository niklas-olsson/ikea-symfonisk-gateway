"""Media player platform for IKEA SYMFONISK Gateway."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SymfoniskCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the media player platform."""
    coordinator: SymfoniskCoordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities([SymfoniskMediaPlayer(coordinator, entry)])


class SymfoniskMediaPlayer(CoordinatorEntity[SymfoniskCoordinator], MediaPlayerEntity):
    """Media player for SYMFONISK Gateway sessions."""

    _attr_name = "Playback"
    _attr_supported_features = (
        MediaPlayerEntityFeature.PLAY | MediaPlayerEntityFeature.STOP | MediaPlayerEntityFeature.SELECT_SOURCE
    )

    def __init__(self, coordinator: SymfoniskCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": f"SYMFONISK Gateway ({coordinator.host})",
            "manufacturer": "IKEA",
            "model": "SYMFONISK Gateway",
        }

    def _get_session(self) -> dict[str, Any] | None:
        """Return the session for the currently selected target."""
        target_id = self.coordinator.selected_target_id
        if not target_id:
            return None

        for session in self.coordinator.data.sessions:
            if session.get("target_id") == target_id and session.get("state") != "stopped":
                return session

        return None

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self.coordinator.last_update_success and self.coordinator.data.health.get("status") == "ok"

    @property
    def state(self) -> MediaPlayerState:
        """Return the state of the player."""
        active_session = self._get_session()
        if not active_session:
            return MediaPlayerState.IDLE

        pres_state = active_session.get("presentation_state")

        if pres_state == "playing":
            return MediaPlayerState.PLAYING
        if pres_state == "buffering":
            return MediaPlayerState.BUFFERING
        if pres_state == "error":
            return MediaPlayerState.IDLE
        if pres_state == "idle":
            return MediaPlayerState.IDLE

        # Fallback to internal state if presentation_state is not available
        state = active_session.get("state")
        if state == "playing":
            return MediaPlayerState.PLAYING
        if state in ("starting", "preparing", "healing"):
            return MediaPlayerState.BUFFERING
        if state == "degraded":
            return MediaPlayerState.ON
        if state == "failed":
            return MediaPlayerState.IDLE

        return MediaPlayerState.IDLE

    @property
    def source_list(self) -> list[str]:
        """List of available input sources."""
        return [s["display_name"] for s in self.coordinator.data.sources]

    @property
    def source(self) -> str | None:
        """Name of the current input source."""
        selected_id = self.coordinator.selected_source_id
        for s in self.coordinator.data.sources:
            if s["source_id"] == selected_id:
                return s["display_name"]
        return None

    @property
    def target_name(self) -> str | None:
        """Name of the current playback target."""
        selected_id = self.coordinator.selected_target_id
        for t in self.coordinator.data.targets:
            if t["target_id"] == selected_id:
                return t["display_name"]
        return None

    @property
    def media_title(self) -> str | None:
        """Title of current playing media."""
        source_name = self.source or "No Source"
        target_name = self.target_name or "No Speaker"

        if not self.coordinator.data.sessions:
            return f"Ready: {source_name} ➔ {target_name}"

        return f"Playing: {source_name} ➔ {target_name}"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return entity specific state attributes."""
        session = self._get_session()
        if not session:
            return {}

        media_status = session.get("media_status") or {}
        return {
            "session_id": session.get("session_id"),
            "source_id": session.get("source_id"),
            "target_id": session.get("target_id"),
            "stream_profile": session.get("stream_profile"),
            "effective_stream_profile": session.get("effective_stream_profile"),
            "bridge_state": session.get("state"),
            "presentation_state": session.get("presentation_state"),
            "presentation_detail": session.get("presentation_detail"),
            "media_state": media_status.get("state"),
            "media_reason": media_status.get("reason"),
            "effective_delivery_profile": media_status.get("effective_delivery_profile"),
            "last_error": session.get("last_error"),
        }

    async def async_media_play(self) -> None:
        """Send play command."""
        source_id = self.coordinator.selected_source_id
        target_id = self.coordinator.selected_target_id

        if not source_id or not target_id:
            _LOGGER.error("Source or Target not selected")
            return

        await self.coordinator.start_session(source_id, target_id)

    async def async_media_stop(self) -> None:
        """Send stop command."""
        target_id = self.coordinator.selected_target_id
        if not target_id:
            return

        for session in self.coordinator.data.sessions:
            if session.get("target_id") == target_id and session.get("state") != "stopped":
                await self.coordinator.stop_session(session["session_id"])

    async def async_select_source(self, source: str) -> None:
        """Select input source."""
        source_id = None
        for s in self.coordinator.data.sources:
            if s["display_name"] == source:
                source_id = s["source_id"]
                break

        if source_id:
            await self.coordinator.async_set_config("preferred_source_id", source_id)

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return f"{self.coordinator.host}_media_player"
