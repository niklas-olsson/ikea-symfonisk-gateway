"""Stream publisher subsystem."""

from dataclasses import dataclass


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

from bridge_core.stream.publisher import StreamPublisher  # noqa: E402

__all__ = ["StreamProfile", "STREAM_PROFILES", "StreamPublisher"]
