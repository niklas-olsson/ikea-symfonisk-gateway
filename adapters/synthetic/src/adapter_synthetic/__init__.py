"""Synthetic test source adapter.

Generates sine waves, pink noise, and silence for testing.
"""

import asyncio
import math
import struct
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
        self._phase = 0.0
        self._source_id = f"synthetic:{uuid4().hex[:8]}"

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
        while self._running:
            frame = self._generate_frame()
            pts_ns = int(self._phase * 1_000_000_000 / SAMPLE_RATE)
            duration_ns = int(SAMPLES_PER_FRAME * 1_000_000_000 / SAMPLE_RATE)

            if self._frame_sink:
                self._frame_sink.on_frame(frame, pts_ns, duration_ns)

            await asyncio.sleep(SAMPLES_PER_FRAME / SAMPLE_RATE)

    def _generate_frame(self) -> bytes:
        if self._mode == SyntheticMode.SILENCE:
            return b"\x00" * FRAME_SIZE

        samples = []
        for i in range(SAMPLES_PER_FRAME):
            t = (self._phase + i) / SAMPLE_RATE
            if self._mode == SyntheticMode.SINE_WAVE:
                sample = 0.5 * math.sin(2 * math.pi * 440 * t)
            elif self._mode == SyntheticMode.PINK_NOISE:
                sample = np.random.randn() * 0.3
            else:
                sample = 0.0

            sample_int = int(sample * 32767)
            sample_int = max(-32768, min(32767, sample_int))
            samples.extend([sample_int, sample_int])

        self._phase += SAMPLES_PER_FRAME
        num_samples = FRAME_SIZE // BYTES_PER_SAMPLE
        return struct.pack("<" + "h" * num_samples, *samples)

    def set_mode(self, mode: SyntheticMode) -> None:
        self._mode = mode
