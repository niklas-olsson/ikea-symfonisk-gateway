import logging
from typing import Any, Dict, List

from .discovery import SonosDiscovery

logger = logging.getLogger(__name__)

class SonosTopology:
    """Builds and maintains Sonos playback topology."""

    def __init__(self, discovery: SonosDiscovery) -> None:
        self._discovery = discovery

    async def build(self) -> Dict[str, Any]:
        """
        Build topology based on currently discovered devices.

        Returns:
            Dictionary matching the get_topology interface.
        """
        devices = self._discovery.devices
        discovered = self._discovery.discovered

        if not devices:
            return {
                "renderer": "sonos",
                "targets": [],
                "discovered": discovered,
            }

        targets_by_id = {}
        processed_uids = set()

        for uid, device_meta in devices.items():
            if uid in processed_uids:
                continue

            try:
                device = device_meta.device
                group = device.group
                if not group:
                    logger.warning("Device %s has no group", device.player_name)
                    continue

                coordinator = group.coordinator
                coord_uid = coordinator.uid

                # If we've already processed this group via another member, skip
                if coord_uid in targets_by_id:
                    processed_uids.add(uid)
                    continue

                # Collect members
                member_uids = []
                member_models = []

                # group.members gives the SoCo devices that belong to the same logical group
                for member in group.members:
                    m_uid = member.uid
                    member_uids.append(m_uid)
                    processed_uids.add(m_uid)

                    # fetch model from our cached discovery metadata, if missing default to "Unknown"
                    m_meta = devices.get(m_uid)
                    m_model = m_meta.model if m_meta else "Unknown"
                    member_models.append(m_model)

                # Target ID is the coordinator UID
                target_id = coord_uid

                # Determine target type: "speaker", "stereo_pair", or "group"
                # "stereo pair" condition: 2 members with the EXACT same model and they are grouped
                target_type = "speaker"
                if len(member_uids) == 2 and member_models[0] == member_models[1]:
                    target_type = "stereo_pair"
                elif len(member_uids) > 1:
                    target_type = "group"

                targets_by_id[target_id] = {
                    "target_id": target_id,
                    "target_type": target_type,
                    "display_name": coordinator.player_name,
                    "members": member_uids,
                    "coordinator_id": coord_uid,
                }

            except Exception as e:
                logger.error("Error building topology for %s: %s", uid, e)

        return {
            "renderer": "sonos",
            "targets": list(targets_by_id.values()),
            "discovered": discovered,
        }
