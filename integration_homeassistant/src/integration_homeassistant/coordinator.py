"""DataUpdateCoordinator for IKEA SYMFONISK Gateway."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

_LOGGER = logging.getLogger(__name__)


class SymfoniskData:
    """Class to hold bridge data."""

    def __init__(self) -> None:
        self.health: dict[str, Any] = {}
        self.sources: list[dict[str, Any]] = []
        self.targets: list[dict[str, Any]] = []
        self.sessions: list[dict[str, Any]] = []
        self.config: dict[str, Any] = {}


class SymfoniskCoordinator(DataUpdateCoordinator[SymfoniskData]):
    """Class to manage fetching data from the bridge."""

    def __init__(self, hass: HomeAssistant, host: str, port: int) -> None:
        """Initialize."""
        self.host = host
        self.port = port
        self.base_url = f"http://{host}:{port}"

        super().__init__(
            hass,
            _LOGGER,
            name=f"Symfonisk Gateway ({host})",
            update_interval=timedelta(seconds=5),
        )

    async def _async_update_data(self) -> SymfoniskData:
        """Fetch data from API endpoint."""
        session = async_get_clientsession(self.hass)
        data = SymfoniskData()

        try:
            async with asyncio.timeout(10):
                # Fetch health
                async with session.get(f"{self.base_url}/health") as resp:
                    resp.raise_for_status()
                    data.health = await resp.json()

                # Fetch sources
                async with session.get(f"{self.base_url}/v1/sources") as resp:
                    resp.raise_for_status()
                    sources_data = await resp.json()
                    data.sources = sources_data.get("sources", [])

                # Fetch targets
                async with session.get(f"{self.base_url}/v1/targets") as resp:
                    resp.raise_for_status()
                    targets_data = await resp.json()
                    data.targets = targets_data.get("targets", [])

                # Fetch sessions
                async with session.get(f"{self.base_url}/v1/sessions") as resp:
                    resp.raise_for_status()
                    sessions_data = await resp.json()
                    data.sessions = sessions_data.get("sessions", [])

                # Fetch config
                async with session.get(f"{self.base_url}/v1/config") as resp:
                    resp.raise_for_status()
                    config_data = await resp.json()
                    data.config = config_data.get("config", {})

                return data

        except (aiohttp.ClientError, TimeoutError) as err:
            raise UpdateFailed(f"Error communicating with API: {err}") from err

    @property
    def selected_source_id(self) -> str | None:
        """Return the preferred source ID from config."""
        return self.data.config.get("preferred_source_id")

    @property
    def selected_target_id(self) -> str | None:
        """Return the preferred target ID from config."""
        return self.data.config.get("preferred_target_id")

    def get_active_session(self) -> dict[str, Any] | None:
        """Return the active session for the selected target."""
        target_id = self.selected_target_id
        if not target_id:
            return None

        for session in self.data.sessions:
            if session.get("target_id") == target_id and session.get("state") != "stopped":
                return session

        return None

    async def start_session(self, source_id: str | None = None, target_id: str | None = None) -> str:
        """Start a new session or reuse/takeover an existing one."""
        session = async_get_clientsession(self.hass)
        payload = {
            "source_id": source_id or self.selected_source_id,
            "target_id": target_id or self.selected_target_id,
            "stream_profile": "auto",
            "takeover": True,
        }

        async with session.post(f"{self.base_url}/v1/sessions", json=payload) as resp:
            if not resp.ok:
                text = await resp.text()
                raise Exception(f"Failed to create session: {text}")

            data = await resp.json()
            session_id = data["session_id"]
            resume_available = data.get("resume_available", False)

        if resume_available:
            # Resume existing session
            async with session.post(
                f"{self.base_url}/v1/sessions/{session_id}/resume",
                json={"force_reclaim": True},
            ) as resp:
                if not resp.ok:
                    text = await resp.text()
                    raise Exception(f"Failed to resume session: {text}")
        else:
            # Start new session
            async with session.post(f"{self.base_url}/v1/sessions/{session_id}/start") as resp:
                if not resp.ok:
                    text = await resp.text()
                    raise Exception(f"Failed to start session: {text}")

        await self.async_request_refresh()
        return session_id

    async def async_stop_playback(self) -> None:
        """Stop all active playback sessions."""
        for session in self.data.sessions:
            if session.get("state") in ("playing", "starting", "preparing", "healing"):
                await self.stop_session(session["session_id"])

    async def stop_session(self, session_id: str) -> None:
        """Stop a session."""
        session = async_get_clientsession(self.hass)
        async with session.post(f"{self.base_url}/v1/sessions/{session_id}/stop") as resp:
            if not resp.ok:
                text = await resp.text()
                raise Exception(f"Failed to stop session: {text}")

        await self.async_request_refresh()

    async def async_set_config(self, key: str, value: Any) -> None:
        """Set a configuration value on the bridge."""
        session = async_get_clientsession(self.hass)
        payload = {"value": value}
        async with session.put(f"{self.base_url}/v1/config/{key}", json=payload) as resp:
            if not resp.ok:
                text = await resp.text()
                raise Exception(f"Failed to set config {key}: {text}")

        await self.async_request_refresh()

    async def async_recover_session(self, session_id: str) -> None:
        """Trigger session recovery on the bridge."""
        session = async_get_clientsession(self.hass)
        async with session.post(f"{self.base_url}/v1/sessions/{session_id}/recover") as resp:
            if not resp.ok:
                text = await resp.text()
                raise Exception(f"Failed to recover session: {text}")

        await self.async_request_refresh()

    async def async_refresh_sources(self) -> None:
        """Trigger source refresh on the bridge."""
        session = async_get_clientsession(self.hass)
        async with session.post(f"{self.base_url}/v1/sources/refresh") as resp:
            if not resp.ok:
                text = await resp.text()
                raise Exception(f"Failed to refresh sources: {text}")

        await self.async_request_refresh()

    async def async_refresh_targets(self) -> None:
        """Trigger target refresh on the bridge."""
        session = async_get_clientsession(self.hass)
        async with session.post(f"{self.base_url}/v1/targets/refresh") as resp:
            if not resp.ok:
                text = await resp.text()
                raise Exception(f"Failed to refresh targets: {text}")

        await self.async_request_refresh()

    async def async_refresh_discovery(self) -> None:
        """Trigger a full discovery refresh (sources and targets) on the bridge."""
        session = async_get_clientsession(self.hass)
        async with session.post(f"{self.base_url}/v1/discovery/refresh") as resp:
            if not resp.ok:
                text = await resp.text()
                raise Exception(f"Failed to refresh discovery: {text}")

        await self.async_request_refresh()
