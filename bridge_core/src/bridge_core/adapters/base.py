"""Renderer adapter base interface."""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from enum import Enum
from typing import Any, Optional


class OwnershipStatus(str, Enum):
    OWNED = "owned"
    NOT_OWNED = "not_owned"
    UNKNOWN = "unknown"


class OwnershipResult:
    """Result of an ownership inspection."""

    def __init__(self, status: OwnershipStatus, evidence: Optional[str] = None):
        self.status = status
        self.evidence = evidence

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "evidence": self.evidence,
        }


class TargetDescriptor(ABC):
    """Describes a logical playback target."""

    @property
    @abstractmethod
    def target_id(self) -> str: ...

    @property
    @abstractmethod
    def renderer(self) -> str: ...

    @property
    @abstractmethod
    def target_type(self) -> str: ...

    @property
    @abstractmethod
    def display_name(self) -> str: ...

    @property
    @abstractmethod
    def members(self) -> list[str]: ...

    @property
    @abstractmethod
    def coordinator_id(self) -> str: ...

    @property
    def supported_codecs(self) -> list[str]:
        return ["mp3", "aac", "pcm_s16le"]

    @property
    def supported_sample_rates(self) -> list[int]:
        return [44100, 48000]

    @property
    def supported_channels(self) -> list[int]:
        return [1, 2]

    @property
    def max_bitrate_kbps(self) -> int | None:
        return None

    @property
    def is_preferred(self) -> bool:
        return getattr(self, "_is_preferred", False)

    @is_preferred.setter
    def is_preferred(self, value: bool) -> None:
        self._is_preferred = value

    @property
    def is_active(self) -> bool:
        return getattr(self, "_is_active", False)

    @is_active.setter
    def is_active(self, value: bool) -> None:
        self._is_active = value

    @property
    def is_available(self) -> bool:
        return getattr(self, "_is_available", True)

    @is_available.setter
    def is_available(self, value: bool) -> None:
        self._is_available = value

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API responses and comparison."""
        return {
            "target_id": self.target_id,
            "renderer": self.renderer,
            "target_type": self.target_type,
            "display_name": self.display_name,
            "members": self.members,
            "coordinator_id": self.coordinator_id,
        }


class RendererAdapter(ABC):
    """Abstract base class for renderer adapters."""

    @abstractmethod
    def id(self) -> str:
        """Return unique adapter identifier."""
        ...

    @abstractmethod
    async def list_targets(self) -> Sequence[TargetDescriptor]:
        """Discover and list available targets."""
        ...

    @abstractmethod
    async def get_topology(self) -> dict[str, Any]:
        """Get current renderer topology."""
        ...

    @abstractmethod
    async def prepare_target(self, target_id: str) -> dict[str, Any]:
        """Prepare a target for playback."""
        ...

    @abstractmethod
    async def play_stream(
        self,
        target_id: str,
        stream_url: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Start playback of a stream on a target."""
        ...

    @abstractmethod
    async def stop(self, target_id: str) -> dict[str, Any]:
        """Stop playback on a target."""
        ...

    @abstractmethod
    async def set_volume(self, target_id: str, volume: float) -> dict[str, Any]:
        """Set volume on a target (0.0 to 1.0)."""
        ...

    @abstractmethod
    async def heal(self, target_id: str) -> dict[str, Any]:
        """Attempt to heal a target's group/topology."""
        ...

    @abstractmethod
    async def inspect_ownership(self, target_id: str) -> OwnershipResult:
        """Inspect if the target is currently playing content from this bridge."""
        ...
