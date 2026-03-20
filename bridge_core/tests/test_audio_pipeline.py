
import pytest
from bridge_core.stream.pipeline import JitterBuffer, StreamPipeline
from ingress_sdk.protocol import AudioFrame


@pytest.mark.asyncio
async def test_jitter_buffer_ordering() -> None:
    jb = JitterBuffer(target_ms=10)

    # Push frames out of order
    frame1 = AudioFrame(sequence=1, pts_ns=100, duration_ns=10_000_000, format={}, audio_data=b"data1")
    frame2 = AudioFrame(sequence=2, pts_ns=200, duration_ns=10_000_000, format={}, audio_data=b"data2")
    frame0 = AudioFrame(sequence=0, pts_ns=0, duration_ns=10_000_000, format={}, audio_data=b"data0")

    await jb.push(frame1)
    await jb.push(frame2)
    await jb.push(frame0)

    # Pop frames and check order
    res0 = await jb.pop()
    assert res0 is not None
    assert res0.sequence == 0

    res1 = await jb.pop()
    assert res1 is not None
    assert res1.sequence == 1

    res2 = await jb.pop()
    assert res2 is not None
    assert res2.sequence == 2


@pytest.mark.asyncio
async def test_stream_pipeline_lifecycle() -> None:
    session_id = "test_sess"
    profile_id = "mp3_48k_stereo_320"
    pipeline = StreamPipeline(session_id, profile_id)

    await pipeline.start()
    assert pipeline._active is True
    assert pipeline._process is not None

    await pipeline.stop()
    assert pipeline._active is False
    assert pipeline._process is None
