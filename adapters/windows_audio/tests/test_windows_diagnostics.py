from unittest.mock import MagicMock

from adapter_windows_audio.backends import PyAudioWPatchBackend


def _build_fake_backend_module() -> MagicMock:
    fake_module = MagicMock()
    fake_module.paContinue = 0
    fake_pa = MagicMock()
    fake_pa.get_default_wasapi_loopback.return_value = {"index": 1, "name": "Loopback", "hostApi": 0}
    fake_pa.get_default_output_device_info.return_value = {"index": 0, "name": "Speakers", "hostApi": 0}
    fake_pa.get_host_api_count.return_value = 1
    fake_pa.get_host_api_info_by_index.side_effect = lambda index: {"index": index, "name": "Windows WASAPI"}
    fake_pa.get_device_count.return_value = 1
    fake_pa.get_device_info_by_index.side_effect = lambda index: {"index": index, "name": "Loopback", "hostApi": 0}
    fake_module.PyAudio.return_value = fake_pa
    return fake_module


def test_probe_health_signal_presence() -> None:
    fake_module = _build_fake_backend_module()
    backend = PyAudioWPatchBackend(backend_module=fake_module)
    backend._running = True

    health1 = backend.probe_health("default")
    assert health1.signal_present is False

    backend._non_silent_signal_detected = True
    backend._diagnostics.startup_substate = "active"
    health2 = backend.probe_health("default")
    assert health2.signal_present is True

    health3 = backend.probe_health("default")
    assert health3.signal_present is True


def test_prepare_probe_failure() -> None:
    fake_module = _build_fake_backend_module()
    fake_module.PyAudio.return_value.get_default_wasapi_loopback.side_effect = RuntimeError("probe failed")
    backend = PyAudioWPatchBackend(backend_module=fake_module)

    res = backend.prepare("default")

    assert res.success is False
    assert res.code == "windows_loopback_probe_failed"


def test_probe_health_reports_callback_substate() -> None:
    backend = PyAudioWPatchBackend(backend_module=_build_fake_backend_module())
    backend._running = True
    backend._diagnostics.startup_substate = "stream_started_no_callbacks"

    health = backend.probe_health("default")

    assert health.source_state == "stream_started_no_callbacks"


def test_zero_length_callback_is_not_treated_as_idle_signal() -> None:
    backend = PyAudioWPatchBackend(backend_module=_build_fake_backend_module())
    backend._running = True
    backend._frame_sink = MagicMock()

    backend._audio_callback(b"", 0, None, 0)

    diagnostics = backend.get_diagnostics_snapshot()
    assert diagnostics.callback_count == 1
    assert diagnostics.non_empty_buffer_count == 0
    assert diagnostics.startup_substate == "callbacks_active_no_samples"
