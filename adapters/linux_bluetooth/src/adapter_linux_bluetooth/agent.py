"""BlueZ pairing agent via DBus."""

import logging
from collections.abc import Callable
from typing import Any

from dbus_fast.aio import MessageBus
from dbus_fast.annotations import DBusObjectPath, DBusStr, DBusUInt32, DBusUInt64
from dbus_fast.service import ServiceInterface, dbus_method

logger = logging.getLogger(__name__)

BLUEZ_SERVICE = "org.bluez"
AGENT_INTERFACE = "org.bluez.Agent1"
AGENT_MANAGER_INTERFACE = "org.bluez.AgentManager1"


class PairingAgent(ServiceInterface):
    """Headless pairing agent for BlueZ."""

    def __init__(self, agent_path: str) -> None:
        super().__init__(AGENT_INTERFACE)
        self.agent_path = agent_path
        self.candidate_mac: str | None = None
        self.on_pairing_completed: Callable[[str, bool], Any] | None = None

    @dbus_method()
    def Release(self) -> None:  # noqa: N802
        logger.info("Pairing agent released")

    @dbus_method()
    def RequestPinCode(self, device: DBusObjectPath) -> DBusStr:  # noqa: N802
        logger.info(f"RequestPinCode for {device}")
        return "0000"

    @dbus_method()
    def DisplayPinCode(self, device: DBusObjectPath, pincode: DBusStr) -> None:  # noqa: N802
        logger.info(f"DisplayPinCode for {device}: {pincode}")

    @dbus_method()
    def RequestPasskey(self, device: DBusObjectPath) -> DBusUInt32:  # noqa: N802
        logger.info(f"RequestPasskey for {device}")
        return 0

    @dbus_method()
    def DisplayPasskey(self, device: DBusObjectPath, passkey: DBusUInt32, entered: DBusUInt64) -> None:  # noqa: N802
        logger.info(f"DisplayPasskey for {device}: {passkey}")

    @dbus_method()
    def RequestConfirmation(self, device: DBusObjectPath, passkey: DBusUInt32) -> None:  # noqa: N802
        logger.info(f"RequestConfirmation for {device}, passkey {passkey}")
        if self._should_accept(device):
            self._notify_completed(device, True)
        else:
            self._notify_completed(device, False)
            raise Exception("Pairing rejected: device not candidate")

    @dbus_method()
    def RequestAuthorization(self, device: DBusObjectPath) -> None:  # noqa: N802
        logger.info(f"RequestAuthorization for {device}")
        if self._should_accept(device):
            self._notify_completed(device, True)
        else:
            self._notify_completed(device, False)
            raise Exception("Authorization rejected")

    @dbus_method()
    def AuthorizeService(self, device: DBusObjectPath, uuid: DBusStr) -> None:  # noqa: N802
        logger.info(f"AuthorizeService for {device}, UUID {uuid}")
        if self._should_accept(device):
            return
        raise Exception("Service authorization rejected")

    @dbus_method()
    def Cancel(self) -> None:  # noqa: N802
        logger.info("Pairing cancelled")

    def _should_accept(self, device_path: str) -> bool:
        """Decide whether to accept the pairing request."""
        if self.candidate_mac is None:
            return True

        mac_from_path = self._mac_from_path(device_path)
        return mac_from_path.upper() == self.candidate_mac.upper()

    def _mac_from_path(self, device_path: str) -> str:
        return device_path.split("/")[-1].replace("dev_", "").replace("_", ":")

    def _notify_completed(self, device_path: str, success: bool) -> None:
        if self.on_pairing_completed:
            mac = self._mac_from_path(device_path)
            self.on_pairing_completed(mac, success)


async def register_agent(bus: MessageBus, agent: PairingAgent, capability: str = "NoInputNoOutput") -> bool:
    """Register the pairing agent with BlueZ."""
    try:
        bus.export(agent.agent_path, agent)

        introspection = await bus.introspect(BLUEZ_SERVICE, "/org/bluez")
        proxy_object = bus.get_proxy_object(BLUEZ_SERVICE, "/org/bluez", introspection)
        agent_manager = proxy_object.get_interface(AGENT_MANAGER_INTERFACE)

        await agent_manager.call_register_agent(agent.agent_path, capability)  # type: ignore[attr-defined]
        await agent_manager.call_request_default_agent(agent.agent_path)  # type: ignore[attr-defined]

        logger.info(f"Pairing agent registered at {agent.agent_path}")
        return True
    except Exception as e:
        logger.error(f"Failed to register pairing agent: {e}")
        return False


async def unregister_agent(bus: MessageBus, agent_path: str) -> bool:
    """Unregister the pairing agent from BlueZ."""
    try:
        introspection = await bus.introspect(BLUEZ_SERVICE, "/org/bluez")
        proxy_object = bus.get_proxy_object(BLUEZ_SERVICE, "/org/bluez", introspection)
        agent_manager = proxy_object.get_interface(AGENT_MANAGER_INTERFACE)

        await agent_manager.call_unregister_agent(agent_path)  # type: ignore[attr-defined]
        bus.unexport(agent_path)

        logger.info(f"Pairing agent unregistered from {agent_path}")
        return True
    except Exception as e:
        logger.error(f"Failed to unregister pairing agent: {e}")
        return False
