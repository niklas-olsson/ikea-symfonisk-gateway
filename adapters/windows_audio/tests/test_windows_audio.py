from unittest.mock import MagicMock, patch

import numpy as np
from adapter_windows_audio import WindowsAudioAdapter
from adapter_windows_audio.backends import NullWindowsBackend, PyAudioWPatchBackend
from adapter_windows_audio.backends.models import BackendProbeResult
from ingress_sdk.types import SourceType


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
    fake_module = MagicMock()
    fake_module.PyAudio.return_value.get_default_wasapi_loopback.return_value = {"index": 7}
    mock_load.return_value = fake_module

    adapter = WindowsAudioAdapter()

    assert isinstance(adapter._backend, PyAudioWPatchBackend)
    sources = adapter.list_sources()
    assert len(sources) == 1
    assert sources[0].source_type == SourceType.SYSTEM_OUTPUT
    assert sources[0].metadata["backend"] == "pyaudiowpatch"
    assert sources[0].metadata["degraded"] is False


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
    assert sources[0].metadata["error_code"] == "windows_loopback_backend_missing"

    prepare = adapter.prepare("default")
    assert prepare.success is False
    assert prepare.code == "windows_loopback_backend_missing"


@patch("platform.system", return_value="Windows")
@patch("adapter_windows_audio.load_pyaudiowpatch")
def test_probe_failure_yields_degraded_source(mock_load: MagicMock, mock_platform: MagicMock) -> None:
    del mock_platform
    fake_module = MagicMock()
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
    fake_module = MagicMock()
    fake_module.PyAudio.return_value.get_default_wasapi_loopback.return_value = {"index": 1}
    fake_module.paContinue = 0
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
    assert callback_result == (None, 0)


@patch("platform.system", return_value="Windows")
@patch("adapter_windows_audio.load_pyaudiowpatch")
def test_start_stop_session_delegates_backend(mock_load: MagicMock, mock_platform: MagicMock) -> None:
    del mock_platform
    fake_stream = MagicMock()
    fake_stream.start_stream = MagicMock()
    fake_module = MagicMock()
    fake_module.paInt16 = 8
    fake_module.paContinue = 0
    fake_module.PyAudio.return_value.get_default_wasapi_loopback.return_value = {"index": 3}
    fake_module.PyAudio.return_value.open.return_value = fake_stream
    mock_load.return_value = fake_module

    adapter = WindowsAudioAdapter()
    result = adapter.start("default", MagicMock())

    assert result.success is True
    assert result.backend == "pyaudiowpatch"

    adapter.stop(result.session_id)
    fake_stream.stop_stream.assert_called_once()
    fake_stream.close.assert_called_once()
