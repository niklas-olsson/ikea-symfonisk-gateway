import asyncio
import logging
import wave
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest
from bridge_core.core.session_manager import SessionFrameSink
from bridge_core.stream.pipeline import JitterBuffer, StreamPipeline
from ingress_sdk.protocol import AudioFrame


class FakeStdin:
    def __init__(self) -> None:
        self.writes: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    async def drain(self) -> None:
        return None


class FakeStdout:
    def __init__(self, chunks: list[bytes] | None = None) -> None:
        self._chunks = list(chunks or [])
        self._blocked = asyncio.Event()

    async def read(self, _: int) -> bytes:
        if self._chunks:
            return self._chunks.pop(0)
        await self._blocked.wait()
        return b""


class FakeProcess:
    def __init__(self, stdout_chunks: list[bytes] | None = None) -> None:
        self.stdin = FakeStdin()
        self.stdout = FakeStdout(stdout_chunks)
        self.wait = AsyncMock(return_value=0)
        self.terminate = Mock()


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
    pipeline = StreamPipeline("test_sess", "mp3_48k_stereo_320")
    mock_process = FakeProcess()

    with patch("asyncio.create_subprocess_exec", return_value=mock_process):
        await pipeline.start()
        assert pipeline._active is True
        assert pipeline._process is not None

        await pipeline.stop()
        assert pipeline._active is False
        assert pipeline._process is None
        diagnostics = pipeline.get_diagnostics_snapshot()
        assert diagnostics["live_jitter_target_ms"] == 250
        assert diagnostics["ffmpeg_input_format"]["frame_duration_ms"] == 20
        assert diagnostics["transport_heartbeat_window_ms"] == 500


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


@pytest.mark.asyncio
async def test_pipeline_injects_silence_for_healthy_idle() -> None:
    pipeline = StreamPipeline(
        "test_sess",
        "mp3_48k_stereo_320",
        keepalive_idle_threshold_ms=10,
        keepalive_frame_duration_ms=10,
        source_outage_grace_ms=100,
    )
    mock_process = FakeProcess()

    with patch("asyncio.create_subprocess_exec", return_value=mock_process):
        await pipeline.start()
        await asyncio.sleep(0.05)
        await pipeline.stop()

    diagnostics = pipeline.get_diagnostics_snapshot()
    expected_silence = b"\x00" * diagnostics["ffmpeg_input_format"]["bytes_per_frame"]
    assert diagnostics["silence_frames_written"] > 0
    assert any(chunk == expected_silence for chunk in mock_process.stdin.writes)


