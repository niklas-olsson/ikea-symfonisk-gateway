import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bridge_core.core.event_bus import EventBus, EventType

from renderer_sonos import SonosRendererAdapter, SonosTargetDescriptor


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture
def adapter(event_bus: EventBus) -> SonosRendererAdapter:
    return SonosRendererAdapter(event_bus)


@pytest.mark.asyncio
async def test_play_stream_success(adapter: SonosRendererAdapter) -> None:
    mock_player = MagicMock()
    mock_player.uid = "RINCON_1"
    mock_player.group = None
    adapter._players["RINCON_1"] = mock_player

    # Mock target descriptor
    mock_target = SonosTargetDescriptor(
        target_id="RINCON_1", target_type="speaker", display_name="Living Room", members=["RINCON_1"], coordinator_id="RINCON_1"
    )
    adapter._targets["RINCON_1"] = mock_target

    with patch.object(adapter, "_run_with_retry", new_callable=AsyncMock) as mock_retry:
        result = await adapter.play_stream("RINCON_1", "http://stream.url", {"title": "Test Title"})

        assert result["success"] is True
        # Verify it was called with title from metadata
        mock_retry.assert_called_with(mock_player.play_uri, "http://stream.url", title="Test Title")
        assert adapter._last_played["RINCON_1"]["stream_url"] == "http://stream.url"


@pytest.mark.asyncio
async def test_play_stream_target_not_found(adapter: SonosRendererAdapter) -> None:
    result = await adapter.play_stream("UNKNOWN", "http://stream.url")
    assert result["success"] is False
    assert "not found" in result["error"]


@pytest.mark.asyncio
async def test_stop_success(adapter: SonosRendererAdapter) -> None:
    mock_player = MagicMock()
    mock_player.uid = "RINCON_1"
    adapter._players["RINCON_1"] = mock_player
    adapter._last_played["RINCON_1"] = {"stream_url": "url"}

    with patch.object(adapter, "_run_with_retry", new_callable=AsyncMock) as mock_retry:
        result = await adapter.stop("RINCON_1")

        assert result["success"] is True
        mock_retry.assert_called_once_with(mock_player.stop)
        # Verify last_played was cleared
        assert "RINCON_1" not in adapter._last_played


@pytest.mark.asyncio
async def test_set_volume_success(adapter: SonosRendererAdapter) -> None:
    mock_player = MagicMock()
    mock_player.uid = "RINCON_1"
    mock_player.group = MagicMock()
    adapter._players["RINCON_1"] = mock_player

    with patch.object(adapter, "_run_with_retry", new_callable=AsyncMock) as mock_retry:
        result = await adapter.set_volume("RINCON_1", 0.5)

        assert result["success"] is True
        assert result["volume"] == 0.5

        # Verify that the lambda in set_volume is called
        func, player, volume = mock_retry.call_args[0]
        assert player == mock_player
        assert volume == 50

        # Call the captured function to ensure it sets the group volume
        func(player, volume)
        assert player.group.volume == 50


@pytest.mark.asyncio
async def test_run_with_retry_backoff(adapter: SonosRendererAdapter) -> None:
    mock_func = MagicMock(side_effect=[Exception("Fail 1"), Exception("Fail 2"), "Success"])

    # Use shorter delays for testing
    result = await adapter._run_with_retry(mock_func, initial_delay=0.01)

    assert result == "Success"
    assert mock_func.call_count == 3


@pytest.mark.asyncio
async def test_run_with_retry_exhaustion(adapter: SonosRendererAdapter) -> None:
    mock_func = MagicMock(side_effect=Exception("Always fails"))

    with pytest.raises(Exception, match="Always fails"):
        await adapter._run_with_retry(mock_func, max_retries=2, initial_delay=0.01)

    assert mock_func.call_count == 2


@pytest.mark.asyncio
async def test_heal_success(adapter: SonosRendererAdapter, event_bus: EventBus) -> None:
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

    with (
        patch.object(adapter, "_run_with_retry", new_callable=AsyncMock) as mock_retry,
        patch.object(adapter, "list_targets", new_callable=AsyncMock),
    ):
        result = await adapter.heal("GROUP_1")

        assert result["success"] is True

        # Verify that join was called via _run_with_retry
        join_call = None
        for call in mock_retry.call_args_list:
            if "join" in str(call):
                join_call = call
                break

        assert join_call is not None
        func, member, coord = join_call[0]
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
async def test_heal_restarts_playback(adapter: SonosRendererAdapter) -> None:
    mock_target = MagicMock()
    mock_target.target_id = "COORD_1"
    mock_target.coordinator_id = "COORD_1"
    mock_target.members = ["COORD_1"]
    adapter._targets["COORD_1"] = mock_target

    mock_coord = MagicMock()
    mock_coord.uid = "COORD_1"
    # Not playing currently
    mock_coord.get_current_transport_info.return_value = {"current_transport_state": "STOPPED"}

    adapter._players["COORD_1"] = mock_coord
    adapter._last_played["COORD_1"] = {"stream_url": "http://stream.url", "metadata": {"title": "Saved Title"}}

    with (
        patch.object(adapter, "_run_with_retry", new_callable=AsyncMock) as mock_retry,
        patch.object(adapter, "list_targets", new_callable=AsyncMock),
    ):
        # We need to simulate _run_with_retry executing the lambda
        async def side_effect(func: Any, *args: Any, **kwargs: Any) -> Any:
            return func(*args, **kwargs)

        mock_retry.side_effect = side_effect

        result = await adapter.heal("COORD_1")

        assert result["success"] is True
        assert result["playback_restarted"] is True
        mock_coord.play_uri.assert_called_once_with("http://stream.url", title="Saved Title")


@pytest.mark.asyncio
async def test_prepare_target_success(adapter: SonosRendererAdapter) -> None:
    mock_player = MagicMock()
    mock_player.uid = "RINCON_1"
    mock_player.is_coordinator = True
    adapter._players["RINCON_1"] = mock_player

    with (
        patch.object(adapter, "list_targets", new_callable=AsyncMock) as mock_list,
        patch.object(adapter, "_run_with_retry", new_callable=AsyncMock) as mock_retry,
    ):
        # Simulate status check
        async def side_effect(func: Any, *args: Any, **kwargs: Any) -> Any:
            return func(*args, **kwargs)

        mock_retry.side_effect = side_effect

        result = await adapter.prepare_target("RINCON_1")

        assert result["success"] is True
        mock_list.assert_called_once()
        mock_player.get_current_transport_info.assert_called_once()
