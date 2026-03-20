import asyncio

import numpy as np
import pytest
from adapter_synthetic import FRAME_SIZE, SAMPLE_RATE, SAMPLES_PER_FRAME, SyntheticAdapter
from ingress_sdk.types import SourceType, SyntheticMode


class MockFrameSink:
    def __init__(self) -> None:
        self.frames: list[bytes] = []
        self.pts: list[int] = []
        self.durations: list[int] = []

    def on_frame(self, data: bytes, pts_ns: int, duration_ns: int) -> None:
        self.frames.append(data)
        self.pts.append(pts_ns)
        self.durations.append(duration_ns)


def test_adapter_instantiation() -> None:
    adapter = SyntheticAdapter()
    assert adapter.platform() == "any"
    assert "synthetic-" in adapter.id()


def test_list_sources() -> None:
    adapter = SyntheticAdapter()
    sources = adapter.list_sources()
    assert len(sources) == 1
    assert sources[0].source_type == SourceType.SYNTHETIC_TEST_SOURCE
    assert "Synthetic" in sources[0].display_name


def test_prepare_and_health() -> None:
    adapter = SyntheticAdapter()
    sources = adapter.list_sources()
    source_id = sources[0].source_id

    # Initial health
    health = adapter.probe_health(source_id)
    assert health.healthy is True
    assert health.source_state == "idle"
    assert health.signal_present is False

    # Prepare
    prepare_result = adapter.prepare(source_id)
    assert prepare_result.success is True
    assert prepare_result.source_id == source_id

    # Health after prepare
    health = adapter.probe_health(source_id)
    assert health.healthy is True
    assert health.source_state == "active"


@pytest.mark.asyncio
async def test_sine_wave_generation() -> None:
    adapter = SyntheticAdapter()
    sink: MockFrameSink = MockFrameSink()
    adapter.set_mode(SyntheticMode.SINE_WAVE)

    sources = adapter.list_sources()
    source_id = sources[0].source_id

    adapter.prepare(source_id)
    result = adapter.start(source_id, sink)

    # Check health while running
    health = adapter.probe_health(source_id)
    assert health.healthy is True
    assert health.source_state == "active"
    assert health.signal_present is True

    # Wait for a few frames
    await asyncio.sleep(0.1)
    adapter.stop(result.session_id)

    # Health after stop
    health = adapter.probe_health(source_id)
    assert health.source_state == "idle"
    assert health.signal_present is False

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
async def test_silence_generation() -> None:
    adapter = SyntheticAdapter()
    sink: MockFrameSink = MockFrameSink()
    adapter.set_mode(SyntheticMode.SILENCE)

    sources = adapter.list_sources()
    source_id = sources[0].source_id
    adapter.prepare(source_id)
    result = adapter.start(source_id, sink)

    await asyncio.sleep(0.1)
    adapter.stop(result.session_id)

    assert len(sink.frames) >= 5
    for frame in sink.frames:
        assert len(frame) == FRAME_SIZE
        assert frame == b"\x00" * FRAME_SIZE


@pytest.mark.asyncio
async def test_pink_noise_generation() -> None:
    adapter = SyntheticAdapter()
    sink: MockFrameSink = MockFrameSink()
    adapter.set_mode(SyntheticMode.PINK_NOISE)

    sources = adapter.list_sources()
    source_id = sources[0].source_id
    adapter.prepare(source_id)
    result = adapter.start(source_id, sink)

    await asyncio.sleep(0.1)
    adapter.stop(result.session_id)

    assert len(sink.frames) >= 5
    for frame in sink.frames:
        assert len(frame) == FRAME_SIZE
        assert frame != b"\x00" * FRAME_SIZE


def test_format_verification() -> None:
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
