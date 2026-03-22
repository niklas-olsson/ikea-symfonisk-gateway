"""Media player platform for IKEA SYMFONISK Gateway."""

from __future__ import annotations

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
    _attr_supported_features = MediaPlayerEntityFeature.STOP

    def __init__(self, coordinator: SymfoniskCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": f"SYMFONISK Gateway ({coordinator.host})",
            "manufacturer": "IKEA",
            "model": "SYMFONISK Gateway",
        }

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self.coordinator.last_update_success and self.coordinator.data.health.get("status") == "ok"

    @property
    def state(self) -> MediaPlayerState:
        """Return the state of the player."""
        if not self.coordinator.data.sessions:
            return MediaPlayerState.IDLE

        active_session = self.coordinator.data.sessions[0]
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
    def media_title(self) -> str | None:
        """Title of current playing media."""
        if not self.coordinator.data.sessions:
            return None

        session = self.coordinator.data.sessions[0]
        return f"Playback {session.get('session_id')}"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return entity specific state attributes."""
        if not self.coordinator.data.sessions:
            return {}

        session = self.coordinator.data.sessions[0]
        media_status = session.get("media_status") or {}
        return {
            "session_id": session.get("session_id"),
            "source_id": session.get("source_id"),
            "target_id": session.get("target_id"),
            "stream_profile": session.get("stream_profile"),
            "requested_stream_profile": session.get("requested_stream_profile"),
            "selected_stream_profile": session.get("selected_stream_profile"),
            "effective_stream_profile": session.get("effective_stream_profile"),
            "bridge_state": session.get("state"),
            "presentation_state": session.get("presentation_state"),
            "presentation_detail": session.get("presentation_detail"),
            "media_state": media_status.get("state"),
            "media_reason": media_status.get("reason"),
            "delivery_profile": media_status.get("delivery_profile"),
            "effective_delivery_profile": media_status.get("effective_delivery_profile"),
            "auto_fell_back_to_stable": media_status.get("auto_fell_back_to_stable"),
            "fallback_reason": media_status.get("fallback_reason"),
            "source_health": media_status.get("source_health"),
            "last_error": session.get("last_error"),
        }

    async def async_media_stop(self) -> None:
        """Send stop command."""
        if self.coordinator.data.sessions:
            session_id = self.coordinator.data.sessions[0]["session_id"]
            await self.coordinator.stop_session(session_id)

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return f"{self.coordinator.host}_media_player"