@pytest.mark.asyncio
async def test_pipeline_resumes_real_frames_after_keepalive() -> None:
    pipeline = StreamPipeline(
        "test_sess",
        "mp3_48k_stereo_320",
        keepalive_idle_threshold_ms=10,
        keepalive_frame_duration_ms=10,
        source_outage_grace_ms=100,
    )
    pipeline.jitter_buffer.target_ms = 10
    mock_process = FakeProcess()

    with patch("asyncio.create_subprocess_exec", return_value=mock_process):
        await pipeline.start()
        await asyncio.sleep(0.03)
        diagnostics = pipeline.get_diagnostics_snapshot()
        real_frame = AudioFrame(
            sequence=0,
            pts_ns=0,
            duration_ns=10_000_000,
            format={},
            audio_data=b"\x01\x00" * (diagnostics["ffmpeg_input_format"]["bytes_per_frame"] // 2),
        )
        await pipeline.push_frame(real_frame)
        await asyncio.sleep(0.03)
        await pipeline.stop()

    updated = pipeline.get_diagnostics_snapshot()
    assert updated["silence_frames_written"] > 0
    assert updated["real_frames_written"] > 0
    assert real_frame.audio_data in mock_process.stdin.writes
    assert updated["encoder_write_count"] >= updated["real_frames_written"] + updated["silence_frames_written"]
    assert updated["keepalive_to_first_real_frame_ms"] is not None


@pytest.mark.asyncio
async def test_pipeline_continues_keepalive_without_source_failure_policy() -> None:
    pipeline = StreamPipeline(
        "test_sess",
        "mp3_48k_stereo_320",
        keepalive_idle_threshold_ms=10,
        keepalive_frame_duration_ms=10,
        source_outage_grace_ms=30,
    )
    mock_process = FakeProcess()

    with patch("asyncio.create_subprocess_exec", return_value=mock_process):
        await pipeline.start()
        await asyncio.sleep(0.08)
        await pipeline.stop()

    diagnostics = pipeline.get_diagnostics_snapshot()
    assert diagnostics["silence_frames_written"] > 0
    assert diagnostics["source_outage_active"] is False


@pytest.mark.asyncio
async def test_pipeline_tracks_transport_heartbeat_from_stdout() -> None:
    pipeline = StreamPipeline(
        "test_sess",
        "mp3_48k_stereo_320",
        keepalive_idle_threshold_ms=10,
        keepalive_frame_duration_ms=10,
        transport_heartbeat_window_ms=50,
    )
    mock_process = FakeProcess(stdout_chunks=[b"encoded-data"])

    with patch("asyncio.create_subprocess_exec", return_value=mock_process):
        await pipeline.start()
        await asyncio.sleep(0.01)
        diagnostics = pipeline.get_diagnostics_snapshot()
        assert diagnostics["encoded_bytes_emitted_total"] == len(b"encoded-data")
        assert diagnostics["encoded_bytes_emitted_last_window"] == len(b"encoded-data")
        assert diagnostics["transport_alive"] is True
        assert diagnostics["last_stdout_read_monotonic"] is not None
        await asyncio.sleep(0.06)
        await pipeline.stop()

    final_diagnostics = pipeline.get_diagnostics_snapshot()
    assert final_diagnostics["transport_alive"] is False


@pytest.mark.asyncio
async def test_pipeline_tracks_client_fanout_timestamp() -> None:
    class DelayedStdout:
        async def read(self, _: int) -> bytes:
            await asyncio.sleep(0.02)
            return b"encoded-data"

    pipeline = StreamPipeline(
        "test_sess",
        "mp3_48k_stereo_320",
        keepalive_idle_threshold_ms=10,
        keepalive_frame_duration_ms=10,
    )
    mock_process = FakeProcess(stdout_chunks=[b"encoded-data"])
    mock_process.stdout = DelayedStdout()

    with patch("asyncio.create_subprocess_exec", return_value=mock_process):
        await pipeline.start()
        subscriber = pipeline.subscribe()
        read_task = asyncio.create_task(anext(subscriber))
        await asyncio.sleep(0.01)
        attached_diagnostics = pipeline.get_diagnostics_snapshot()
        assert attached_diagnostics["active_client_count"] == 1
        assert attached_diagnostics["last_client_attach_monotonic"] is not None
        first_chunk = await asyncio.wait_for(read_task, timeout=1.0)
        assert first_chunk == b"encoded-data"
        diagnostics = pipeline.get_diagnostics_snapshot()
        assert diagnostics["last_client_fanout_monotonic"] is not None
        await subscriber.aclose()
        detached_diagnostics = pipeline.get_diagnostics_snapshot()
        assert detached_diagnostics["active_client_count"] == 0
        assert detached_diagnostics["last_client_detach_monotonic"] is not None
        await pipeline.stop()


@pytest.mark.asyncio
async def test_pipeline_debug_capture_writes_pre_and_post_encoder_files(tmp_path: Path) -> None:
    pre_path = tmp_path / "pre.wav"
    post_path = tmp_path / "post.mp3"
    pipeline = StreamPipeline(
        "test_sess",
        "mp3_48k_stereo_320",
        keepalive_idle_threshold_ms=10,
        keepalive_frame_duration_ms=10,
        source_outage_grace_ms=100,
        debug_capture_enabled=True,
        debug_capture_pre_encoder_path=str(pre_path),
        debug_capture_post_encoder_path=str(post_path),
    )
    mock_process = FakeProcess(stdout_chunks=[b"encoded-data"])

    with patch("asyncio.create_subprocess_exec", return_value=mock_process):
        await pipeline.start()
        await asyncio.sleep(0.05)
        await pipeline.stop()

    assert pre_path.exists()
    assert post_path.exists()
    with wave.open(str(pre_path), "rb") as pre_wav:
        assert pre_wav.getnchannels() == 2
        assert pre_wav.getframerate() == 48000
        assert pre_wav.getsampwidth() == 2
        assert pre_wav.getnframes() > 0
    assert post_path.read_bytes() == b"encoded-data"
