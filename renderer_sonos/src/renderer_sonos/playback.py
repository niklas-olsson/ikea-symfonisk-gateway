import logging
from typing import Any

import soco  # type: ignore

logger = logging.getLogger(__name__)


class PlaybackController:
    """Handles Sonos playback operations."""

    def __init__(self) -> None:
        # We assume the caller provides device objects or IPs,
        # but to keep it simple, we'll map target_id to IP/Device.
        # For now, let's assume target_id is the IP or exact UID that can be discovered,
        # or we just take the SoCo object. Let's make it easy to inject SoCo objects.
        self._devices: dict[str, soco.SoCo] = {}

    def add_device(self, target_id: str, device: soco.SoCo) -> None:
        """Register a SoCo device."""
        self._devices[target_id] = device

    def get_device(self, target_id: str) -> soco.SoCo | None:
        """Get a registered SoCo device."""
        return self._devices.get(target_id)

    def _get_coordinator(self, device: soco.SoCo) -> soco.SoCo:
        """Get the coordinator for a given device."""
        try:
            if hasattr(device, "group") and device.group is not None:
                return device.group.coordinator
        except Exception as e:
            logger.warning(f"Could not determine group coordinator for {device.ip_address}: {e}")
        return device

    def play_stream(self, target_id: str, stream_url: str, metadata: dict[str, Any] | None = None) -> None:
        """Play a stream on a device (or its coordinator)."""
        device = self.get_device(target_id)
        if not device:
            raise ValueError(f"Unknown target ID: {target_id}")

        coordinator = self._get_coordinator(device)
        logger.info(f"Playing stream {stream_url} on coordinator {coordinator.ip_address} for target {target_id}")

        # metadata could contain title, etc., but play_uri primarily takes URI
        # metadata string could be passed as meta parameter to play_uri
        meta = metadata.get("metadata", "") if metadata else ""
        coordinator.play_uri(stream_url, meta=meta)

    def stop(self, target_id: str) -> None:
        """Stop playback on a device (or its coordinator)."""
        device = self.get_device(target_id)
        if not device:
            raise ValueError(f"Unknown target ID: {target_id}")

        coordinator = self._get_coordinator(device)
        logger.info(f"Stopping playback on coordinator {coordinator.ip_address} for target {target_id}")
        coordinator.stop()

    def set_volume(self, target_id: str, volume: float) -> None:
        """Set volume on a specific device (0.0 to 1.0).
        Note: Volume changes usually target the specific device, not necessarily the coordinator,
        though soco handles group volume if requested. Here we set it on the specific device.
        """
        device = self.get_device(target_id)
        if not device:
            raise ValueError(f"Unknown target ID: {target_id}")

        if not (0.0 <= volume <= 1.0):
            raise ValueError("Volume must be between 0.0 and 1.0")

        # Convert 0.0-1.0 to 0-100
        vol_int = int(volume * 100)
        logger.info(f"Setting volume on {device.ip_address} for target {target_id} to {vol_int}")
        device.volume = vol_int
