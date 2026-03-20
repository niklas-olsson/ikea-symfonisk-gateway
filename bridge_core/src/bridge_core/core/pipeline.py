"""Audio pipeline manager - handles resampling, encoding, and buffering."""

from dataclasses import dataclass
from typing import Any


@dataclass
class PipelineStats:
    """Statistics for a pipeline."""

    frames_processed: int = 0
    frames_dropped: int = 0
    buffer_fill_ms: float = 0.0
    encode_latency_ms: float = 0.0


class Pipeline:
    """Represents an active audio pipeline for a session."""

    def __init__(self, session_id: str, profile: str):
        self.session_id = session_id
        self.profile = profile
        self.active = False
        self.stats = PipelineStats()

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "profile": self.profile,
            "active": self.active,
            "stats": {
                "frames_processed": self.stats.frames_processed,
                "frames_dropped": self.stats.frames_dropped,
                "buffer_fill_ms": self.stats.buffer_fill_ms,
                "encode_latency_ms": self.stats.encode_latency_ms,
            },
        }


class PipelineManager:
    """Manages audio pipelines for active sessions."""

    def __init__(self) -> None:
        self._pipelines: dict[str, Pipeline] = {}

    def create(self, session_id: str, profile: str) -> Pipeline:
        """Create a new pipeline."""
        pipeline = Pipeline(session_id, profile)
        self._pipelines[session_id] = pipeline
        return pipeline

    def get(self, session_id: str) -> Pipeline | None:
        """Get a pipeline by session ID."""
        return self._pipelines.get(session_id)

    def start(self, session_id: str) -> bool:
        """Start a pipeline."""
        pipeline = self._pipelines.get(session_id)
        if pipeline:
            pipeline.active = True
            return True
        return False

    def stop(self, session_id: str) -> bool:
        """Stop a pipeline."""
        pipeline = self._pipelines.get(session_id)
        if pipeline:
            pipeline.active = False
            return True
        return False

    def delete(self, session_id: str) -> bool:
        """Delete a pipeline."""
        return self._pipelines.pop(session_id, None) is not None

    def list(self) -> list[Pipeline]:
        """List all pipelines."""
        return list(self._pipelines.values())
