"""Test cases for the synthetic test adapter."""

import asyncio
import math
import struct
from typing import List

import pytest
from ingress_sdk.base import FrameSink
from ingress_sdk.types import SyntheticMode

from adapter_synthetic import (
    BYTES_PER_SAMPLE,
    CHANNELS,
    FRAME_SIZE,
    SAMPLE_RATE,
    SAMPLES_PER_FRAME,
    SyntheticAdapter,
)


class MockFrameSink(FrameSink):
    def __init__(self):
        self.frames = []

    def on_frame(self, data: bytes, pts_ns: int, duration_ns: int) -> None:
        self.frames.append((data, pts_ns, duration_ns))


@pytest.fixture
def adapter():
    return SyntheticAdapter()


@pytest.mark.asyncio
async def test_adapter_capabilities(adapter):
    caps = adapter.capabilities()
    assert caps.supports_synthetic_test_source is True
    assert 48000 in caps.supports_sample_rates
    assert 2 in caps.supports_channels


@pytest.mark.asyncio
async def test_adapter_list_sources(adapter):
    sources = adapter.list_sources()
    assert len(sources) == 1
    source = sources[0]
    assert source.display_name == "Synthetic Test Source"
    assert 48000 in source.capabilities.sample_rates
    assert 2 in source.capabilities.channels
    assert 16 in source.capabilities.bit_depths


@pytest.mark.asyncio
async def test_adapter_prepare_and_start(adapter):
    sources = adapter.list_sources()
    source_id = sources[0].source_id

    prep = adapter.prepare(source_id)
    assert prep.success is True

    sink = MockFrameSink()
    start = adapter.start(source_id, sink)
    assert start.success is True
    assert start.session_id is not None

    await asyncio.sleep(0.05)  # Let it generate a few frames

    adapter.stop(start.session_id)

    assert len(sink.frames) > 0

    # Check frame format
    data, pts_ns, duration_ns = sink.frames[0]
    assert len(data) == FRAME_SIZE
    assert duration_ns == int(SAMPLES_PER_FRAME * 1_000_000_000 / SAMPLE_RATE)


@pytest.mark.asyncio
async def test_silence_mode(adapter):
    adapter.set_mode(SyntheticMode.SILENCE)

    frame = adapter._generate_frame()
    assert len(frame) == FRAME_SIZE
    assert frame == b"\x00" * FRAME_SIZE


@pytest.mark.asyncio
async def test_sine_wave_mode(adapter):
    adapter.set_mode(SyntheticMode.SINE_WAVE)

    frame1 = adapter._generate_frame()
    assert len(frame1) == FRAME_SIZE
    assert frame1 != b"\x00" * FRAME_SIZE

    # Check if sine wave format looks like PCM
    samples = struct.unpack("<" + "h" * (FRAME_SIZE // BYTES_PER_SAMPLE), frame1)
    # Stereo interleaving, first sample left
    assert samples[0] == samples[1] # Channels should have same value


@pytest.mark.asyncio
async def test_pink_noise_mode(adapter):
    adapter.set_mode(SyntheticMode.PINK_NOISE)

    frame = adapter._generate_frame()
    assert len(frame) == FRAME_SIZE
    assert frame != b"\x00" * FRAME_SIZE
