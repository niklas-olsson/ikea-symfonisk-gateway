"""The IKEA SYMFONISK Gateway integration."""

from __future__ import annotations

import logging
import os

from homeassistant.components import frontend
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

    # Register static path for panel assets
    panel_path = os.path.join(os.path.dirname(__file__), "www")
    if os.path.exists(panel_path):
        hass.http.register_static_path(
            "/ikea_symfonisk_gateway_static",
            panel_path,
        )

        # Register the Bridge panel in the sidebar
        frontend.async_register_panel(
            hass,
            frontend_url_path="symfonisk_gateway",
            webcomponent_name="symfonisk-gateway-panel",
            sidebar_title="Bridge",
            sidebar_icon="mdi:router-wireless",
            module_url="/ikea_symfonisk_gateway_static/panel.js",
            config={"host": entry.data[CONF_HOST], "port": entry.data[CONF_PORT]},
        )

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

    async def handle_refresh_discovery(call: ServiceCall) -> None:
        """Handle the refresh_discovery service call."""
        target_coordinator = coordinator
        if entry_id := call.data.get("entry_id"):
            target_coordinator = hass.data[DOMAIN].get(entry_id)

        if target_coordinator:
            await target_coordinator.async_refresh_discovery()

    async def handle_start_playback(call: ServiceCall) -> None:
        """Handle the start_playback service call."""
        target_coordinator = coordinator
        if entry_id := call.data.get("entry_id"):
            target_coordinator = hass.data[DOMAIN].get(entry_id)

        if not target_coordinator:
            _LOGGER.error("Coordinator not found for entry_id: %s", entry_id)
            return

        source_id = call.data.get("source_id") or target_coordinator.selected_source_id
        target_id = call.data.get("target_id") or target_coordinator.selected_target_id

        if not source_id or not target_id:
            _LOGGER.error("Source or Target not selected and no defaults available")
            return

        await target_coordinator.start_session(source_id, target_id)

    async def handle_stop_playback(call: ServiceCall) -> None:
        """Handle the stop_playback service call."""
        target_coordinator = coordinator
        if entry_id := call.data.get("entry_id"):
            target_coordinator = hass.data[DOMAIN].get(entry_id)

        if target_coordinator:
            await target_coordinator.async_stop_playback()

    hass.services.async_register(DOMAIN, "recover_session", handle_recover_session)
    hass.services.async_register(DOMAIN, "refresh_sources", handle_refresh_sources)
    hass.services.async_register(DOMAIN, "refresh_targets", handle_refresh_targets)
    hass.services.async_register(DOMAIN, "refresh_discovery", handle_refresh_discovery)
    hass.services.async_register(DOMAIN, "start_playback", handle_start_playback)
    hass.services.async_register(DOMAIN, "stop_playback", handle_stop_playback)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    # Clean up panel
    frontend.async_remove_panel(hass, "symfonisk_gateway")

    if not hass.data[DOMAIN]:
        hass.services.async_remove(DOMAIN, "recover_session")
        hass.services.async_remove(DOMAIN, "refresh_sources")
        hass.services.async_remove(DOMAIN, "refresh_targets")
        hass.services.async_remove(DOMAIN, "refresh_discovery")
        hass.services.async_remove(DOMAIN, "start_playback")
        hass.services.async_remove(DOMAIN, "stop_playback")

    return unload_ok
