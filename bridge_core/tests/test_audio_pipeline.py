import asyncio
import logging
import time
import wave
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest
from bridge_core.core.session_manager import SessionFrameSink
from bridge_core.stream.pipeline import JitterBuffer, PipelineSubscriber, PipelineSubscriberInfo, StreamPipeline
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
        self.stdout: Any = FakeStdout(stdout_chunks)
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
        assert diagnostics["transport_heartbeat_window_ms"] == 1000
        assert diagnostics["running_delivery_profile"] == "stable"


@pytest.mark.asyncio
async def test_pipeline_experimental_policy_defaults() -> None:
    pipeline = StreamPipeline("test_sess", "mp3_48k_stereo_320", delivery_profile="experimental")
    diagnostics = pipeline.get_diagnostics_snapshot()

    assert diagnostics["running_delivery_profile"] == "experimental"
    assert pipeline._primary_policy.queue_bytes == 131072
    assert pipeline._primary_policy.overflow_grace_ms == 750
    assert pipeline._primary_policy.max_backlog_ms == 750


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
        keepalive_enabled=True,
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
        keepalive_enabled=True,
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
        keepalive_enabled=True,
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
        keepalive_enabled=True,
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
async def test_stable_pipeline_feeds_real_frames_without_fixed_slot_pacing() -> None:
    pipeline = StreamPipeline("test_sess", "mp3_48k_stereo_320", delivery_profile="stable")
    frames = [
        AudioFrame(sequence=index, pts_ns=index * 10_000_000, duration_ns=10_000_000, format={}, audio_data=b"x" * 4)
        for index in range(3)
    ]
    write_times: list[float] = []

    async def fake_pop() -> AudioFrame | None:
        if frames:
            return frames.pop(0)
        pipeline._active = False
        return None

    async def fake_write(_: bytes, *, is_silence: bool, now: float) -> None:
        assert is_silence is False
        write_times.append(time.monotonic())

    pipeline.jitter_buffer.pop = AsyncMock(side_effect=fake_pop)  # type: ignore[method-assign]
    pipeline._write_encoder_input = AsyncMock(side_effect=fake_write)  # type: ignore[method-assign]
    pipeline._active = True

    started = time.monotonic()
    await pipeline._feed_ffmpeg()
    elapsed = time.monotonic() - started

    assert len(write_times) == 3
    assert elapsed < 0.02


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
async def test_pipeline_stop_drains_background_tasks() -> None:
    pipeline = StreamPipeline("test_sess", "mp3_48k_stereo_320")
    mock_process = FakeProcess()

    with patch("asyncio.create_subprocess_exec", return_value=mock_process):
        await pipeline.start()
        await pipeline.stop()

    assert pipeline._feed_task is None
    assert pipeline._read_task is None
    assert pipeline._process is None


@pytest.mark.asyncio
async def test_pipeline_subscriber_shutdown_drains_pending_waiters() -> None:
    pipeline = StreamPipeline("test_sess", "mp3_48k_stereo_320")
    mock_process = FakeProcess()

    with patch("asyncio.create_subprocess_exec", return_value=mock_process):
        await pipeline.start()
        subscriber = pipeline.subscribe()
        read_task = asyncio.create_task(anext(subscriber))
        await asyncio.sleep(0)
        await pipeline.stop()
        with pytest.raises((StopAsyncIteration, asyncio.CancelledError)):
            await asyncio.wait_for(read_task, timeout=1.0)
        await subscriber.aclose()


@pytest.mark.asyncio
async def test_pipeline_evicts_stalled_subscriber_and_keeps_healthy_one() -> None:
    pipeline = StreamPipeline(
        "test_sess",
        "mp3_48k_stereo_320",
        target_id="tgt_1",
        client_queue_bytes=8,
        client_overflow_grace_ms=1,
    )
    now = time.monotonic()
    healthy = PipelineSubscriber(
        subscriber_id=1,
        queue=asyncio.Queue(),
        wake_event=asyncio.Event(),
        attached_monotonic=now,
        role="primary_renderer",
        remote_addr="192.168.1.10",
        user_agent="Sonos/1.0",
        delivery_path_id="tgt_1",
        last_successful_enqueue_monotonic=now,
        last_successful_dequeue_monotonic=now,
        last_successful_yield_monotonic=now,
        overflow_started_monotonic=None,
        overflow_events=0,
        queued_bytes=0,
    )
    stalled = PipelineSubscriber(
        subscriber_id=2,
        queue=asyncio.Queue(),
        wake_event=asyncio.Event(),
        attached_monotonic=now,
        role="auxiliary",
        remote_addr="127.0.0.1",
        user_agent="curl",
        delivery_path_id=None,
        last_successful_enqueue_monotonic=now,
        last_successful_dequeue_monotonic=now,
        last_successful_yield_monotonic=now,
        overflow_started_monotonic=now - 1,
        overflow_events=1,
        queued_bytes=8,
    )
    pipeline._clients = [healthy, stalled]
    pipeline._active = True

    await pipeline._fan_out_encoded_chunk(b"abcd", time.monotonic())

    diagnostics = pipeline.get_diagnostics_snapshot()
    assert diagnostics["active_client_count"] == 1
    assert diagnostics["effective_client_count"] == 1
    assert diagnostics["client_stall_disconnects_total"] == 1
    assert healthy.queue.qsize() == 1
    assert stalled.closed is True
    assert diagnostics["primary_delivery_alive"] is True


