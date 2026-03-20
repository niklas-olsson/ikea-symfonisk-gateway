import asyncio
import logging
from typing import Dict, Any

import soco

logger = logging.getLogger(__name__)

class SonosDeviceMetadata:
    def __init__(self, uid: str, name: str, ip: str, model: str, soco_device: soco.SoCo):
        self.uid = uid
        self.name = name
        self.ip = ip
        self.model = model
        self.device = soco_device

class SonosDiscovery:
    """Discovers Sonos devices on the local network."""

    def __init__(self) -> None:
        self._devices: Dict[str, SonosDeviceMetadata] = {}
        self._discovered = False
        self._bg_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    @property
    def devices(self) -> Dict[str, SonosDeviceMetadata]:
        """Return currently discovered devices."""
        return self._devices

    @property
    def discovered(self) -> bool:
        """Return whether discovery has completed at least once."""
        return self._discovered

    def start_background_discovery(self, interval: int = 30) -> None:
        """Start a background task to periodically discover devices."""
        if self._bg_task is None or self._bg_task.done():
            self._stop_event.clear()
            self._bg_task = asyncio.create_task(self._discovery_loop(interval))

    def stop_background_discovery(self) -> None:
        """Stop the background discovery task."""
        self._stop_event.set()
        if self._bg_task:
            self._bg_task.cancel()

    async def _discovery_loop(self, interval: int) -> None:
        """Periodic discovery loop."""
        while not self._stop_event.is_set():
            await self.discover()
            try:
                # wait for the interval or until stopped
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass
            except asyncio.CancelledError:
                break

    async def discover(self, timeout: int = 5) -> Dict[str, SonosDeviceMetadata]:
        """
        Discover Sonos devices.

        Args:
            timeout: Maximum time in seconds to wait for discovery.

        Returns:
            Dictionary mapping device UID to SonosDeviceMetadata.
        """
        loop = asyncio.get_running_loop()

        try:
            # soco.discover is a blocking call, so run it in a thread pool.
            # include_invisible=True ensures we see hidden members of stereo pairs or surrounds.
            discovered_set = await loop.run_in_executor(
                None,
                lambda: soco.discover(timeout=timeout, include_invisible=True)
            )
        except Exception as e:
            logger.error("Failed to discover Sonos devices: %s", e)
            discovered_set = None

        if not discovered_set:
            logger.debug("No Sonos devices found during discovery.")
            self._discovered = True
            return self._devices

        new_devices = {}
        for device in discovered_set:
            try:
                # uid format is typically 'RINCON_macaddress01400'
                uid = device.uid
                name = device.player_name
                ip = device.ip_address

                # Fetch device info to get the model
                # This could be slow if done synchronously for many devices, but we are inside
                # a discovery loop that might tolerate it.
                # Better to fetch it in executor or cache it
                # device.get_speaker_info() does an HTTP request, so wrap in executor
                info = await loop.run_in_executor(None, device.get_speaker_info)
                model = info.get('model_name', 'Unknown')

                new_devices[uid] = SonosDeviceMetadata(
                    uid=uid, name=name, ip=ip, model=model, soco_device=device
                )
                logger.debug(
                    "Found device: %s (%s) [Model: %s] at %s",
                    name, uid, model, ip
                )
            except Exception as e:
                logger.warning("Failed to extract info from a discovered device: %s", e)

        # Update the known devices
        self._devices = new_devices
        self._discovered = True
        return self._devices
