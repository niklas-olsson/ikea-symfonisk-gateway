"""BlueZ adapter controller via DBus."""

import logging
import os
import shutil
from typing import Any

from dbus_fast import Variant
from dbus_fast.aio import MessageBus

logger = logging.getLogger(__name__)

BLUEZ_SERVICE = "org.bluez"
ADAPTER_INTERFACE = "org.bluez.Adapter1"
PROPERTIES_INTERFACE = "org.freedesktop.DBus.Properties"


class BlueZAdapterController:
    """Controls a BlueZ Bluetooth adapter via DBus."""

    def __init__(self, adapter_name: str = "hci0"):
        self.adapter_name = adapter_name
        self.adapter_path = f"/org/bluez/{adapter_name}"
        self._bus: MessageBus | None = None

    async def _get_bus(self) -> MessageBus:
        """Get or create the system message bus."""
        if self._bus is None or not self._bus.connected:
            self._bus = await MessageBus(bus_type=0).connect()  # System bus
        return self._bus

    async def get_properties(self) -> dict[str, Any]:
        """Get all properties of the Bluetooth adapter."""
        try:
            bus = await self._get_bus()
            introspection = await bus.introspect(BLUEZ_SERVICE, self.adapter_path)
            proxy_object = bus.get_proxy_object(BLUEZ_SERVICE, self.adapter_path, introspection)
            properties_iface = proxy_object.get_interface(PROPERTIES_INTERFACE)

            all_props = await properties_iface.call_get_all(ADAPTER_INTERFACE)
            # Unwrap Variants
            return {k: v.value for k, v in all_props.items()}
        except Exception as e:
            logger.error(f"Failed to get adapter properties: {e}")
            return {}

    async def set_property(self, name: str, value: Any, signature: str) -> bool:
        """Set a property of the Bluetooth adapter."""
        try:
            bus = await self._get_bus()
            introspection = await bus.introspect(BLUEZ_SERVICE, self.adapter_path)
            proxy_object = bus.get_proxy_object(BLUEZ_SERVICE, self.adapter_path, introspection)
            properties_iface = proxy_object.get_interface(PROPERTIES_INTERFACE)

            await properties_iface.call_set(ADAPTER_INTERFACE, name, Variant(signature, value))
            return True
        except Exception as e:
            logger.error(f"Failed to set adapter property {name}: {e}")
            return False

    async def set_powered(self, powered: bool) -> bool:
        return await self.set_property("Powered", powered, "b")

    async def set_alias(self, alias: str) -> bool:
        return await self.set_property("Alias", alias, "s")

    async def set_discoverable(self, discoverable: bool) -> bool:
        return await self.set_property("Discoverable", discoverable, "b")

    async def set_pairable(self, pairable: bool) -> bool:
        return await self.set_property("Pairable", pairable, "b")

    async def set_discoverable_timeout(self, timeout: int) -> bool:
        return await self.set_property("DiscoverableTimeout", timeout, "u")

    async def set_pairable_timeout(self, timeout: int) -> bool:
        return await self.set_property("PairableTimeout", timeout, "u")

    async def is_available(self) -> bool:
        """Check if the adapter is available on DBus."""
        try:
            bus = await self._get_bus()
            introspection = await bus.introspect(BLUEZ_SERVICE, self.adapter_path)
            return introspection is not None
        except Exception:
            return False

    async def check_readiness(self) -> list[str]:
        """Check for common adapter issues (rfkill, permissions, missing service)."""
        errors = []

        # 1. Check if DBus socket is accessible
        if not os.path.exists("/run/dbus/system_bus_socket"):
            errors.append("system_dbus_socket_missing")
        elif not os.access("/run/dbus/system_bus_socket", os.W_OK):
            errors.append("system_dbus_permission_denied")

        # 2. Check if BlueZ service is running on DBus
        try:
            bus = await self._get_bus()
            introspection = await bus.introspect(BLUEZ_SERVICE, "/org/bluez")
            if introspection is None:
                errors.append("bluez_service_not_found")
        except Exception:
            errors.append("bluez_service_unreachable")

        # 3. Check if adapter exists
        if not await self.is_available():
            errors.append("adapter_not_found")

        # 4. Check rfkill if possible
        if shutil.which("rfkill"):
            try:
                import subprocess
                result = subprocess.run(["rfkill", "list", "bluetooth"], capture_output=True, text=True)
                if "Soft blocked: yes" in result.stdout:
                    errors.append("adapter_soft_blocked")
                if "Hard blocked: yes" in result.stdout:
                    errors.append("adapter_hard_blocked")
            except Exception:
                pass

        return errors
