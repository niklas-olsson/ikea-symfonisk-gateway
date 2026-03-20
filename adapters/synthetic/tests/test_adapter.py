import asyncio

import numpy as np
import pytest
from adapter_synthetic import FRAME_SIZE, SAMPLE_RATE, SAMPLES_PER_FRAME, SyntheticAdapter
from ingress_sdk.types import SourceType, SyntheticMode


class MockFrameSink:
    def __init__(self):
        self.frames = []
        self.pts = []
        self.durations = []

    def on_frame(self, data: bytes, pts_ns: int, duration_ns: int) -> None:
        self.frames.append(data)
        self.pts.append(pts_ns)
        self.durations.append(duration_ns)


def test_adapter_instantiation():
    adapter = SyntheticAdapter()
    assert adapter.platform() == "any"
    assert "synthetic-" in adapter.id()


def test_list_sources():
    adapter = SyntheticAdapter()
    sources = adapter.list_sources()
    assert len(sources) == 1
    assert sources[0].source_type == SourceType.SYNTHETIC_TEST_SOURCE
    assert "Synthetic" in sources[0].display_name


@pytest.mark.asyncio
async def test_sine_wave_generation():
    adapter = SyntheticAdapter()
    sink = MockFrameSink()
    adapter.set_mode(SyntheticMode.SINE_WAVE)

    sources = adapter.list_sources()
    adapter.start(sources[0].source_id, sink)

    # Wait for a few frames
    await asyncio.sleep(0.1)
    adapter.stop(None)

    assert len(sink.frames) >= 5
    for frame in sink.frames:
        assert len(frame) == FRAME_SIZE
        # Ensure it's not silence
        assert frame != b"\x00" * FRAME_SIZE

    # Check timing
    for i in range(1, len(sink.pts)):
        assert sink.pts[i] > sink.pts[i - 1]
        assert sink.durations[i] == int(SAMPLES_PER_FRAME * 1_000_000_000 / SAMPLE_RATE)


@pytest.mark.asyncio
async def test_silence_generation():
    adapter = SyntheticAdapter()
    sink = MockFrameSink()
    adapter.set_mode(SyntheticMode.SILENCE)

    sources = adapter.list_sources()
    adapter.start(sources[0].source_id, sink)

    await asyncio.sleep(0.1)
    adapter.stop(None)

    assert len(sink.frames) >= 5
    for frame in sink.frames:
        assert len(frame) == FRAME_SIZE
        assert frame == b"\x00" * FRAME_SIZE


@pytest.mark.asyncio
async def test_pink_noise_generation():
    adapter = SyntheticAdapter()
    sink = MockFrameSink()
    adapter.set_mode(SyntheticMode.PINK_NOISE)

    sources = adapter.list_sources()
    adapter.start(sources[0].source_id, sink)

    await asyncio.sleep(0.1)
    adapter.stop(None)

    assert len(sink.frames) >= 5
    for frame in sink.frames:
        assert len(frame) == FRAME_SIZE
        assert frame != b"\x00" * FRAME_SIZE


def test_format_verification():
    adapter = SyntheticAdapter()
    adapter.set_mode(SyntheticMode.SINE_WAVE)
    frame_data = adapter._generate_frame()

    assert len(frame_data) == FRAME_SIZE

    # Convert back to numpy to check properties
    samples = np.frombuffer(frame_data, dtype="<i2")
    assert len(samples) == SAMPLES_PER_FRAME * 2

    # Check stereo (L and R should be identical in our mono-to-stereo implementation)
    left = samples[0::2]
    right = samples[1::2]
    np.testing.assert_array_equal(left, right)
