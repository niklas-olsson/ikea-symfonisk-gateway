"""Stream publisher subsystem."""

from dataclasses import dataclass
from typing import Any


@dataclass
class StreamProfile:
    """Defines an encoding profile for stream publication."""

    id: str
    codec: str
    sample_rate: int
    channels: int
    bitrate_kbps: int


STREAM_PROFILES: dict[str, StreamProfile] = {
    "mp3_48k_stereo_320": StreamProfile(
        id="mp3_48k_stereo_320",
        codec="mp3",
        sample_rate=48000,
        channels=2,
        bitrate_kbps=320,
    ),
    "aac_48k_stereo_256": StreamProfile(
        id="aac_48k_stereo_256",
        codec="aac",
        sample_rate=48000,
        channels=2,
        bitrate_kbps=256,
    ),
    "pcm_wav_48k_stereo_16": StreamProfile(
        id="pcm_wav_48k_stereo_16",
        codec="pcm_s16le",
        sample_rate=48000,
        channels=2,
        bitrate_kbps=0,
    ),
}


class StreamPublisher:
    """Publishes live audio streams over HTTP."""

    def __init__(self, host: str = "0.0.0.0", port: int = 8080):
        self.host = host
        self.port = port
        self._streams: dict[str, dict[str, Any]] = {}
        self._active = False

    def get_stream_url(self, session_id: str, profile_id: str = "mp3_48k_stereo_320") -> str:
        """Get the stream URL for a session."""
        extension = "mp3"
        if profile_id == "aac_48k_stereo_256":
            extension = "aac"
        elif profile_id == "pcm_wav_48k_stereo_16":
            extension = "wav"
        return f"http://{self.host}:{self.port}/streams/{session_id}/live.{extension}"

    def publish(self, session_id: str, profile_id: str) -> str:
        """Start publishing a stream for a session."""
        self._streams[session_id] = {
            "session_id": session_id,
            "profile_id": profile_id,
            "active": True,
        }
        return self.get_stream_url(session_id, profile_id)

    def unpublish(self, session_id: str) -> bool:
        """Stop publishing a stream."""
        if session_id in self._streams:
            self._streams[session_id]["active"] = False
            del self._streams[session_id]
            return True
        return False

    def get_streams(self) -> list[dict[str, Any]]:
        """List all active streams."""
        return list(self._streams.values())

    def start(self) -> None:
        """Start the stream server."""
        self._active = True

    def stop(self) -> None:
        """Stop the stream server."""
        self._active = False
