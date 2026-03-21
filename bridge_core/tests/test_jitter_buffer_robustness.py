import pytest
import asyncio
from bridge_core.stream.pipeline import JitterBuffer
from ingress_sdk.protocol import AudioFrame

@pytest.mark.asyncio
async def test_jitter_buffer_duplicate_sequence() -> None:
    jb = JitterBuffer(target_ms=10)

    # Push two frames with the same sequence number
    frame1 = AudioFrame(sequence=1, pts_ns=100, duration_ns=10_000_000, format={}, audio_data=b"data1")
    frame2 = AudioFrame(sequence=1, pts_ns=200, duration_ns=10_000_000, format={}, audio_data=b"data2")

    await jb.push(frame1)
    # This should no longer fail with TypeError
    await jb.push(frame2)

    # We should be able to pop both
    res1 = await jb.pop()
    assert res1 is not None
    assert res1.sequence == 1
    assert res1.audio_data == b"data1" # FIFO for same sequence because of counter

    res2 = await jb.pop()
    assert res2 is not None
    assert res2.sequence == 1
    assert res2.audio_data == b"data2"

@pytest.mark.asyncio
async def test_jitter_buffer_high_rate_repeated() -> None:
    jb = JitterBuffer(target_ms=0) # Immediate ready

    num_frames = 100
    for i in range(num_frames):
        frame = AudioFrame(sequence=10, pts_ns=i*10, duration_ns=1_000_000, format={}, audio_data=f"data{i}".encode())
        await jb.push(frame)

    assert jb.size_ms == num_frames # 1ms duration per frame

    for i in range(num_frames):
        res = await jb.pop()
        assert res is not None
        assert res.sequence == 10
        assert res.audio_data == f"data{i}".encode()

@pytest.mark.asyncio
async def test_jitter_buffer_out_of_order_duplicates() -> None:
    jb = JitterBuffer(target_ms=10)

    frames = [
        AudioFrame(sequence=2, pts_ns=200, duration_ns=10_000_000, format={}, audio_data=b"2a"),
        AudioFrame(sequence=1, pts_ns=100, duration_ns=10_000_000, format={}, audio_data=b"1a"),
        AudioFrame(sequence=2, pts_ns=210, duration_ns=10_000_000, format={}, audio_data=b"2b"),
        AudioFrame(sequence=1, pts_ns=110, duration_ns=10_000_000, format={}, audio_data=b"1b"),
    ]

    for f in frames:
        await jb.push(f)

    # Expected order: 1a, 1b, 2a, 2b
    expected_data = [b"1a", b"1b", b"2a", b"2b"]
    for expected in expected_data:
        res = await jb.pop()
        assert res is not None
        assert res.audio_data == expected
