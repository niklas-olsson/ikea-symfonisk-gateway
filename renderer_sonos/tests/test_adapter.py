import pytest
from unittest.mock import MagicMock, patch
from typing import Any
from renderer_sonos import SonosRendererAdapter

@pytest.mark.asyncio
async def test_sonos_renderer_adapter() -> None:
    adapter = SonosRendererAdapter()

    assert adapter.id() == "sonos-renderer-v1"

    device = MagicMock()
    device.uid = "RINCON_1"
    device.player_name = "Living Room"
    device.ip_address = "1.2.3.4"
    device.get_speaker_info.return_value = {"model_name": "SYMFONISK Bookshelf"}

    group = MagicMock()
    group.coordinator = device
    group.members = [device]
    device.group = group

    with patch('soco.discover', return_value={device}):
        # Mock run_in_executor to execute the function directly for testing
        import asyncio
        loop = asyncio.get_event_loop()
        original_run_in_executor = loop.run_in_executor

        async def mock_run_in_executor(executor: Any, func: Any, *args: Any) -> Any:
            return func(*args)

        with patch.object(loop, 'run_in_executor', new=mock_run_in_executor):
            await adapter._ensure_discovery_started()

            # Allow time for bg task to finish one iteration
            await asyncio.sleep(0.1)

            targets = await adapter.list_targets()

            assert len(targets) > 0, "Targets not populated. Background task didn't run as expected."
            assert targets[0].target_id == "RINCON_1"
            assert targets[0].display_name == "Living Room"
            assert targets[0].renderer == "sonos"

            topology = await adapter.get_topology()
            assert topology["renderer"] == "sonos"
            assert topology["discovered"] is True
            assert len(topology["targets"]) == 1
            assert topology["targets"][0]["target_id"] == "RINCON_1"

            # Clean up task
            adapter._discovery.stop_background_discovery()
            # wait for it to exit
            await asyncio.sleep(0.1)
