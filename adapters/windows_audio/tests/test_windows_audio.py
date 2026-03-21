import sys
from unittest.mock import MagicMock, patch

mock_sd = MagicMock()
sys.modules["sounddevice"] = mock_sd

import numpy as np  # noqa: E402
from adapter_windows_audio import WindowsAudioAdapter  # noqa: E402
from ingress_sdk.types import SourceType  # noqa: E402


def test_adapter_id() -> None:
    adapter = WindowsAudioAdapter()
    assert adapter.id() == "windows-audio-adapter"


def test_adapter_platform() -> None:
    adapter = WindowsAudioAdapter()
    assert adapter.platform() == "windows"


def test_adapter_capabilities() -> None:
    adapter = WindowsAudioAdapter()
    caps = adapter.capabilities()
    assert caps.supports_system_audio is True
    assert 48000 in caps.supports_sample_rates
    assert 2 in caps.supports_channels


@patch("platform.system")
@patch("adapter_windows_audio._get_sd")
def test_list_sources_windows(mock_get_sd: MagicMock, mock_platform: MagicMock) -> None:
    mock_platform.return_value = "Windows"
    local_mock_sd = MagicMock()
    mock_get_sd.return_value = local_mock_sd

    local_mock_sd.query_hostapis.return_value = [{"name": "MME"}, {"name": "Windows WASAPI"}, {"name": "DirectSound"}]

    local_mock_sd.query_devices.return_value = [
        {"name": "Speakers (Realtek)", "hostapi": 1, "max_input_channels": 0, "max_output_channels": 2, "default_samplerate": 48000.0},
        {"name": "Microphone (Realtek)", "hostapi": 1, "max_input_channels": 1, "max_output_channels": 0, "default_samplerate": 44100.0},
        {"name": "Other device", "hostapi": 0, "max_input_channels": 2, "max_output_channels": 0, "default_samplerate": 48000.0},
    ]

    adapter = WindowsAudioAdapter()
    sources = adapter.list_sources()

    assert len(sources) == 3
    assert sources[0].display_name == "Default System Sound windows"
    assert sources[0].source_type == SourceType.SYSTEM_AUDIO

    assert sources[1].display_name == "Speakers (Realtek)"
    assert sources[1].source_type == SourceType.SYSTEM_AUDIO

    assert sources[2].display_name == "Microphone (Realtek)"
    assert sources[2].source_type == SourceType.MICROPHONE


@patch("platform.system")
def test_list_sources_non_windows(mock_platform: MagicMock) -> None:
    mock_platform.return_value = "Linux"
    adapter = WindowsAudioAdapter()
    sources = adapter.list_sources()
    assert len(sources) == 1
    assert sources[0].display_name == "Default System Sound windows"


@patch("platform.system")
@patch("adapter_windows_audio._get_sd")
def test_start_stop_session(mock_get_sd: MagicMock, mock_platform: MagicMock) -> None:
    mock_platform.return_value = "Windows"
    local_mock_sd = MagicMock()
    mock_get_sd.return_value = local_mock_sd

    adapter = WindowsAudioAdapter()
    frame_sink = MagicMock()

    result = adapter.start("default", frame_sink)
    assert result.success is True
    assert result.session_id is not None
    assert adapter._running is True
    local_mock_sd.WasapiSettings.assert_called_once_with(loopback=True)

    adapter.stop(result.session_id)
    assert adapter._running is False
    assert adapter._session_id is None


@patch("platform.system")
@patch("adapter_windows_audio._get_sd")
def test_start_loopback_not_supported(mock_get_sd: MagicMock, mock_platform: MagicMock) -> None:
    mock_platform.return_value = "Windows"
    local_mock_sd = MagicMock()
    mock_get_sd.return_value = local_mock_sd

    # Mock WasapiSettings to raise TypeError (e.g. unexpected keyword argument 'loopback')
    local_mock_sd.WasapiSettings.side_effect = TypeError("Unexpected keyword argument 'loopback'")

    adapter = WindowsAudioAdapter()
    frame_sink = MagicMock()

    result = adapter.start("default", frame_sink)

    assert result.success is False
    assert result.code == "windows_loopback_not_supported"
    assert "Windows system audio capture requires wasapi_loopback or similar Windows-native backend" in result.message


def test_audio_callback() -> None:
    adapter = WindowsAudioAdapter()
    frame_sink = MagicMock()
    adapter._frame_sink = frame_sink
    adapter._running = True

    indata = np.zeros((480, 2), dtype="int16")
    adapter._audio_callback(indata, 480, None, None)

    frame_sink.on_frame.assert_called_once()
    args, _ = frame_sink.on_frame.call_args
    assert len(args[0]) == 480 * 2 * 2
    assert args[1] == 0
    assert args[2] == 10000000


def test_audio_callback_mono() -> None:
    adapter = WindowsAudioAdapter()
    frame_sink = MagicMock()
    adapter._frame_sink = frame_sink
    adapter._running = True

    indata = np.zeros((480, 1), dtype="int16")
    adapter._audio_callback(indata, 480, None, None)

    frame_sink.on_frame.assert_called_once()
    args, _ = frame_sink.on_frame.call_args
    assert len(args[0]) == 480 * 2 * 2
    assert args[1] == 0
    assert args[2] == 10000000