@pytest.mark.asyncio
async def test_pipeline_closes_evicted_subscriber_cleanly() -> None:
    pipeline = StreamPipeline(
        "test_sess",
        "mp3_48k_stereo_320",
        target_id="tgt_1",
        delivery_profile="experimental",
        client_queue_bytes=8,
        client_overflow_grace_ms=1,
    )
    subscriber = pipeline.subscribe(PipelineSubscriberInfo(role="primary_renderer", delivery_path_id="tgt_1"))
    subscribe_task = asyncio.create_task(anext(subscriber))
    await asyncio.sleep(0)
    internal = pipeline._clients[0]
    internal.queued_bytes = 8
    internal.overflow_started_monotonic = time.monotonic() - 1
    pipeline._active = True

    await pipeline._fan_out_encoded_chunk(b"abcd", time.monotonic())
    with pytest.raises((StopAsyncIteration, asyncio.CancelledError)):
        await asyncio.wait_for(subscribe_task, timeout=1.0)
    await subscriber.aclose()

    diagnostics = pipeline.get_diagnostics_snapshot()
    assert diagnostics["active_client_count"] == 0
    assert diagnostics["last_client_detach_monotonic"] is not None


@pytest.mark.asyncio
async def test_primary_establishment_and_health_require_yield_progress() -> None:
    pipeline = StreamPipeline(
        "test_sess",
        "mp3_48k_stereo_320",
        target_id="tgt_1",
        delivery_profile="experimental",
        transport_heartbeat_window_ms=100,
        primary_health_require_yield_progress=True,
    )
    now = time.monotonic()
    primary = PipelineSubscriber(
        subscriber_id=1,
        queue=asyncio.Queue(),
        wake_event=asyncio.Event(),
        attached_monotonic=now,
        role="primary_renderer",
        remote_addr="192.168.1.10",
        user_agent="Sonos/1.0",
        delivery_path_id="tgt_1",
        last_successful_enqueue_monotonic=now,
        last_successful_dequeue_monotonic=now,
        last_successful_yield_monotonic=None,
        overflow_started_monotonic=None,
        overflow_events=0,
        queued_bytes=0,
    )
    pipeline._clients = [primary]

    diagnostics = pipeline.get_diagnostics_snapshot()
    assert diagnostics["primary_client_count"] == 1
    assert diagnostics["primary_effective_client_count"] == 0
    assert diagnostics["primary_delivery_alive"] is False
    assert diagnostics["subscribers"][0]["is_establishing_candidate"] is True

    primary.last_successful_yield_monotonic = time.monotonic()
    diagnostics = pipeline.get_diagnostics_snapshot()
    assert diagnostics["primary_effective_client_count"] == 1
    assert diagnostics["primary_delivery_alive"] is True
    assert diagnostics["primary_delivery_health_model"] == "strict"


@pytest.mark.asyncio
async def test_stable_primary_delivery_alive_uses_attached_primary_count() -> None:
    pipeline = StreamPipeline(
        "test_sess",
        "mp3_48k_stereo_320",
        target_id="tgt_1",
        delivery_profile="stable",
        transport_heartbeat_window_ms=100,
    )
    now = time.monotonic()
    primary = PipelineSubscriber(
        subscriber_id=1,
        queue=asyncio.Queue(),
        wake_event=asyncio.Event(),
        attached_monotonic=now,
        role="primary_renderer",
        remote_addr="192.168.1.10",
        user_agent="Sonos/1.0",
        delivery_path_id="tgt_1",
        last_successful_enqueue_monotonic=now,
        last_successful_dequeue_monotonic=None,
        last_successful_yield_monotonic=None,
        overflow_started_monotonic=None,
        overflow_events=0,
        queued_bytes=0,
    )
    pipeline._clients = [primary]

    diagnostics = pipeline.get_diagnostics_snapshot()
    assert diagnostics["primary_client_count"] == 1
    assert diagnostics["primary_effective_client_count"] == 1
    assert diagnostics["primary_delivery_alive"] is True
    assert diagnostics["primary_delivery_health_model"] == "attached"
    assert diagnostics["subscribers"][0]["is_primary_candidate"] is True
    assert diagnostics["subscribers"][0]["is_primary_healthy"] is True


