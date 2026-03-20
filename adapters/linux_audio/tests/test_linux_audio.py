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
    class MockResult:
        stdout = "1\talsa_output.pci-0000_00_1f.3.analog-stereo.monitor\tmodule-alsa-card.c\ts16le 2ch 48000Hz\n2\talsa_input.pci-0000_00_1f.3.analog-stereo\tmodule-alsa-card.c\ts16le 2ch 48000Hz"

    def mock_run(*args: object, **kwargs: object) -> MockResult:
        if args and isinstance(args[0], list) and "pactl" in args[0]:
            return MockResult()
        raise FileNotFoundError()

    import subprocess
    monkeypatch.setattr(subprocess, "run", mock_run)

    sources = adapter.list_sources()

    assert len(sources) == 2

    # First source is a monitor (system audio)
    assert sources[0].source_id == "alsa_output.pci-0000_00_1f.3.analog-stereo.monitor"
    assert "System Audio" in sources[0].display_name
    assert sources[0].source_type == "system_audio"

    # Second source is a mic/line in
    assert sources[1].source_id == "alsa_input.pci-0000_00_1f.3.analog-stereo"
    assert "Microphone/Line-In" in sources[1].display_name
    assert sources[1].source_type == "microphone"
