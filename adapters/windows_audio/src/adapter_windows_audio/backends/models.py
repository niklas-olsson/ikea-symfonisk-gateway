"""Windows loopback backend models."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class BackendProbeResult(BaseModel):
    """Probe result for a Windows loopback backend."""

    backend: str
    available: bool
    state: Literal["available", "missing_dependency", "unsupported_host", "probe_failed", "initialization_error"]
    loopback_supported: bool
    default_output_detected: bool
    code: str | None = None
    message: str | None = None


class BackendStartupDiagnostics(BaseModel):
    """Runtime diagnostics for a Windows loopback capture session."""

    selected_host_api: dict[str, Any] | None = None
    default_render_device: dict[str, Any] | None = None
    loopback_device: dict[str, Any] | None = None
    sample_rate: int = 48000
    actual_sample_rate: int | None = None
    attempted_rates: list[int] = Field(default_factory=list)
    channels: int = 2
    sample_format: str = "paInt16"
    frames_per_buffer: int = 480
    stream_opened: bool = False
    stream_started: bool = False
    callback_started: bool = False
    first_callback_at: float | None = None
    callback_count: int = 0
    non_empty_buffer_count: int = 0
    samples_received: int = 0
    raw_bytes_received: int = 0
    frames_emitted: int = 0
    last_status_flags: str | None = None
    last_callback_error: str | None = None
    startup_substate: str = "idle"
    start_viability: dict[str, bool] = Field(
        default_factory=lambda: {
            "stream_opened": False,
            "stream_started": False,
            "callback_registered": False,
        }
    )
