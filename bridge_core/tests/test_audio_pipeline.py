import asyncio
import logging
from unittest.mock import AsyncMock, Mock, patch

import pytest
from bridge_core.core.session_manager import SessionFrameSink
from bridge_core.stream.pipeline import JitterBuffer, StreamPipeline
from ingress_sdk.protocol import AudioFrame


@pytest.mark.asyncio
async def test_jitter_buffer_ordering() -> None:
    jb = JitterBuffer(target_ms=10)

    frame1 = AudioFrame(sequence=1, pts_ns=100, duration_ns=10_000_000, format={}, audio_data=b"data1")
    frame2 = AudioFrame(sequence=2, pts_ns=200, duration_ns=10_000_000, format={}, audio_data=b"data2")
    frame0 = AudioFrame(sequence=0, pts_ns=0, duration_ns=10_000_000, format={}, audio_data=b"data0")

    await jb.push(frame1)
    await jb.push(frame2)
    await jb.push(frame0)

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

    mock_process = AsyncMock()
    mock_process.wait = AsyncMock(return_value=0)
    mock_process.terminate = Mock()

    with patch("asyncio.create_subprocess_exec", return_value=mock_process):
        await pipeline.start()
        assert pipeline._active is True
        assert pipeline._process is not None

        await pipeline.stop()
        assert pipeline._active is False
        assert pipeline._process is None


@pytest.mark.asyncio
async def test_session_frame_sink_sequences_do_not_trigger_late_drops(caplog: pytest.LogCaptureFixture) -> None:
    class PipelineStub:
        def __init__(self) -> None:
            self.jitter_buffer = JitterBuffer(target_ms=1)

        async def push_frame(self, frame: AudioFrame) -> None:
            await self.jitter_buffer.push(frame)

    pipeline = PipelineStub()
    sink = SessionFrameSink(pipeline)  # type: ignore[arg-type]
    caplog.set_level(logging.WARNING)
    sink.start()

    sink.on_frame(b"frame1", 0, 1_000_000)
    sink.on_frame(b"frame2", 1_000_000, 1_000_000)
    sink.on_frame(b"frame3", 2_000_000, 1_000_000)

    await asyncio.wait_for(sink._queue.join(), timeout=1.0)
    sink.stop()

    frames = [await pipeline.jitter_buffer.pop(), await pipeline.jitter_buffer.pop(), await pipeline.jitter_buffer.pop()]
    assert [frame.sequence for frame in frames if frame is not None] == [0, 1, 2]
    assert "Dropping late frame" not in caplog.text
