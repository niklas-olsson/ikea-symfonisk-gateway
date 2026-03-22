from unittest.mock import MagicMock, patch

import numpy as np
from adapter_windows_audio import WindowsAudioAdapter
from adapter_windows_audio.backends import NullWindowsBackend, PyAudioWPatchBackend, resolve_default_loopback_triplet
from adapter_windows_audio.backends.models import BackendProbeResult
from ingress_sdk.types import SourceType


def _build_fake_backend_module(loopback_device: dict[str, object] | None = None) -> MagicMock:
    fake_module = MagicMock()
    fake_module.paInt16 = 8
    fake_module.paContinue = 0
    fake_pa = MagicMock()
    fake_pa.get_default_wasapi_loopback.return_value = loopback_device
    fake_pa.get_default_wasapi_loopback_device_info = None
    fake_pa.get_default_output_device_info.return_value = {"index": 2, "name": "Speakers", "hostApi": 0}
    fake_pa.get_host_api_count.return_value = 1
    fake_pa.get_host_api_info_by_index.side_effect = lambda index: {"index": index, "name": "Windows WASAPI"}
    fake_pa.get_device_count.return_value = 2
    fake_pa.get_device_info_by_index.side_effect = lambda index: [
        {"index": 2, "name": "Speakers", "hostApi": 0},
        {"index": 7, "name": "Speakers (loopback)", "hostApi": 0, "isLoopbackDevice": True},
    ][index]
    fake_module.PyAudio.return_value = fake_pa
    return fake_module


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
    assert caps.supports_microphone is False
    assert caps.supports_line_in is False
    assert caps.supports_sample_rates == [48000]
    assert caps.supports_channels == [2]


@patch("platform.system", return_value="Windows")
@patch("adapter_windows_audio.load_pyaudiowpatch")
def test_selects_pyaudiowpatch_backend(mock_load: MagicMock, mock_platform: MagicMock) -> None:
    del mock_platform
    fake_module = _build_fake_backend_module(loopback_device={"index": 7, "name": "Speakers (loopback)", "hostApi": 0})
    mock_load.return_value = fake_module

    adapter = WindowsAudioAdapter()

    assert isinstance(adapter._backend, PyAudioWPatchBackend)
    sources = adapter.list_sources()
    assert len(sources) == 1
    assert sources[0].source_type == SourceType.SYSTEM_OUTPUT
    assert sources[0].metadata["backend"] == "pyaudiowpatch"
    assert sources[0].metadata["degraded"] is False
    assert sources[0].metadata["status"] == "ready"
    assert sources[0].metadata["startable"] is True
    assert sources[0].metadata["backend_probe"]["available"] is True
    assert sources[0].metadata["non_empty_buffer_count"] == 0


@patch("platform.system", return_value="Windows")
@patch("adapter_windows_audio.load_pyaudiowpatch", return_value=None)
def test_missing_backend_yields_degraded_source(mock_load: MagicMock, mock_platform: MagicMock) -> None:
    del mock_load, mock_platform
    adapter = WindowsAudioAdapter()

    sources = adapter.list_sources()

    assert len(sources) == 1
    assert sources[0].display_name == "Default System Sound (windows)"
    assert sources[0].source_type == SourceType.SYSTEM_OUTPUT
    assert sources[0].metadata["degraded"] is True
    assert sources[0].metadata["status"] == "degraded"
    assert sources[0].metadata["startable"] is False
    assert sources[0].metadata["error_code"] == "windows_loopback_backend_missing"

    prepare = adapter.prepare("default")
    assert prepare.success is False
    assert prepare.code == "windows_loopback_backend_missing"


@patch("platform.system", return_value="Windows")
@patch("adapter_windows_audio.load_pyaudiowpatch")
def test_probe_failure_yields_degraded_source(mock_load: MagicMock, mock_platform: MagicMock) -> None:
    del mock_platform
    fake_module = _build_fake_backend_module()
    fake_module.PyAudio.return_value.get_default_wasapi_loopback.side_effect = RuntimeError("probe failed")
    mock_load.return_value = fake_module

    adapter = WindowsAudioAdapter()
    source = adapter.list_sources()[0]

    assert source.metadata["degraded"] is True
    assert source.metadata["error_code"] == "windows_loopback_probe_failed"


def test_null_backend_fails_with_probe_error() -> None:
    backend = NullWindowsBackend(
        BackendProbeResult(
            backend="pyaudiowpatch",
            available=False,
            state="missing_dependency",
            loopback_supported=False,
            default_output_detected=False,
            code="windows_loopback_backend_missing",
            message="No Windows loopback-capable backend is installed.",
        )
    )

    result = backend.start("default", MagicMock())
    assert result.success is False
    assert result.code == "windows_loopback_backend_missing"


