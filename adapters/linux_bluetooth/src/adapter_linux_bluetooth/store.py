"""Trusted device store for Bluetooth adapters."""

import json
import logging
from pathlib import Path
from typing import Any, cast

logger = logging.getLogger(__name__)


def get_default_storage_path() -> Path:
    """Return default storage path for trusted devices."""
    # Use the persistent config directory mounted as a volume
    data_dir = Path("/app/config")
    if not data_dir.is_dir():
        # Fallback for development if /app/config isn't mapped
        data_dir = Path.home() / ".config" / "ikea-symfonisk-bridge"

    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "bluetooth_trusted_devices.json"


class TrustedDeviceStore:
    """Persists trusted Bluetooth devices and preferences."""

    def __init__(self, storage_path: str | Path | None = None):
        if storage_path is None:
            self.storage_path = get_default_storage_path()
        else:
            self.storage_path = Path(storage_path)

        self._data: dict[str, Any] = {
            "trusted_devices": {},
            "blocked_devices": [],
            "preferred_device": None,
        }
        self._load()

    def _load(self) -> None:
        """Load data from storage."""
        if not self.storage_path.exists():
            return

        try:
            with open(self.storage_path) as f:
                self._data.update(json.load(f))
        except Exception as e:
            logger.error(f"Failed to load trusted devices from {self.storage_path}: {e}")

    def _save(self) -> None:
        """Save data to storage."""
        try:
            with open(self.storage_path, "w") as f:
                json.dump(self._data, f, indent=4)
        except Exception as e:
            logger.error(f"Failed to save trusted devices to {self.storage_path}: {e}")

    def is_trusted(self, mac: str) -> bool:
        """Check if a device is trusted."""
        return mac in self._data["trusted_devices"]

    def is_blocked(self, mac: str) -> bool:
        """Check if a device is blocked."""
        return mac in self._data["blocked_devices"]

    def trust_device(self, mac: str, metadata: dict[str, Any]) -> None:
        """Add a device to the trusted list."""
        if mac in self._data["blocked_devices"]:
            self._data["blocked_devices"].remove(mac)

        self._data["trusted_devices"][mac] = metadata
        self._save()

    def untrust_device(self, mac: str) -> None:
        """Remove a device from the trusted list."""
        if mac in self._data["trusted_devices"]:
            del self._data["trusted_devices"][mac]
            if self._data["preferred_device"] == mac:
                self._data["preferred_device"] = None
            self._save()

    def block_device(self, mac: str) -> None:
        """Block a device from connecting."""
        self.untrust_device(mac)
        if mac not in self._data["blocked_devices"]:
            self._data["blocked_devices"].append(mac)
            self._save()

    def unblock_device(self, mac: str) -> None:
        """Unblock a device."""
        if mac in self._data["blocked_devices"]:
            self._data["blocked_devices"].remove(mac)
            self._save()

    def forget_device(self, mac: str) -> None:
        """Forget a device (untrust and block to prevent auto-reconnect if desired)."""
        self.untrust_device(mac)

    def get_preferred_device(self) -> str | None:
        """Get the MAC of the preferred device."""
        return cast(str | None, self._data["preferred_device"])

    def set_preferred_device(self, mac: str | None) -> None:
        """Set the preferred device MAC."""
        if mac and mac not in self._data["trusted_devices"]:
            logger.warning(f"Setting preferred device to untrusted MAC: {mac}")

        self._data["preferred_device"] = mac
        self._save()

    def list_trusted(self) -> dict[str, Any]:
        """List all trusted devices."""
        return cast(dict[str, Any], self._data["trusted_devices"])

    def get_device_metadata(self, mac: str) -> dict[str, Any] | None:
        """Get metadata for a trusted device."""
        return cast(dict[str, Any] | None, self._data["trusted_devices"].get(mac))
