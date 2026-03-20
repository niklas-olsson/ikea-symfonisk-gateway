"""Renderer adapter base interface."""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any


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
