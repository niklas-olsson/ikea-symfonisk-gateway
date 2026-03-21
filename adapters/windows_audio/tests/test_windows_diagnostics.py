import sys
from unittest.mock import MagicMock, patch

# Mock sounddevice BEFORE importing the adapter
mock_sd = MagicMock()
sys.modules["sounddevice"] = mock_sd

from adapter_windows_audio import WindowsAudioAdapter  # noqa: E402


def test_probe_health_signal_presence() -> None:
    adapter = WindowsAudioAdapter()
    adapter._running = True

    health1 = adapter.probe_health("default")
    assert health1.signal_present is False

    adapter._samples_captured = 1000
    health2 = adapter.probe_health("default")
    assert health2.signal_present is True

    health3 = adapter.probe_health("default")
    assert health3.signal_present is False

    adapter._samples_captured = 2000
    health4 = adapter.probe_health("default")
    assert health4.signal_present is True


def test_prepare_library_misconfigured() -> None:
    with patch("adapter_windows_audio._get_sd", return_value=None):
        with patch("platform.system", return_value="Windows"):
            adapter = WindowsAudioAdapter()
            res = adapter.prepare("default")
            assert res.success is False
            assert res.code == "windows_audio_library_misconfigured"
