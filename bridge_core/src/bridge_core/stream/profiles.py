"""Stream profiles and data structures."""

from dataclasses import dataclass
from typing import Literal

DeliveryProfile = Literal["stable", "experimental"]


@dataclass
class StreamProfile:
    """Defines an encoding profile for stream publication."""

    id: str
    codec: str
    sample_rate: int
    channels: int
    bitrate_kbps: int


@dataclass(frozen=True)
class DeliveryProfileDefaults:
    """Default values for a delivery profile."""

    delivery_profile: DeliveryProfile
    keepalive_enabled: bool
    keepalive_idle_threshold_ms: int
    keepalive_frame_duration_ms: int
    live_jitter_target_ms: int
    transport_heartbeat_window_ms: int
    primary_client_queue_bytes: int
    primary_client_overflow_grace_ms: int
    primary_client_max_backlog_ms: int
    primary_detach_grace_ms: int
    primary_health_require_yield_progress: bool


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

DELIVERY_PROFILE_DEFAULTS: dict[DeliveryProfile, DeliveryProfileDefaults] = {
    "stable": DeliveryProfileDefaults(
        delivery_profile="stable",
        keepalive_enabled=False,
        keepalive_idle_threshold_ms=200,
        keepalive_frame_duration_ms=20,
        live_jitter_target_ms=250,
        transport_heartbeat_window_ms=1000,
        primary_client_queue_bytes=262144,
        primary_client_overflow_grace_ms=1500,
        primary_client_max_backlog_ms=1500,
        primary_detach_grace_ms=10000,
        primary_health_require_yield_progress=False,
    ),
    "experimental": DeliveryProfileDefaults(
        delivery_profile="experimental",
        keepalive_enabled=True,
        keepalive_idle_threshold_ms=100,
        keepalive_frame_duration_ms=10,
        live_jitter_target_ms=60,
        transport_heartbeat_window_ms=750,
        primary_client_queue_bytes=131072,
        primary_client_overflow_grace_ms=750,
        primary_client_max_backlog_ms=750,
        primary_detach_grace_ms=5000,
        primary_health_require_yield_progress=True,
    ),
}

# General constants shared across profiles
AUDIO_SOURCE_OUTAGE_GRACE_MS_DEFAULT = 5000
AUDIO_AUX_CLIENT_QUEUE_BYTES_DEFAULT = 131072
AUDIO_AUX_CLIENT_OVERFLOW_GRACE_MS_DEFAULT = 750
AUDIO_AUX_CLIENT_MAX_BACKLOG_MS_DEFAULT = 1500
