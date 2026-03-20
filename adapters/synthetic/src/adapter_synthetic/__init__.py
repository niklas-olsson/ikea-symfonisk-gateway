"""Synthetic test source adapter.

Generates sine waves, pink noise, and silence for testing.
"""

import asyncio
from uuid import uuid4

import numpy as np
from ingress_sdk.base import FrameSink, IngressAdapter
from ingress_sdk.types import (
    AdapterCapabilities,
    HealthResult,
    PrepareResult,
    SourceCapabilities,
    SourceDescriptor,
    SourceType,
    StartResult,
    SyntheticMode,
)

SAMPLE_RATE = 48000
CHANNELS = 2
BYTES_PER_SAMPLE = 2
SAMPLES_PER_FRAME = 480
FRAME_SIZE = SAMPLES_PER_FRAME * CHANNELS * BYTES_PER_SAMPLE


class SyntheticAdapter(IngressAdapter):
    """Test adapter that generates synthetic audio."""

    def __init__(self) -> None:
        self._session_id: str | None = None
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._frame_sink: FrameSink | None = None
        self._mode: SyntheticMode = SyntheticMode.SINE_WAVE
        self._sample_index = 0
        self._source_id = f"synthetic:{uuid4().hex[:8]}"

        # Pink noise state (Voss-McCartney algorithm)
        # Using 16 rows for a good 1/f approximation
        self._pink_rows = np.random.randn(16)
        self._pink_running_sum = np.sum(self._pink_rows)
        self._pink_key = 0

    def id(self) -> str:
        return f"synthetic-{uuid4().hex[:8]}"

    def platform(self) -> str:
        return "any"

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            supports_synthetic_test_source=True,
            supports_sample_rates=[44100, 48000],
            supports_channels=[1, 2],
            supports_hotplug_events=False,
            supports_pairing=False,
        )

    def list_sources(self) -> list[SourceDescriptor]:
        return [
            SourceDescriptor(
                source_id=self._source_id,
                source_type=SourceType.SYNTHETIC_TEST_SOURCE,
                display_name="Synthetic Test Source",
                platform="any",
                capabilities=SourceCapabilities(
                    sample_rates=[44100, 48000],
                    channels=[1, 2],
                    bit_depths=[16],
                ),
            )
        ]

    def prepare(self, source_id: str) -> PrepareResult:
        return PrepareResult(success=True, source_id=source_id)

    def start(self, source_id: str, frame_sink: FrameSink) -> StartResult:
        self._session_id = f"sess_{uuid4().hex[:12]}"
        self._frame_sink = frame_sink
        self._running = True
        self._sample_index = 0
        self._task = asyncio.create_task(self._generate_loop())
        return StartResult(success=True, session_id=self._session_id)

    def stop(self, session_id: str) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        self._session_id = None
        self._frame_sink = None

    def probe_health(self, source_id: str) -> HealthResult:
        return HealthResult(
            healthy=True,
            source_state="active",
            signal_present=True,
            dropped_frames=0,
            last_error=None,
        )

    async def _generate_loop(self) -> None:
        start_time = asyncio.get_event_loop().time()
        frames_sent = 0

        while self._running:
            pts_ns = int(self._sample_index * 1_000_000_000 / SAMPLE_RATE)
            duration_ns = int(SAMPLES_PER_FRAME * 1_000_000_000 / SAMPLE_RATE)

            frame = self._generate_frame()

            if self._frame_sink:
                self._frame_sink.on_frame(frame, pts_ns, duration_ns)

            frames_sent += 1
            # Calculate when the next frame should be sent relative to the start
            next_frame_time = start_time + (frames_sent * SAMPLES_PER_FRAME / SAMPLE_RATE)
            sleep_time = next_frame_time - asyncio.get_event_loop().time()
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)
            else:
                # We are behind, don't sleep but yield
                await asyncio.sleep(0)

    def _generate_frame(self) -> bytes:
        if self._mode == SyntheticMode.SILENCE:
            self._sample_index += SAMPLES_PER_FRAME
            return b"\x00" * FRAME_SIZE

        if self._mode == SyntheticMode.SINE_WAVE:
            # Generate 440Hz sine wave
            t = (self._sample_index + np.arange(SAMPLES_PER_FRAME)) / SAMPLE_RATE
            samples = 0.5 * np.sin(2 * np.pi * 440 * t)
        elif self._mode == SyntheticMode.PINK_NOISE:
            samples = np.zeros(SAMPLES_PER_FRAME)
            for j in range(SAMPLES_PER_FRAME):
                self._pink_key = (self._pink_key + 1) & 0xFFFF
                if self._pink_key == 0:
                    self._pink_key = 1

                # Determine which row to update using the index of the lowest set bit
                i = (self._pink_key & -self._pink_key).bit_length() - 1
                # Ensure i is within bounds of our 16 rows
                i = min(i, 15)

                old_val = self._pink_rows[i]
                self._pink_rows[i] = np.random.randn()
                self._pink_running_sum += self._pink_rows[i] - old_val
                samples[j] = self._pink_running_sum / 16.0

            # Scale to avoid excessive clipping (Voss algorithm std dev is ~0.25)
            samples = samples * 0.5
        else:
            samples = np.zeros(SAMPLES_PER_FRAME)

        self._sample_index += SAMPLES_PER_FRAME

        # Convert to 16-bit PCM (little-endian)
        samples_int16 = (samples * 32767).clip(-32768, 32767).astype("<i2")

        # Create stereo by duplicating mono to both channels [L, R, L, R, ...]
        stereo_samples = np.empty(SAMPLES_PER_FRAME * 2, dtype="<i2")
        stereo_samples[0::2] = samples_int16
        stereo_samples[1::2] = samples_int16

        return stereo_samples.tobytes()

    def set_mode(self, mode: SyntheticMode) -> None:
        """Set the synthetic generation mode."""
        self._mode = mode
