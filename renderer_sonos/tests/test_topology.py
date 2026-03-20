import pytest
from unittest.mock import MagicMock
from renderer_sonos.discovery import SonosDiscovery, SonosDeviceMetadata
from renderer_sonos.topology import SonosTopology

@pytest.mark.asyncio
async def test_topology_empty() -> None:
    discovery = MagicMock(spec=SonosDiscovery)
    discovery.devices = {}
    discovery.discovered = True

    topology = SonosTopology(discovery)
    result = await topology.build()

    assert result == {
        "renderer": "sonos",
        "targets": [],
        "discovered": True
    }

@pytest.mark.asyncio
async def test_topology_single_speaker() -> None:
    discovery = MagicMock(spec=SonosDiscovery)
    discovery.discovered = True

    device = MagicMock()
    device.uid = "RINCON_1"
    device.player_name = "Living Room"
    device.ip_address = "1.2.3.4"

    group = MagicMock()
    group.coordinator = device
    group.members = [device]
    device.group = group

    meta = SonosDeviceMetadata(uid="RINCON_1", name="Living Room", ip="1.2.3.4", model="SYMFONISK Bookshelf", soco_device=device)
    discovery.devices = {"RINCON_1": meta}

    topology = SonosTopology(discovery)
    result = await topology.build()

    assert result["renderer"] == "sonos"
    assert result["discovered"] is True
    assert len(result["targets"]) == 1

    target = result["targets"][0]
    assert target["target_id"] == "RINCON_1"
    assert target["target_type"] == "speaker"
    assert target["display_name"] == "Living Room"
    assert target["members"] == ["RINCON_1"]
    assert target["coordinator_id"] == "RINCON_1"

@pytest.mark.asyncio
async def test_topology_stereo_pair() -> None:
    discovery = MagicMock(spec=SonosDiscovery)
    discovery.discovered = True

    coord = MagicMock()
    coord.uid = "RINCON_COORD"
    coord.player_name = "Stereo Pair Room"

    member = MagicMock()
    member.uid = "RINCON_MEMBER"
    member.player_name = "Stereo Pair Room"

    group = MagicMock()
    group.coordinator = coord
    group.members = [coord, member]

    coord.group = group
    member.group = group

    meta1 = SonosDeviceMetadata(uid="RINCON_COORD", name="Stereo Pair Room", ip="1.2.3.4", model="SYMFONISK Bookshelf", soco_device=coord)
    meta2 = SonosDeviceMetadata(uid="RINCON_MEMBER", name="Stereo Pair Room", ip="1.2.3.5", model="SYMFONISK Bookshelf", soco_device=member)

    discovery.devices = {"RINCON_COORD": meta1, "RINCON_MEMBER": meta2}

    topology = SonosTopology(discovery)
    result = await topology.build()

    assert len(result["targets"]) == 1

    target = result["targets"][0]
    assert target["target_id"] == "RINCON_COORD"
    assert target["target_type"] == "stereo_pair"
    assert target["display_name"] == "Stereo Pair Room"
    assert sorted(target["members"]) == sorted(["RINCON_COORD", "RINCON_MEMBER"])
    assert target["coordinator_id"] == "RINCON_COORD"

@pytest.mark.asyncio
async def test_topology_group() -> None:
    discovery = MagicMock(spec=SonosDiscovery)
    discovery.discovered = True

    coord = MagicMock()
    coord.uid = "RINCON_COORD"
    coord.player_name = "Living Room"

    member = MagicMock()
    member.uid = "RINCON_MEMBER"
    member.player_name = "Kitchen"

    group = MagicMock()
    group.coordinator = coord
    group.members = [coord, member]

    coord.group = group
    member.group = group

    meta1 = SonosDeviceMetadata(uid="RINCON_COORD", name="Living Room", ip="1.2.3.4", model="SYMFONISK Bookshelf", soco_device=coord)
    meta2 = SonosDeviceMetadata(uid="RINCON_MEMBER", name="Kitchen", ip="1.2.3.5", model="SYMFONISK Table Lamp", soco_device=member)

    discovery.devices = {"RINCON_COORD": meta1, "RINCON_MEMBER": meta2}

    topology = SonosTopology(discovery)
    result = await topology.build()

    assert len(result["targets"]) == 1

    target = result["targets"][0]
    assert target["target_id"] == "RINCON_COORD"
    assert target["target_type"] == "group"
    assert target["display_name"] == "Living Room"
    assert sorted(target["members"]) == sorted(["RINCON_COORD", "RINCON_MEMBER"])
