import asyncio
import logging

import soco  # type: ignore

logger = logging.getLogger(__name__)


class HealController:
    """Handles Sonos group topology healing and recovery."""

    def __init__(self, max_retries: int = 5, base_backoff: float = 1.0):
        self.max_retries = max_retries
        self.base_backoff = base_backoff
        # Track expected topology: target_id -> coordinator target_id
        # This represents what we "want" the topology to be.
        # If expected_topology[uid] == uid, it's a standalone or coordinator.
        # If expected_topology[uid] == other_uid, it should be in a group.
        self._expected_topology: dict[str, str] = {}

    def set_expected_topology(self, topology: dict[str, str]) -> None:
        """Update the desired topology."""
        self._expected_topology = topology

    def get_expected_coordinator(self, target_id: str) -> str | None:
        """Get the expected coordinator for a target."""
        return self._expected_topology.get(target_id)

    async def heal_group_membership(self, target_id: str, device: soco.SoCo, coordinator_device: soco.SoCo | None = None) -> bool:
        """
        Attempt to heal group membership for a specific device.
        Returns True if successful or already correct, False otherwise.
        Emits heal events through the return status for now.
        """
        expected_coord_id = self.get_expected_coordinator(target_id)

        # If we don't know what it should be, we assume it's fine.
        if not expected_coord_id:
            logger.debug(f"No expected topology for {target_id}, skipping heal.")
            return True

        for attempt in range(1, self.max_retries + 1):
            try:
                # Determine current coordinator
                current_coord_device = None
                try:
                    if hasattr(device, "group") and device.group is not None:
                        current_coord_device = device.group.coordinator
                except Exception as e:
                    logger.warning(f"Error checking group for {device.ip_address}: {e}")

                # We need a way to uniquely identify the current coordinator to compare
                # with the expected coordinator. SoCo uids usually map well to target_ids.
                # Assuming the device UID matches target_id.

                # For this generic implementation, let's assume we use device.uid
                current_coord_uid = getattr(current_coord_device, "uid", None) if current_coord_device else None
                device_uid = getattr(device, "uid", None)

                # Check if we need to heal
                # If target_id == expected_coord_id, it should be its own coordinator
                if target_id == expected_coord_id:
                    if current_coord_uid != device_uid:
                        logger.info(f"Healing: {target_id} should be standalone/coordinator, but is grouped. Unjoining.")
                        device.unjoin()
                    else:
                        logger.debug(f"{target_id} topology is correct (standalone/coordinator).")
                        return True
                else:
                    # It should be joined to expected_coord_id
                    # We need the expected coordinator device to join it
                    if not coordinator_device:
                        logger.error(f"Cannot heal {target_id}: Missing coordinator device {expected_coord_id}")
                        return False

                    coord_uid = getattr(coordinator_device, "uid", None)
                    if current_coord_uid != coord_uid:
                        logger.info(f"Healing: {target_id} should be grouped with {expected_coord_id}. Joining.")
                        device.join(coordinator_device)
                    else:
                        logger.debug(f"{target_id} topology is correct (grouped with {expected_coord_id}).")
                        return True

                # If we made it here without exception, the operation succeeded.
                # We can verify on the next loop iteration or just return True.
                logger.info(f"Heal attempt {attempt} successful for {target_id}.")
                return True

            except Exception as e:
                logger.warning(f"Heal attempt {attempt} failed for {target_id}: {e}")
                if attempt < self.max_retries:
                    backoff = self.base_backoff * (2 ** (attempt - 1))
                    logger.info(f"Retrying heal for {target_id} in {backoff} seconds...")
                    await asyncio.sleep(backoff)
                else:
                    logger.error(f"Failed to heal {target_id} after {self.max_retries} attempts.")
                    return False

        return False
