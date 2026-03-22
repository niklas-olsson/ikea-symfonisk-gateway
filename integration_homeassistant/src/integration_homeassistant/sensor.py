"""Sensor platform for IKEA SYMFONISK Gateway."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTime
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
            SymfoniskSessionStateSensor(coordinator, entry),
            SymfoniskDeliveryProfileSensor(coordinator, entry),
            SymfoniskNegotiatedProfileSensor(coordinator, entry),
            SymfoniskFailureReasonSensor(coordinator, entry),
        ]
    )


class SymfoniskSensor(CoordinatorEntity[SymfoniskCoordinator], SensorEntity):
    """Base class for Symfonisk sensors."""

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


class SymfoniskStatusSensor(SymfoniskSensor):
    """Sensor for bridge status."""

    _attr_name = "Status"
    _attr_unique_id = "status"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

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
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    @property
    def native_value(self) -> float | None:
        """Return the state of the sensor."""
        return self.coordinator.data.health.get("uptime")

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return f"{self.coordinator.host}_uptime"


class SymfoniskSessionStateSensor(SymfoniskSensor):
    """Sensor for active playback state."""

    _attr_name = "Playback State"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> str | None:
        """Return the state of the sensor."""
        if not self.coordinator.data.sessions:
            return "no_active_session"

        return self.coordinator.data.sessions[0].get("presentation_state") or self.coordinator.data.sessions[0].get("state")

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return f"{self.coordinator.host}_session_state"


class SymfoniskNegotiatedProfileSensor(SymfoniskSensor):
    """Sensor for negotiated stream profile."""

    _attr_name = "Stream Profile"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> str | None:
        """Return the state of the sensor."""
        if not self.coordinator.data.sessions:
            return None

        return self.coordinator.data.sessions[0].get("effective_stream_profile")

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return f"{self.coordinator.host}_negotiated_profile"


class SymfoniskFailureReasonSensor(SymfoniskSensor):
    """Sensor for playback failure reason/action."""

    _attr_name = "Failure Reason"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    @property
    def native_value(self) -> str | None:
        """Return the state of the sensor."""
        if not self.coordinator.data.sessions:
            return None

        session = self.coordinator.data.sessions[0]
        last_error = session.get("last_error")
        if last_error:
            return last_error.get("action") or last_error.get("message")
        return None

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return f"{self.coordinator.host}_failure_reason"


class SymfoniskDeliveryProfileSensor(SymfoniskSensor):
    """Sensor for active delivery profile."""

    _attr_name = "Delivery Profile"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> str | None:
        """Return the state of the sensor."""
        if not self.coordinator.data.sessions:
            return None

        session = self.coordinator.data.sessions[0]
        media_status = session.get("media_status") or {}
        return media_status.get("effective_delivery_profile")

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return f"{self.coordinator.host}_delivery_profile"
