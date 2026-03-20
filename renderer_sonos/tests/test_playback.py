from unittest.mock import MagicMock

import pytest  # type: ignore
from renderer_sonos.playback import PlaybackController


def test_playback_controller_add_get_device() -> None:
    controller = PlaybackController()
    mock_device = MagicMock()

    controller.add_device("target_1", mock_device)
    retrieved = controller.get_device("target_1")

    assert retrieved is mock_device
    assert controller.get_device("target_2") is None


def test_playback_controller_play_stream() -> None:
    controller = PlaybackController()
    mock_device = MagicMock()
    mock_coordinator = MagicMock()
    mock_coordinator.ip_address = "192.168.1.10"

    # Setup group/coordinator structure
    mock_group = MagicMock()
    mock_group.coordinator = mock_coordinator
    mock_device.group = mock_group

    controller.add_device("target_1", mock_device)

    controller.play_stream("target_1", "http://stream.url", {"metadata": "some_meta"})

    mock_coordinator.play_uri.assert_called_once_with("http://stream.url", meta="some_meta")


def test_playback_controller_play_stream_no_metadata() -> None:
    controller = PlaybackController()
    mock_device = MagicMock()
    mock_device.group = None  # Standalone
    mock_device.ip_address = "192.168.1.11"

    controller.add_device("target_1", mock_device)
    controller.play_stream("target_1", "http://stream.url")

    mock_device.play_uri.assert_called_once_with("http://stream.url", meta="")


def test_playback_controller_stop() -> None:
    controller = PlaybackController()
    mock_device = MagicMock()
    mock_coordinator = MagicMock()
    mock_coordinator.ip_address = "192.168.1.10"

    mock_group = MagicMock()
    mock_group.coordinator = mock_coordinator
    mock_device.group = mock_group

    controller.add_device("target_1", mock_device)
    controller.stop("target_1")

    mock_coordinator.stop.assert_called_once()


def test_playback_controller_set_volume() -> None:
    controller = PlaybackController()
    mock_device = MagicMock()
    mock_device.ip_address = "192.168.1.10"

    controller.add_device("target_1", mock_device)

    controller.set_volume("target_1", 0.75)

    assert mock_device.volume == 75


def test_playback_controller_set_volume_invalid() -> None:
    controller = PlaybackController()
    mock_device = MagicMock()

    controller.add_device("target_1", mock_device)

    with pytest.raises(ValueError):
        controller.set_volume("target_1", 1.5)

    with pytest.raises(ValueError):
        controller.set_volume("target_1", -0.1)
