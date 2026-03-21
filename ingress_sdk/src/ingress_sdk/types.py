"""Type definitions for ingress adapters."""

from enum import Enum
from typing import Any

from pydantic import BaseModel


class SourceType(str, Enum):
    SYSTEM_AUDIO = "system_audio"
    BLUETOOTH_AUDIO = "bluetooth_audio"
    LINE_IN = "line_in"
    MICROPHONE = "microphone"
    FILE_REPLAY = "file_replay"
    SYNTHETIC_TEST_SOURCE = "synthetic_test_source"


class SourceCapabilities(BaseModel):
    sample_rates: list[int] = [44100, 48000]
    channels: list[int] = [1, 2]
    bit_depths: list[int] = [16]


class SourceDescriptor(BaseModel):
    source_id: str
    source_type: SourceType
    display_name: str
    platform: str
    capabilities: SourceCapabilities
    adapter_id: str | None = None
    local_source_id: str | None = None
    metadata: dict[str, Any] = {}


class AdapterCapabilities(BaseModel):
    supports_system_audio: bool = False
    supports_bluetooth_audio: bool = False
    supports_line_in: bool = False
    supports_microphone: bool = False
    supports_file_replay: bool = False
    supports_synthetic_test_source: bool = False
    supports_sample_rates: list[int] = [44100, 48000]
    supports_channels: list[int] = [1, 2]
    supports_hotplug_events: bool = False
    supports_pairing: bool = False


class PrepareResult(BaseModel):
    success: bool
    message: str = ""
    error: str | None = None
    code: str | None = None
    source_id: str


class StartResult(BaseModel):
    success: bool
    session_id: str = ""
    message: str = ""
    code: str | None = None
    backend: str | None = None


class PairingResult(BaseModel):
    success: bool
    message: str = ""
    error: str | None = None


class HealthResult(BaseModel):
    healthy: bool
    source_state: str
    signal_present: bool
    dropped_frames: int = 0
    last_error: str | None = None


class SyntheticMode(str, Enum):
    SINE_WAVE = "sine_wave"
    PINK_NOISE = "pink_noise"
    SILENCE = "silence"
