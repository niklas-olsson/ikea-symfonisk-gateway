"""Button platform for IKEA SYMFONISK Gateway."""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
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
    """Set up the button platform."""
    coordinator: SymfoniskCoordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities(
        [
            SymfoniskStartButton(coordinator, entry),
            SymfoniskStopButton(coordinator, entry),
        ]
    )


class SymfoniskButton(CoordinatorEntity[SymfoniskCoordinator], ButtonEntity):
    """Base class for Symfonisk buttons."""

    def __init__(self, coordinator: SymfoniskCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": f"SYMFONISK Gateway ({coordinator.host})",
            "manufacturer": "IKEA",
            "model": "SYMFONISK Gateway",
        }


class SymfoniskStartButton(SymfoniskButton):
    """Button to start a playback session."""

    _attr_name = "Start Playback"

    async def async_press(self) -> None:
        """Handle the button press."""
        source_id = self.coordinator.selected_source_id
        target_id = self.coordinator.selected_target_id

        if not source_id or not target_id:
            _LOGGER.error("Source or Target not selected in coordinator")
            return

        await self.coordinator.start_session(source_id, target_id)

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return f"{self.coordinator.host}_start_button"


class SymfoniskStopButton(SymfoniskButton):
    """Button to stop all playback sessions."""

    _attr_name = "Stop All Playback"

    async def async_press(self) -> None:
        """Handle the button press."""
        for session in self.coordinator.data.sessions:
            if session.get("state") in ("playing", "starting", "preparing"):
                await self.coordinator.stop_session(session["session_id"])

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return f"{self.coordinator.host}_stop_button"
