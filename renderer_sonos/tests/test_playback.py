import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bridge_core.core.event_bus import EventBus, EventType
from renderer_sonos import SonosRendererAdapter


@pytest.fixture
def event_bus():
    return EventBus()


@pytest.fixture
def adapter(event_bus):
    return SonosRendererAdapter(event_bus)


@pytest.mark.asyncio
async def test_play_stream_success(adapter):
    mock_player = MagicMock()
    mock_player.uid = "RINCON_1"
    adapter._players["RINCON_1"] = mock_player

    with patch.object(adapter, "_run_with_retry", new_callable=AsyncMock) as mock_retry:
        result = await adapter.play_stream("RINCON_1", "http://stream.url")

        assert result["success"] is True
        mock_retry.assert_called_once_with(mock_player.play_uri, "http://stream.url")


@pytest.mark.asyncio
async def test_play_stream_target_not_found(adapter):
    result = await adapter.play_stream("UNKNOWN", "http://stream.url")
    assert result["success"] is False
    assert "not found" in result["error"]


@pytest.mark.asyncio
async def test_stop_success(adapter):
    mock_player = MagicMock()
    mock_player.uid = "RINCON_1"
    adapter._players["RINCON_1"] = mock_player

    with patch.object(adapter, "_run_with_retry", new_callable=AsyncMock) as mock_retry:
        result = await adapter.stop("RINCON_1")

        assert result["success"] is True
        mock_retry.assert_called_once_with(mock_player.stop)


@pytest.mark.asyncio
async def test_set_volume_success(adapter):
    mock_player = MagicMock()
    mock_player.uid = "RINCON_1"
    adapter._players["RINCON_1"] = mock_player

    with patch.object(adapter, "_run_with_retry", new_callable=AsyncMock) as mock_retry:
        result = await adapter.set_volume("RINCON_1", 0.5)

        assert result["success"] is True
        assert result["volume"] == 0.5

        # Verify that the lambda in set_volume is called with correct volume
        # We need to capture the function passed to _run_with_retry
        func, player, volume = mock_retry.call_args[0]
        assert player == mock_player
        assert volume == 50

        # Call the captured function to ensure it sets the volume
        func(player, volume)
        assert player.volume == 50


@pytest.mark.asyncio
async def test_run_with_retry_backoff(adapter):
    mock_func = MagicMock(side_effect=[Exception("Fail 1"), Exception("Fail 2"), "Success"])

    # Use shorter delays for testing
    result = await adapter._run_with_retry(mock_func, initial_delay=0.01)

    assert result == "Success"
    assert mock_func.call_count == 3


@pytest.mark.asyncio
async def test_run_with_retry_exhaustion(adapter):
    mock_func = MagicMock(side_effect=Exception("Always fails"))

    with pytest.raises(Exception, match="Always fails"):
        await adapter._run_with_retry(mock_func, max_retries=2, initial_delay=0.01)

    assert mock_func.call_count == 2


@pytest.mark.asyncio
async def test_heal_success(adapter, event_bus):
    # Mock target with coordinator and one member
    mock_target = MagicMock()
    mock_target.target_id = "GROUP_1"
    mock_target.coordinator_id = "COORD_1"
    mock_target.members = ["COORD_1", "MEMBER_1"]
    adapter._targets["GROUP_1"] = mock_target

    mock_coord = MagicMock()
    mock_coord.uid = "COORD_1"
    mock_member = MagicMock()
    mock_member.uid = "MEMBER_1"
    mock_member.group = MagicMock()
    mock_member.group.coordinator = MagicMock()
    mock_member.group.coordinator.uid = "OTHER_COORD"  # Not currently in group

    adapter._players["COORD_1"] = mock_coord
    adapter._players["MEMBER_1"] = mock_member

    # Subscribe to events to verify emission
    queue = event_bus.subscribe()

    with patch.object(adapter, "_run_with_retry", new_callable=AsyncMock) as mock_retry:
        result = await adapter.heal("GROUP_1")

        assert result["success"] is True

        # Verify that join was called via _run_with_retry
        # We need to find the call that used join
        join_call = None
        for call in mock_retry.call_args_list:
            if "join" in str(call):
                join_call = call
                break

        assert join_call is not None
        # Check that the lambda or internal function called join
        func, member, coord = join_call[0]
        assert member == mock_member
        assert coord == mock_coord

        # Call the captured function to ensure it joins
        func(member, coord)
        member.join.assert_called_once_with(mock_coord)

    # Give event bus a moment to process tasks
    await asyncio.sleep(0.01)

    # Verify events
    events = []
    while not queue.empty():
        events.append(await queue.get())

    event_types = [e.type for e in events]
    assert EventType.HEAL_ATTEMPTED.value in event_types
    assert EventType.HEAL_SUCCEEDED.value in event_types


@pytest.mark.asyncio
async def test_heal_already_joined(adapter, event_bus):
    mock_target = MagicMock()
    mock_target.target_id = "GROUP_1"
    mock_target.coordinator_id = "COORD_1"
    mock_target.members = ["COORD_1", "MEMBER_1"]
    adapter._targets["GROUP_1"] = mock_target

    mock_coord = MagicMock()
    mock_coord.uid = "COORD_1"
    mock_member = MagicMock()
    mock_member.uid = "MEMBER_1"
    mock_member.group = MagicMock()
    mock_member.group.coordinator = MagicMock()
    mock_member.group.coordinator.uid = "COORD_1"  # Already in group

    adapter._players["COORD_1"] = mock_coord
    adapter._players["MEMBER_1"] = mock_member

    with patch.object(adapter, "_run_with_retry", new_callable=AsyncMock) as mock_retry:
        result = await adapter.heal("GROUP_1")
        assert result["success"] is True

        # Find the join call if any
        func, member, coord = mock_retry.call_args[0]
        func(member, coord)
        member.join.assert_not_called()


@pytest.mark.asyncio
async def test_heal_failure_emits_event(adapter, event_bus):
    mock_target = MagicMock()
    mock_target.target_id = "GROUP_1"
    mock_target.coordinator_id = "COORD_1"
    mock_target.members = ["COORD_1", "MEMBER_1"]
    adapter._targets["GROUP_1"] = mock_target

    mock_coord = MagicMock()
    mock_coord.uid = "COORD_1"
    mock_member = MagicMock()
    mock_member.uid = "MEMBER_1"
    mock_member.group = MagicMock()
    mock_member.group.coordinator = MagicMock()
    mock_member.group.coordinator.uid = "OTHER"

    adapter._players["COORD_1"] = mock_coord
    adapter._players["MEMBER_1"] = mock_member

    # Subscribe to events to verify emission
    queue = event_bus.subscribe()

    with patch.object(adapter, "_run_with_retry", side_effect=Exception("Heal failed")):
        result = await adapter.heal("GROUP_1")
        assert result["success"] is False

    # Give event bus a moment
    await asyncio.sleep(0.01)

    # Verify events
    events = []
    while not queue.empty():
        events.append(await queue.get())

    event_types = [e.type for e in events]
    assert EventType.HEAL_ATTEMPTED.value in event_types
    assert EventType.HEAL_FAILED.value in event_types
