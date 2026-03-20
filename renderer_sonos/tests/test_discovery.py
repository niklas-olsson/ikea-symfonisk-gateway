from unittest.mock import MagicMock, patch

import pytest

from renderer_sonos import SonosRendererAdapter


@pytest.mark.asyncio
async def test_list_targets_single_speaker() -> None:
    # Mock SoCo discovery
    mock_soco1 = MagicMock()
    mock_soco1.player_name = "Living Room"
    mock_soco1.uid = "RINCON_1"

    mock_group1 = MagicMock()
    mock_group1.coordinator = mock_soco1
    mock_group1.members = [mock_soco1]

    mock_soco1.group = mock_group1

    with patch("soco.discover") as mock_discover:
        mock_discover.return_value = {mock_soco1}

        adapter = SonosRendererAdapter()
        targets = await adapter.list_targets()

        assert len(targets) == 1
        assert targets[0].display_name == "Living Room"
        assert targets[0].target_id == "RINCON_1"
        assert targets[0].coordinator_id == "RINCON_1"
        assert targets[0].members == ["RINCON_1"]


@pytest.mark.asyncio
async def test_list_targets_multiple_groups() -> None:
    # Mock SoCo discovery with two separate groups
    mock_soco1 = MagicMock()
    mock_soco1.player_name = "Living Room"
    mock_soco1.uid = "RINCON_1"
    mock_group1 = MagicMock()
    mock_group1.coordinator = mock_soco1
    mock_group1.members = [mock_soco1]
    mock_soco1.group = mock_group1

    mock_soco2 = MagicMock()
    mock_soco2.player_name = "Kitchen"
    mock_soco2.uid = "RINCON_2"
    mock_group2 = MagicMock()
    mock_group2.coordinator = mock_soco2
    mock_group2.members = [mock_soco2]
    mock_soco2.group = mock_group2

    with patch("soco.discover") as mock_discover:
        # discover() returns all discovered speakers
        mock_discover.return_value = {mock_soco1, mock_soco2}

        adapter = SonosRendererAdapter()
        targets = await adapter.list_targets()

        assert len(targets) == 2
        ids = [t.target_id for t in targets]
        assert "RINCON_1" in ids
        assert "RINCON_2" in ids


@pytest.mark.asyncio
async def test_list_targets_stereo_pair() -> None:
    # A stereo pair is one group with two members
    mock_soco_left = MagicMock()
    mock_soco_left.player_name = "Bedroom"
    mock_soco_left.uid = "RINCON_LEFT"

    mock_soco_right = MagicMock()
    mock_soco_right.player_name = "Bedroom"
    mock_soco_right.uid = "RINCON_RIGHT"

    mock_group = MagicMock()
    mock_group.coordinator = mock_soco_left
    mock_group.members = [mock_soco_left, mock_soco_right]

    mock_soco_left.group = mock_group
    mock_soco_right.group = mock_group

    with patch("soco.discover") as mock_discover:
        mock_discover.return_value = {mock_soco_left, mock_soco_right}

        adapter = SonosRendererAdapter()
        targets = await adapter.list_targets()

        # Should only return ONE target for the stereo pair
        assert len(targets) == 1
        assert targets[0].target_id == "RINCON_LEFT"  # coordinator ID
        assert set(targets[0].members) == {"RINCON_LEFT", "RINCON_RIGHT"}
        assert targets[0].display_name == "Bedroom"


@pytest.mark.asyncio
async def test_list_targets_grouped_speakers() -> None:
    # Two speakers grouped together
    mock_soco1 = MagicMock()
    mock_soco1.player_name = "Living Room"
    mock_soco1.uid = "RINCON_1"

    mock_soco2 = MagicMock()
    mock_soco2.player_name = "Kitchen"
    mock_soco2.uid = "RINCON_2"

    mock_group = MagicMock()
    mock_group.coordinator = mock_soco1
    mock_group.members = [mock_soco1, mock_soco2]

    mock_soco1.group = mock_group
    mock_soco2.group = mock_group

    with patch("soco.discover") as mock_discover:
        mock_discover.return_value = {mock_soco1, mock_soco2}

        adapter = SonosRendererAdapter()
        targets = await adapter.list_targets()

        # Groups are also logical targets.
        # Usually, if they are grouped, they act as one.
        assert len(targets) == 1
        assert targets[0].target_id == "RINCON_1"
        assert set(targets[0].members) == {"RINCON_1", "RINCON_2"}


@pytest.mark.asyncio
async def test_get_topology() -> None:
    mock_soco1 = MagicMock()
    mock_soco1.player_name = "Living Room"
    mock_soco1.uid = "RINCON_1"
    mock_group1 = MagicMock()
    mock_group1.coordinator = mock_soco1
    mock_group1.members = [mock_soco1]
    mock_soco1.group = mock_group1

    with patch("soco.discover") as mock_discover:
        mock_discover.return_value = {mock_soco1}
        adapter = SonosRendererAdapter()
        await adapter.list_targets()

        topology = await adapter.get_topology()
        assert topology["renderer"] == "sonos"
        assert topology["discovered"] is True
        assert len(topology["targets"]) == 1
        assert topology["targets"][0]["target_id"] == "RINCON_1"
        assert topology["targets"][0]["display_name"] == "Living Room"
        assert topology["targets"][0]["members"] == ["RINCON_1"]
