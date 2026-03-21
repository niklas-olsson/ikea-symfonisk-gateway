"""Pairing window manager for Bluetooth."""

import asyncio
import logging

from bridge_core.core.event_bus import EventBus, EventType
from dbus_fast import BusType
from dbus_fast.aio import MessageBus

from .agent import PairingAgent, register_agent, unregister_agent
from .dbus_adapter import BlueZAdapterController
from .store import TrustedDeviceStore

logger = logging.getLogger(__name__)


class PairingWindowManager:
    """Manages the lifecycle of a Bluetooth pairing window."""

    def __init__(
        self,
        adapter_controller: BlueZAdapterController,
        store: TrustedDeviceStore,
        event_bus: EventBus | None = None,
        agent_path: str = "/app/bluetooth/agent",
    ):
        self.adapter_controller = adapter_controller
        self.store = store
        self.event_bus = event_bus
        self.agent_path = agent_path
        self._pairing_task: asyncio.Task[None] | None = None
        self._agent: PairingAgent | None = None
        self._is_open = False
        self._completion_event = asyncio.Event()

    @property
    def is_open(self) -> bool:
        return self._is_open

    async def open_window(
        self,
        timeout_seconds: int = 90,
        candidate_mac: str | None = None,
    ) -> bool:
        """Open the pairing window."""
        if self._is_open:
            logger.info("Pairing window already open")
            return True

        self._is_open = True
        self._completion_event.clear()
        self._pairing_task = asyncio.create_task(self._run_window(timeout_seconds, candidate_mac))

        if self.event_bus:
            self.event_bus.emit(
                EventType.BLUETOOTH_PAIRING_WINDOW_OPENED,
                payload={
                    "timeout": timeout_seconds,
                    "candidate_mac": candidate_mac,
                },
            )

        return True

    async def close_window(self) -> bool:
        """Close the pairing window."""
        if not self._is_open:
            return True

        if self._pairing_task:
            self._pairing_task.cancel()
            try:
                await self._pairing_task
            except asyncio.CancelledError:
                pass
            self._pairing_task = None

        # self._is_open is set to False in _run_window finally block or manually
        self._is_open = False

        # Note: Event emission moved to _run_window finally to avoid double emission
        # and ensure it's emitted even on timeout/cancellation.

        return True

    async def _run_window(self, timeout: int, candidate_mac: str | None) -> None:
        """Async loop for the pairing window."""
        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        self._agent = PairingAgent(self.agent_path)
        self._agent.candidate_mac = candidate_mac
        self._agent.on_pairing_completed = self._handle_pairing_completed

        try:
            # 1. Register agent
            if not await register_agent(bus, self._agent):
                logger.error("Failed to register pairing agent")
                return

            # 2. Set adapter properties
            await self.adapter_controller.set_powered(True)
            await self.adapter_controller.set_pairable(True)
            await self.adapter_controller.set_discoverable(True)
            await self.adapter_controller.set_discoverable_timeout(timeout)
            await self.adapter_controller.set_pairable_timeout(timeout)

            # Enforce Speaker class (0x240404) immediately after entering
            # discoverable mode, as bluetoothd will likely override it back
            # to a PC chassis based on system defaults.
            if hasattr(self.adapter_controller, "set_class"):
                await self.adapter_controller.set_class("0x240404")

            # 3. Wait for timeout or completion
            try:
                await asyncio.wait_for(self._completion_event.wait(), timeout=timeout)
                logger.info("Pairing window completed successfully")
            except TimeoutError:
                logger.info("Pairing window timed out")

        except asyncio.CancelledError:
            logger.info("Pairing window cancelled")
        except Exception as e:
            logger.error(f"Error in pairing window: {e}")
        finally:
            # 4. Cleanup
            await self.adapter_controller.set_discoverable(False)
            await self.adapter_controller.set_pairable(False)
            await unregister_agent(bus, self.agent_path)
            self._is_open = False
            self._agent = None
            bus.disconnect()
            if self.event_bus:
                self.event_bus.emit(EventType.BLUETOOTH_PAIRING_WINDOW_CLOSED)

    def _handle_pairing_completed(self, mac: str, success: bool) -> None:
        """Called when the agent finishes a pairing attempt."""
        if success:
            logger.info(f"Device paired successfully: {mac}")
            # Auto-trust the device
            self.store.trust_device(mac, {"paired_at": asyncio.get_event_loop().time()})
            if self.event_bus:
                self.event_bus.emit(
                    EventType.BLUETOOTH_DEVICE_PAIRED,
                    payload={"mac": mac},
                )
            # Trigger auto-close
            self._completion_event.set()
        else:
            logger.warning(f"Pairing rejected for device: {mac}")
            if self.event_bus:
                self.event_bus.emit(
                    EventType.BLUETOOTH_DEVICE_PAIRING_REJECTED,
                    payload={"mac": mac, "reason": "rejected_by_agent"},
                )