def test_pyaudiowpatch_callback_emits_canonical_frame() -> None:
    fake_module = _build_fake_backend_module(loopback_device={"index": 1, "name": "Loopback", "hostApi": 0})
    backend = PyAudioWPatchBackend(backend_module=fake_module)
    frame_sink = MagicMock()
    backend._frame_sink = frame_sink
    backend._running = True

    audio = np.zeros((480, 2), dtype=np.int16)
    callback_result = backend._audio_callback(audio, 480, None, 0)

    frame_sink.on_frame.assert_called_once()
    args, _ = frame_sink.on_frame.call_args
    assert len(args[0]) == 480 * 2 * 2
    assert args[1] == 0
    assert args[2] == 10_000_000
    diagnostics = backend.get_diagnostics_snapshot()
    assert diagnostics["frames_emitted"] == 1
    assert diagnostics["non_empty_buffer_count"] == 1
    assert diagnostics["startup_substate"] == "healthy_but_idle"
    assert callback_result == (None, 0)


@patch("platform.system", return_value="Windows")
@patch("adapter_windows_audio.load_pyaudiowpatch")
def test_start_stop_session_delegates_backend(mock_load: MagicMock, mock_platform: MagicMock) -> None:
    del mock_platform
    fake_stream = MagicMock()
    fake_stream.start_stream = MagicMock()
    fake_module = _build_fake_backend_module(loopback_device={"index": 3, "name": "Loopback", "hostApi": 0})
    fake_module.PyAudio.return_value.open.return_value = fake_stream
    mock_load.return_value = fake_module

    adapter = WindowsAudioAdapter()
    result = adapter.start("default", MagicMock())

    assert result.success is True
    assert result.backend == "pyaudiowpatch"

    adapter.stop(result.session_id)
    fake_stream.stop_stream.assert_called_once()
    fake_stream.close.assert_called_once()


@patch("adapter_windows_audio.backends.pyaudiowpatch_backend.load_pyaudiowpatch")
def test_pyaudiowpatch_fallback_sample_rate(mock_load: MagicMock) -> None:
    loopback_device = {"index": 7, "name": "Loopback", "hostApi": 0, "defaultSampleRate": 48000}
    fake_module = _build_fake_backend_module(loopback_device=loopback_device)
    mock_load.return_value = fake_module
    fake_pa = fake_module.PyAudio.return_value

    # Mock open to fail for 48000 but succeed for 44100
    def mock_open(*args, **kwargs):
        rate = kwargs.get("rate")
        if rate == 48000:
            raise RuntimeError("Invalid sample rate")
        return MagicMock()

    fake_pa.open.side_effect = mock_open

    backend = PyAudioWPatchBackend(backend_module=fake_module)
    frame_sink = MagicMock()
    result = backend.start("default", frame_sink)

    assert result.success is True
    assert backend._diagnostics.actual_sample_rate == 44100
    assert 48000 in backend._diagnostics.attempted_rates
    assert 44100 in backend._diagnostics.attempted_rates


@patch("adapter_windows_audio.backends.pyaudiowpatch_backend.load_pyaudiowpatch")
def test_pyaudiowpatch_resampling_normalization(mock_load: MagicMock) -> None:
    loopback_device = {"index": 7, "name": "Loopback", "hostApi": 0, "defaultSampleRate": 44100}
    fake_module = _build_fake_backend_module(loopback_device=loopback_device)
    mock_load.return_value = fake_module
    fake_pa = fake_module.PyAudio.return_value
    fake_pa.open.return_value = MagicMock()

    backend = PyAudioWPatchBackend(backend_module=fake_module)
    frame_sink = MagicMock()
    backend.start("default", frame_sink)

    assert backend._diagnostics.actual_sample_rate == 44100

    # Simulate 441 frames at 44.1kHz (10ms)
    audio_44100 = np.zeros((441, 2), dtype=np.int16)
    backend._audio_callback(audio_44100, 441, None, 0)

    # Should be normalized to 480 frames (10ms at 48kHz)
    frame_sink.on_frame.assert_called_once()
    args, _ = frame_sink.on_frame.call_args
    data_bytes, pts_ns, duration_ns = args
    assert len(data_bytes) == 480 * 2 * 2
    assert duration_ns == 10_000_000


@patch("adapter_windows_audio.backends.pyaudiowpatch_backend.load_pyaudiowpatch")
def test_pyaudiowpatch_hard_failure_all_rates(mock_load: MagicMock) -> None:
    loopback_device = {"index": 7, "name": "Loopback", "hostApi": 0, "defaultSampleRate": 96000}
    fake_module = _build_fake_backend_module(loopback_device=loopback_device)
    mock_load.return_value = fake_module
    fake_pa = fake_module.PyAudio.return_value

    # Mock open to always fail
    fake_pa.open.side_effect = RuntimeError("Invalid sample rate")

    backend = PyAudioWPatchBackend(backend_module=fake_module)
    frame_sink = MagicMock()
    result = backend.start("default", frame_sink)

    assert result.success is False
    assert result.code == "windows_loopback_start_failed"
    assert backend._diagnostics.startup_substate == "stream_open_failed"
    assert len(backend._diagnostics.attempted_rates) >= 3


def test_loopback_resolution_falls_back_to_enumerated_devices() -> None:
    fake_module = _build_fake_backend_module(loopback_device=None)

    host_api, render_device, loopback_device = resolve_default_loopback_triplet(fake_module)

    assert host_api is not None
    assert render_device is not None
    assert loopback_device is not None
    assert loopback_device["isLoopbackDevice"] is True
