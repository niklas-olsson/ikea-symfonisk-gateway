from unittest.mock import AsyncMock, MagicMock, patch

import pytest  # type: ignore
from renderer_sonos.heal import HealController


@pytest.fixture  # type: ignore
def heal_controller() -> HealController:
    return HealController(max_retries=2, base_backoff=0.01)


@pytest.mark.asyncio  # type: ignore
async def test_heal_group_membership_standalone_correct(heal_controller: HealController) -> None:
    # Device should be standalone, and is already standalone
    heal_controller.set_expected_topology({"uid_1": "uid_1"})

    mock_device = MagicMock()
    mock_device.uid = "uid_1"

    mock_group = MagicMock()
    mock_group.coordinator = mock_device
    mock_device.group = mock_group

    result = await heal_controller.heal_group_membership("uid_1", mock_device)

    assert result is True
    mock_device.unjoin.assert_not_called()
    mock_device.join.assert_not_called()


@pytest.mark.asyncio  # type: ignore
async def test_heal_group_membership_standalone_grouped(heal_controller: HealController) -> None:
    # Device should be standalone, but is currently grouped with another
    heal_controller.set_expected_topology({"uid_1": "uid_1"})

    mock_device = MagicMock()
    mock_device.uid = "uid_1"

    mock_other_coordinator = MagicMock()
    mock_other_coordinator.uid = "uid_2"

    mock_group = MagicMock()
    mock_group.coordinator = mock_other_coordinator
    mock_device.group = mock_group

    result = await heal_controller.heal_group_membership("uid_1", mock_device)

    assert result is True
    mock_device.unjoin.assert_called_once()
    mock_device.join.assert_not_called()


@pytest.mark.asyncio  # type: ignore
async def test_heal_group_membership_grouped_correct(heal_controller: HealController) -> None:
    # Device should be grouped with uid_2, and is grouped with uid_2
    heal_controller.set_expected_topology({"uid_1": "uid_2", "uid_2": "uid_2"})

    mock_device = MagicMock()
    mock_device.uid = "uid_1"

    mock_coordinator = MagicMock()
    mock_coordinator.uid = "uid_2"

    mock_group = MagicMock()
    mock_group.coordinator = mock_coordinator
    mock_device.group = mock_group

    result = await heal_controller.heal_group_membership("uid_1", mock_device, mock_coordinator)

    assert result is True
    mock_device.unjoin.assert_not_called()
    mock_device.join.assert_not_called()


@pytest.mark.asyncio  # type: ignore
async def test_heal_group_membership_grouped_standalone(heal_controller: HealController) -> None:
    # Device should be grouped with uid_2, but is standalone
    heal_controller.set_expected_topology({"uid_1": "uid_2", "uid_2": "uid_2"})

    mock_device = MagicMock()
    mock_device.uid = "uid_1"

    # Standalone
    mock_group = MagicMock()
    mock_group.coordinator = mock_device
    mock_device.group = mock_group

    mock_coordinator = MagicMock()
    mock_coordinator.uid = "uid_2"

    result = await heal_controller.heal_group_membership("uid_1", mock_device, mock_coordinator)

    assert result is True
    mock_device.join.assert_called_once_with(mock_coordinator)


@pytest.mark.asyncio  # type: ignore
async def test_heal_group_membership_retry(heal_controller: HealController) -> None:
    # Device should be grouped with uid_2, but join fails on first attempt
    heal_controller.set_expected_topology({"uid_1": "uid_2", "uid_2": "uid_2"})

    mock_device = MagicMock()
    mock_device.uid = "uid_1"

    mock_group = MagicMock()
    mock_group.coordinator = mock_device
    mock_device.group = mock_group

    mock_coordinator = MagicMock()
    mock_coordinator.uid = "uid_2"

    # First call raises an error, second call succeeds
    mock_device.join.side_effect = [Exception("Network error"), None]

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        result = await heal_controller.heal_group_membership("uid_1", mock_device, mock_coordinator)

        assert result is True
        assert mock_device.join.call_count == 2
        mock_sleep.assert_called_once()
