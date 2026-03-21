import sys
from unittest.mock import MagicMock, patch

# Mock sounddevice BEFORE importing the adapter
mock_sd = MagicMock()
sys.modules["sounddevice"] = mock_sd

import numpy as np  # noqa: E402
from adapter_windows_audio import WindowsAudioAdapter  # noqa: E402


def test_adapter_id():
    adapter = WindowsAudioAdapter()
    assert adapter.id() == "windows-audio-adapter"


def test_adapter_platform():
    adapter = WindowsAudioAdapter()
    assert adapter.platform() == "windows"


def test_adapter_capabilities():
    adapter = WindowsAudioAdapter()
    caps = adapter.capabilities()
    assert caps.supports_system_audio is True
    assert 48000 in caps.supports_sample_rates
    assert 2 in caps.supports_channels


@patch("platform.system")
@patch("sounddevice.query_devices")
@patch("sounddevice.query_hostapis")
def test_list_sources_windows(mock_query_hostapis, mock_query_devices, mock_platform):
    mock_platform.return_value = "Windows"

    # Mock WASAPI host API
    mock_query_hostapis.return_value = [{"name": "MME"}, {"name": "Windows WASAPI"}, {"name": "DirectSound"}]

    # Mock devices
    mock_query_devices.return_value = [
        {"name": "Speakers (Realtek)", "hostapi": 1, "max_input_channels": 2, "default_samplerate": 48000.0},
        {"name": "Microphone (Realtek)", "hostapi": 1, "max_input_channels": 1, "default_samplerate": 44100.0},
        {"name": "Other device", "hostapi": 0, "max_input_channels": 2, "default_samplerate": 48000.0},
    ]

    adapter = WindowsAudioAdapter()
    sources = adapter.list_sources()

    assert len(sources) >= 2
    assert any(s.display_name == "Speakers (Realtek)" for s in sources)
    assert any(s.display_name == "Microphone (Realtek)" for s in sources)


@patch("platform.system")
def test_list_sources_non_windows(mock_platform):
    mock_platform.return_value = "Linux"
    adapter = WindowsAudioAdapter()
    sources = adapter.list_sources()
    assert sources == []


@patch("platform.system")
@patch("sounddevice.InputStream")
def test_start_stop_session(mock_input_stream, mock_platform):
    mock_platform.return_value = "Windows"
    adapter = WindowsAudioAdapter()
    frame_sink = MagicMock()

    # Start
    result = adapter.start("default", frame_sink)
    assert result.success is True
    assert result.session_id is not None
    assert adapter._running is True

    # Stop
    adapter.stop(result.session_id)
    assert adapter._running is False
    assert adapter._session_id is None


def test_audio_callback():
    adapter = WindowsAudioAdapter()
    frame_sink = MagicMock()
    adapter._frame_sink = frame_sink
    adapter._running = True

    # 10ms of 48kHz stereo 16-bit PCM = 480 frames
    indata = np.zeros((480, 2), dtype="int16")
    adapter._audio_callback(indata, 480, None, None)

    frame_sink.on_frame.assert_called_once()
    args, _ = frame_sink.on_frame.call_args
    assert len(args[0]) == 480 * 2 * 2  # bytes
    assert args[1] == 0  # pts_ns
    assert args[2] == 10000000  # duration_ns (10ms)
