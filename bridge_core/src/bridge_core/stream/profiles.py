"""Stream profiles and data structures."""

from dataclasses import dataclass
from typing import Any, Literal

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

STREAM_PROFILE_ORDER = ["pcm_wav_48k_stereo_16", "aac_48k_stereo_256", "mp3_48k_stereo_320"]


def negotiate_stream_profile(
    source_caps: Any,
    target_caps: Any,
    last_known_good: str | None = None,
) -> str:
    """Negotiate the highest safe startup stream profile."""
    # 1. Use last known good if it's still viable
    if last_known_good and last_known_good in STREAM_PROFILES:
        if is_profile_supported(last_known_good, source_caps, target_caps):
            return last_known_good

    # 2. Try profiles in descending quality order
    for profile_id in STREAM_PROFILE_ORDER:
        if is_profile_supported(profile_id, source_caps, target_caps):
            return profile_id

    # 3. Fallback to a safe default if all else fails
    return "mp3_48k_stereo_320"


def is_profile_supported(profile_id: str, source_caps: Any, target_caps: Any) -> bool:
    """Check if a profile is supported by both source and target capabilities."""
    profile = STREAM_PROFILES.get(profile_id)
    if not profile:
        return False

    # Check source support with robustness for Mocks or missing attributes
    source_codecs = getattr(source_caps, "codecs", None)
    if not isinstance(source_codecs, (list, tuple)):
        source_codecs = ["pcm_s16le", "mp3", "aac"]
    if profile.codec not in source_codecs:
        return False

    source_sample_rates = getattr(source_caps, "sample_rates", None)
    if not isinstance(source_sample_rates, (list, tuple)):
        source_sample_rates = [44100, 48000]
    if profile.sample_rate not in source_sample_rates:
        return False

    source_channels = getattr(source_caps, "channels", None)
    if not isinstance(source_channels, (list, tuple)):
        source_channels = [1, 2]
    if profile.channels not in source_channels:
        return False

    # Check target support
    target_codecs = getattr(target_caps, "supported_codecs", None)
    if not isinstance(target_codecs, (list, tuple)):
        target_codecs = ["mp3", "aac", "pcm_s16le"]
    if profile.codec not in target_codecs:
        return False

    target_sample_rates = getattr(target_caps, "supported_sample_rates", None)
    if not isinstance(target_sample_rates, (list, tuple)):
        target_sample_rates = [44100, 48000]
    if profile.sample_rate not in target_sample_rates:
        return False

    target_channels = getattr(target_caps, "supported_channels", None)
    if not isinstance(target_channels, (list, tuple)):
        target_channels = [1, 2]
    if profile.channels not in target_channels:
        return False

    max_bitrate = getattr(target_caps, "max_bitrate_kbps", None)
    if isinstance(max_bitrate, (int, float)) and profile.bitrate_kbps > max_bitrate:
        return False

    return True


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
