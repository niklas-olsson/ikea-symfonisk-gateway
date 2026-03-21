"""Windows loopback backend models."""

from typing import Literal

from pydantic import BaseModel


class BackendProbeResult(BaseModel):
    """Probe result for a Windows loopback backend."""

    backend: str
    available: bool
    state: Literal["available", "missing_dependency", "unsupported_host", "probe_failed", "initialization_error"]
    loopback_supported: bool
    default_output_detected: bool
    code: str | None = None
    message: str | None = None
