"""Sensor platform for IKEA SYMFONISK Gateway."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
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
    """Set up the sensor platform."""
    coordinator: SymfoniskCoordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities(
        [
            SymfoniskStatusSensor(coordinator, entry),
            SymfoniskUptimeSensor(coordinator, entry),
        ]
    )


class SymfoniskSensor(CoordinatorEntity[SymfoniskCoordinator], SensorEntity):
    """Base class for Symfonisk sensors."""

    def __init__(self, coordinator: SymfoniskCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": f"SYMFONISK Gateway ({coordinator.host})",
            "manufacturer": "IKEA",
            "model": "SYMFONISK Gateway",
        }


class SymfoniskStatusSensor(SymfoniskSensor):
    """Sensor for bridge status."""

    _attr_name = "Status"
    _attr_unique_id = "status"

    @property
    def native_value(self) -> str | None:
        """Return the state of the sensor."""
        return self.coordinator.data.health.get("status")

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return f"{self.coordinator.host}_status"


class SymfoniskUptimeSensor(SymfoniskSensor):
    """Sensor for bridge uptime."""

    _attr_name = "Uptime"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    @property
    def native_value(self) -> float | None:
        """Return the state of the sensor."""
        return self.coordinator.data.health.get("uptime")

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return f"{self.coordinator.host}_uptime"
