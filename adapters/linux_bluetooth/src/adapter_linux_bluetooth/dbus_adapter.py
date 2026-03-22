"""BlueZ adapter controller via DBus."""

import logging
import os
import shutil
from typing import Any, cast

from dbus_fast import BusType, Variant
from dbus_fast.aio import MessageBus

logger = logging.getLogger(__name__)

BLUEZ_SERVICE = "org.bluez"
ADAPTER_INTERFACE = "org.bluez.Adapter1"
DEVICE_INTERFACE = "org.bluez.Device1"
PROPERTIES_INTERFACE = "org.freedesktop.DBus.Properties"
OBJECT_MANAGER_INTERFACE = "org.freedesktop.DBus.ObjectManager"


class BlueZAdapterController:
    """Controls a BlueZ Bluetooth adapter via DBus."""

    def __init__(self, adapter_name: str = "hci0"):
        self.adapter_name = adapter_name
        self.adapter_path = f"/org/bluez/{adapter_name}"
        self._bus: MessageBus | None = None
        self._readiness_cache: list[str] = []
        self._readiness_time: float = 0

    async def _get_bus(self) -> MessageBus:
        """Get or create the system message bus."""
        if self._bus is None or not self._bus.connected:
            self._bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        return self._bus

    async def get_properties(self) -> dict[str, Any]:
        """Get all properties of the Bluetooth adapter."""
        try:
            bus = await self._get_bus()
            introspection = await bus.introspect(BLUEZ_SERVICE, self.adapter_path)
            proxy_object = bus.get_proxy_object(BLUEZ_SERVICE, self.adapter_path, introspection)
            properties_iface = proxy_object.get_interface(PROPERTIES_INTERFACE)

            all_props = await properties_iface.call_get_all(ADAPTER_INTERFACE)  # type: ignore[attr-defined]
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

            await properties_iface.call_set(ADAPTER_INTERFACE, name, Variant(signature, value))  # type: ignore[attr-defined]
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

    async def set_class(self, class_hex: str) -> bool:
        """Set the adapter device class explicitly (e.g. 0x240404 for speaker)."""
        try:
            import subprocess

            # hciconfig is available in the container and needed because
            # BlueZ's D-Bus API exposes Class as read-only.
            subprocess.run(["hciconfig", self.adapter_name, "class", class_hex], check=True)
            return True
        except Exception as e:
            logger.error(f"Failed to set adapter class to {class_hex}: {e}")
            return False

    async def is_available(self) -> bool:
        """Check if the adapter is available on DBus."""
        try:
            bus = await self._get_bus()
            introspection = await bus.introspect(BLUEZ_SERVICE, self.adapter_path)
            return introspection is not None
        except Exception:
            return False

    async def get_managed_objects(self) -> dict[str, dict[str, dict[str, Any]]]:
        """Get all managed objects from BlueZ."""
        try:
            bus = await self._get_bus()
            introspection = await bus.introspect(BLUEZ_SERVICE, "/")
            proxy_object = bus.get_proxy_object(BLUEZ_SERVICE, "/", introspection)
            obj_manager = proxy_object.get_interface(OBJECT_MANAGER_INTERFACE)

            managed_objects = await obj_manager.call_get_managed_objects()  # type: ignore[attr-defined]

            # Unwrap Variants recursively
            def unwrap(obj: Any) -> Any:
                if isinstance(obj, Variant):
                    return unwrap(obj.value)
                if isinstance(obj, dict):
                    return {k: unwrap(v) for k, v in obj.items()}
                if isinstance(obj, list):
                    return [unwrap(v) for v in obj]
                return obj

            return cast(dict[str, dict[str, dict[str, Any]]], unwrap(managed_objects))
        except Exception as e:
            logger.error(f"Failed to get managed objects: {e}")
            return {}

    async def connect_device(self, device_path: str) -> bool:
        """Connect to a Bluetooth device."""
        try:
            bus = await self._get_bus()
            introspection = await bus.introspect(BLUEZ_SERVICE, device_path)
            proxy_object = bus.get_proxy_object(BLUEZ_SERVICE, device_path, introspection)
            device_iface = proxy_object.get_interface(DEVICE_INTERFACE)
            await device_iface.call_connect()  # type: ignore[attr-defined]
            return True
        except Exception as e:
            logger.error(f"Failed to connect to device {device_path}: {e}")
            return False

    async def disconnect_device(self, device_path: str) -> bool:
        """Disconnect from a Bluetooth device."""
        try:
            bus = await self._get_bus()
            introspection = await bus.introspect(BLUEZ_SERVICE, device_path)
            proxy_object = bus.get_proxy_object(BLUEZ_SERVICE, device_path, introspection)
            device_iface = proxy_object.get_interface(DEVICE_INTERFACE)
            await device_iface.call_disconnect()  # type: ignore[attr-defined]
            return True
        except Exception as e:
            logger.error(f"Failed to disconnect from device {device_path}: {e}")
            return False

    async def remove_device(self, device_path: str) -> bool:
        """Remove (unpair) a Bluetooth device."""
        try:
            bus = await self._get_bus()
            introspection = await bus.introspect(BLUEZ_SERVICE, self.adapter_path)
            proxy_object = bus.get_proxy_object(BLUEZ_SERVICE, self.adapter_path, introspection)
            adapter_iface = proxy_object.get_interface(ADAPTER_INTERFACE)
            await adapter_iface.call_remove_device(device_path)  # type: ignore[attr-defined]
            return True
        except Exception as e:
            logger.debug(f"Failed to remove device {device_path} (maybe already gone): {e}")
            return False

    async def set_device_trusted(self, device_path: str, trusted: bool) -> bool:
        """Set the Trusted property of a BlueZ device."""
        try:
            bus = await self._get_bus()
            introspection = await bus.introspect(BLUEZ_SERVICE, device_path)
            proxy_object = bus.get_proxy_object(BLUEZ_SERVICE, device_path, introspection)
            properties_iface = proxy_object.get_interface(PROPERTIES_INTERFACE)

            await properties_iface.call_set(DEVICE_INTERFACE, "Trusted", Variant("b", trusted))  # type: ignore[attr-defined]
            return True
        except Exception as e:
            logger.debug(f"Failed to set Trusted property for {device_path}: {e}")
            return False

    def get_device_path(self, mac: str) -> str:
        """Resolve a MAC address to a BlueZ device path."""
        formatted_mac = mac.replace(":", "_").upper()
        return f"{self.adapter_path}/dev_{formatted_mac}"

    async def check_readiness(self) -> list[str]:
        """Check for common adapter issues (rfkill, permissions, missing service)."""
        # Cache results for 30 seconds to avoid frequent subprocess calls
        import time
        now = time.time()
        if now - self._readiness_time < 30:
            return self._readiness_cache

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

        self._readiness_cache = errors
        self._readiness_time = now
        return errors
