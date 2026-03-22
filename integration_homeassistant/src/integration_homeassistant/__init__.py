"""The IKEA SYMFONISK Gateway integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant, ServiceCall

from .const import DOMAIN
from .coordinator import SymfoniskCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.SELECT,
    Platform.BUTTON,
    Platform.MEDIA_PLAYER,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up IKEA SYMFONISK Gateway from a config entry."""

    coordinator = SymfoniskCoordinator(
        hass,
        host=entry.data[CONF_HOST],
        port=entry.data[CONF_PORT],
    )

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def handle_recover_session(call: ServiceCall) -> None:
        """Handle the recover_session service call."""
        target_coordinator = coordinator
        if entry_id := call.data.get("entry_id"):
            target_coordinator = hass.data[DOMAIN].get(entry_id)

        if not target_coordinator:
            _LOGGER.error("Coordinator not found for entry_id: %s", entry_id)
            return

        session_id = call.data.get("session_id")
        if not session_id and target_coordinator.data.sessions:
            session_id = target_coordinator.data.sessions[0]["session_id"]

        if session_id:
            await target_coordinator.async_recover_session(session_id)
        else:
            _LOGGER.warning("No session_id provided and no active session found to recover")

    async def handle_refresh_sources(call: ServiceCall) -> None:
        """Handle the refresh_sources service call."""
        target_coordinator = coordinator
        if entry_id := call.data.get("entry_id"):
            target_coordinator = hass.data[DOMAIN].get(entry_id)

        if target_coordinator:
            await target_coordinator.async_refresh_sources()

    async def handle_refresh_targets(call: ServiceCall) -> None:
        """Handle the refresh_targets service call."""
        target_coordinator = coordinator
        if entry_id := call.data.get("entry_id"):
            target_coordinator = hass.data[DOMAIN].get(entry_id)

        if target_coordinator:
            await target_coordinator.async_refresh_targets()

    hass.services.async_register(DOMAIN, "recover_session", handle_recover_session)
    hass.services.async_register(DOMAIN, "refresh_sources", handle_refresh_sources)
    hass.services.async_register(DOMAIN, "refresh_targets", handle_refresh_targets)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    if not hass.data[DOMAIN]:
        hass.services.async_remove(DOMAIN, "recover_session")
        hass.services.async_remove(DOMAIN, "refresh_sources")
        hass.services.async_remove(DOMAIN, "refresh_targets")

    return unload_ok
