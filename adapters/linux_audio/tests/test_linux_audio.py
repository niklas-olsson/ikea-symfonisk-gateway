import pytest
from adapter_linux_audio import LinuxAudioAdapter


def test_linux_audio_adapter_initializes() -> None:
    """Test that the adapter can be initialized."""
    adapter = LinuxAudioAdapter()
    assert adapter.id() == "linux-audio-adapter"
    assert adapter.platform() == "linux"


def test_linux_audio_capabilities() -> None:
    """Test that capabilities are correctly reported."""
    adapter = LinuxAudioAdapter()
    caps = adapter.capabilities()
    assert caps.supports_system_audio is True
    assert caps.supports_bluetooth_audio is False
    assert 48000 in caps.supports_sample_rates
    assert 2 in caps.supports_channels


def test_list_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test source listing, with mock subprocess to avoid real system dependencies."""
    adapter = LinuxAudioAdapter()

    # Mock subprocess.run to simulate pactl output
    import subprocess
    from typing import Any

    class MockResult:
        def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0, args: list[str] | None = None) -> None:
            self.stdout = stdout
            self.stderr = stderr
            self.returncode = returncode
            self.args = args

    def mock_run(args: list[str], **kwargs: Any) -> MockResult:
        if args and isinstance(args, list) and "pactl" in args:
            return MockResult(
                stdout="1\talsa_output.pci-0000_00_1f.3.analog-stereo.monitor\tmodule-alsa-card.c\ts16le 2ch 48000Hz\n2\talsa_input.pci-0000_00_1f.3.analog-stereo\tmodule-alsa-card.c\ts16le 2ch 48000Hz",
                args=args,
            )
        raise FileNotFoundError()

    monkeypatch.setattr(subprocess, "run", mock_run)

    sources = adapter.list_sources()

    assert len(sources) == 2

    # First source is a monitor (system audio)
    assert sources[0].source_id == "alsa_output.pci-0000_00_1f.3.analog-stereo.monitor"
    assert "System Audio" in sources[0].display_name
    assert sources[0].source_type == "system_audio"

    # Second source is a mic/line in
    assert sources[1].source_id == "alsa_input.pci-0000_00_1f.3.analog-stereo"
    assert "Microphone" in sources[1].display_name
    assert sources[1].source_type == "microphone"
