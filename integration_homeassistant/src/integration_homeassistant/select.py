"""Select platform for IKEA SYMFONISK Gateway."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
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
    """Set up the select platform."""
    coordinator: SymfoniskCoordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities(
        [
            SymfoniskSourceSelect(coordinator, entry),
            SymfoniskTargetSelect(coordinator, entry),
        ]
    )


class SymfoniskSelect(CoordinatorEntity[SymfoniskCoordinator], SelectEntity):
    """Base class for Symfonisk select entities."""

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self.coordinator.last_update_success and self.coordinator.data.health.get("status") == "ok"

    def __init__(self, coordinator: SymfoniskCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": f"SYMFONISK Gateway ({coordinator.host})",
            "manufacturer": "IKEA",
            "model": "SYMFONISK Gateway",
        }


class SymfoniskSourceSelect(SymfoniskSelect):
    """Select entity for audio source selection."""

    _attr_name = "Source"
    _attr_entity_category = EntityCategory.CONFIG

    @property
    def options(self) -> list[str]:
        """Return available sources."""
        return [s["source_id"] for s in self.coordinator.data.sources]

    @property
    def current_option(self) -> str | None:
        """Return the current option."""
        return self.coordinator.data.config.get("preferred_source_id")

    async def async_select_option(self, option: str) -> None:
        """Update the current option."""
        await self.coordinator.async_set_config("preferred_source_id", option)

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return f"{self.coordinator.host}_source_select"


class SymfoniskTargetSelect(SymfoniskSelect):
    """Select entity for playback target selection."""

    _attr_name = "Speaker"
    _attr_entity_category = EntityCategory.CONFIG

    @property
    def options(self) -> list[str]:
        """Return available targets."""
        return [t["target_id"] for t in self.coordinator.data.targets]

    @property
    def current_option(self) -> str | None:
        """Return the current option."""
        return self.coordinator.data.config.get("preferred_target_id")

    async def async_select_option(self, option: str) -> None:
        """Update the current option."""
        await self.coordinator.async_set_config("preferred_target_id", option)

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return f"{self.coordinator.host}_target_select"
