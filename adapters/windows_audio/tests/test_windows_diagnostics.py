from unittest.mock import MagicMock

from adapter_windows_audio.backends import PyAudioWPatchBackend


def test_probe_health_signal_presence() -> None:
    fake_module = MagicMock()
    fake_module.PyAudio.return_value.get_default_wasapi_loopback.return_value = {"index": 1}
    backend = PyAudioWPatchBackend(backend_module=fake_module)
    backend._running = True

    health1 = backend.probe_health("default")
    assert health1.signal_present is False

    backend._samples_captured = 1000
    health2 = backend.probe_health("default")
    assert health2.signal_present is True

    health3 = backend.probe_health("default")
    assert health3.signal_present is False


def test_prepare_probe_failure() -> None:
    fake_module = MagicMock()
    fake_module.PyAudio.return_value.get_default_wasapi_loopback.side_effect = RuntimeError("probe failed")
    backend = PyAudioWPatchBackend(backend_module=fake_module)

    res = backend.prepare("default")

    assert res.success is False
    assert res.code == "windows_loopback_probe_failed"