@pytest.mark.asyncio
async def test_stable_primary_subscriber_is_not_evicted_on_overflow() -> None:
    pipeline = StreamPipeline(
        "test_sess",
        "mp3_48k_stereo_320",
        target_id="tgt_1",
        delivery_profile="stable",
        primary_client_queue_bytes=8,
        primary_client_overflow_grace_ms=1,
    )
    now = time.monotonic()
    primary = PipelineSubscriber(
        subscriber_id=1,
        queue=asyncio.Queue(),
        wake_event=asyncio.Event(),
        attached_monotonic=now,
        role="primary_renderer",
        remote_addr="192.168.1.10",
        user_agent="Sonos/1.0",
        delivery_path_id="tgt_1",
        last_successful_enqueue_monotonic=now,
        last_successful_dequeue_monotonic=None,
        last_successful_yield_monotonic=None,
        overflow_started_monotonic=now - 1,
        overflow_events=1,
        queued_bytes=8,
    )
    pipeline._clients = [primary]
    pipeline._active = True

    await pipeline._fan_out_encoded_chunk(b"abcd", time.monotonic())

    diagnostics = pipeline.get_diagnostics_snapshot()
    assert diagnostics["active_client_count"] == 1
    assert diagnostics["client_queue_overflow_events_total"] == 1
    assert diagnostics["client_stall_disconnects_total"] == 0
    assert primary.closed is False
    assert primary.queue.qsize() == 1
    assert diagnostics["primary_delivery_alive"] is True


@pytest.mark.asyncio
async def test_experimental_primary_subscriber_still_evicted_on_overflow() -> None:
    pipeline = StreamPipeline(
        "test_sess",
        "mp3_48k_stereo_320",
        target_id="tgt_1",
        delivery_profile="experimental",
        primary_client_queue_bytes=8,
        primary_client_overflow_grace_ms=1,
    )
    now = time.monotonic()
    primary = PipelineSubscriber(
        subscriber_id=1,
        queue=asyncio.Queue(),
        wake_event=asyncio.Event(),
        attached_monotonic=now,
        role="primary_renderer",
        remote_addr="192.168.1.10",
        user_agent="Sonos/1.0",
        delivery_path_id="tgt_1",
        last_successful_enqueue_monotonic=now,
        last_successful_dequeue_monotonic=now,
        last_successful_yield_monotonic=now,
        overflow_started_monotonic=now - 1,
        overflow_events=1,
        queued_bytes=8,
    )
    pipeline._clients = [primary]
    pipeline._active = True

    await pipeline._fan_out_encoded_chunk(b"abcd", time.monotonic())

    diagnostics = pipeline.get_diagnostics_snapshot()
    assert diagnostics["active_client_count"] == 0
    assert diagnostics["client_queue_overflow_events_total"] == 1
    assert diagnostics["client_stall_disconnects_total"] == 1
    assert primary.closed is True


@pytest.mark.asyncio
async def test_experimental_primary_health_snapshot_still_requires_progress() -> None:
    pipeline = StreamPipeline(
        "test_sess",
        "mp3_48k_stereo_320",
        target_id="tgt_1",
        delivery_profile="experimental",
        transport_heartbeat_window_ms=100,
        primary_health_require_yield_progress=True,
    )
    now = time.monotonic()
    primary = PipelineSubscriber(
        subscriber_id=1,
        queue=asyncio.Queue(),
        wake_event=asyncio.Event(),
        attached_monotonic=now,
        role="primary_renderer",
        remote_addr="192.168.1.10",
        user_agent="Sonos/1.0",
        delivery_path_id="tgt_1",
        last_successful_enqueue_monotonic=now,
        last_successful_dequeue_monotonic=now,
        last_successful_yield_monotonic=None,
        overflow_started_monotonic=None,
        overflow_events=0,
        queued_bytes=0,
    )
    pipeline._clients = [primary]

    diagnostics = pipeline.get_diagnostics_snapshot()
    assert diagnostics["subscribers"][0]["is_primary_candidate"] is True
    assert diagnostics["subscribers"][0]["is_primary_healthy"] is False

    primary.last_successful_yield_monotonic = time.monotonic()
    diagnostics = pipeline.get_diagnostics_snapshot()
    assert diagnostics["subscribers"][0]["is_primary_healthy"] is True


@pytest.mark.asyncio
async def test_pipeline_debug_capture_writes_pre_and_post_encoder_files(tmp_path: Path) -> None:
    pre_path = tmp_path / "pre.wav"
    post_path = tmp_path / "post.mp3"
    pipeline = StreamPipeline(
        "test_sess",
        "mp3_48k_stereo_320",
        keepalive_enabled=True,
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
