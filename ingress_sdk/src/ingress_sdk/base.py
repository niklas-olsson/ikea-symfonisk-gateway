"""Base interface for all ingress adapters."""

from abc import ABC, abstractmethod
from typing import Protocol

from ingress_sdk.types import (
    AdapterCapabilities,
    HealthResult,
    PairingResult,
    PrepareResult,
    SourceDescriptor,
    StartResult,
)


class FrameSink(Protocol):
    """Callback interface for receiving audio frames."""

    def on_frame(self, data: bytes, pts_ns: int, duration_ns: int) -> None:
        """Called for each audio frame."""
        ...

    def on_error(self, error: Exception) -> None:
        """Called when a source error occurs."""
        ...


class IngressAdapter(ABC):
    """Abstract base class for all ingress adapters.

    All OS-specific ingress adapters must implement this interface.
    """

    @abstractmethod
    def id(self) -> str:
        """Return a unique identifier for this adapter instance."""
        ...

    @abstractmethod
    def platform(self) -> str:
        """Return the platform name (e.g., 'linux', 'windows', 'macos')."""
        ...

    @abstractmethod
    def capabilities(self) -> AdapterCapabilities:
        """Return the capabilities of this adapter."""
        ...

    @abstractmethod
    def list_sources(self) -> list[SourceDescriptor]:
        """Enumerate available audio sources."""
        ...

    @abstractmethod
    def prepare(self, source_id: str) -> PrepareResult:
        """Prepare a source for capture (request permissions, etc.)."""
        ...

    @abstractmethod
    def start(self, source_id: str, frame_sink: FrameSink) -> StartResult:
        """Start capturing audio from the source."""
        ...

    @abstractmethod
    def stop(self, session_id: str) -> None:
        """Stop the active capture session."""
        ...

    @abstractmethod
    def probe_health(self, source_id: str) -> HealthResult:
        """Probe the health of a specific source."""
        ...

    @abstractmethod
    def start_pairing(self, timeout_seconds: int = 60) -> PairingResult:
        """Start adapter pairing mode (if supported)."""
        ...

    @abstractmethod
    def stop_pairing(self) -> PairingResult:
        """Stop adapter pairing mode."""
        ...
