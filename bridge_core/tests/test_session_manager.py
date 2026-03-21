"""Tests for SessionManager."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bridge_core.core.event_bus import EventBus, EventType
from bridge_core.core.session_manager import SessionFrameSink, SessionManager, SessionState
from bridge_core.core.source_registry import SourceRegistry
from bridge_core.core.target_registry import TargetRegistry
from bridge_core.stream.pipeline import StreamPipeline
from ingress_sdk.protocol import AudioFrame
from ingress_sdk.types import PrepareResult, SourceCapabilities, SourceDescriptor, SourceType, StartResult


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture
def source_registry(event_bus: EventBus) -> MagicMock:
    registry = MagicMock(spec=SourceRegistry)
    registry.prepare_source.return_value = PrepareResult(success=True, source_id="src_1")
    registry.start_source.return_value = StartResult(success=True, session_id="adapter_sess_1")
    return registry


@pytest.fixture
def stream_publisher() -> MagicMock:
    publisher = MagicMock()
    publisher.get_stream_url.return_value = "http://localhost:8080/streams/sess_1/live.mp3"
    return publisher


@pytest.fixture
def target_registry(event_bus: EventBus) -> MagicMock:
    registry = MagicMock(spec=TargetRegistry)
    registry.prepare_target = AsyncMock(return_value={"success": True})
    registry.play_stream = AsyncMock(return_value={"success": True})
    registry.stop_target = AsyncMock(return_value={"success": True})
    registry.heal_target = AsyncMock(return_value={"success": True})
    return registry


@pytest.fixture
def session_manager(
    event_bus: EventBus,
    source_registry: MagicMock,
    target_registry: MagicMock,
    stream_publisher: MagicMock,
) -> SessionManager:
    manager = SessionManager(
        event_bus=event_bus,
        source_registry=source_registry,
        target_registry=target_registry,
        stream_publisher=stream_publisher,
    )

    # Mock the pipeline start to avoid FFmpeg dependency in tests
    original_start_session = manager.start_session

    async def mocked_start_session(session_id: str) -> bool:
        session = manager.get(session_id)
        if session:
            session.pipeline = MagicMock(spec=StreamPipeline)
            session.pipeline.start = AsyncMock()
            session.pipeline.stop = AsyncMock()
            session.pipeline.push_frame = AsyncMock()
            session.pipeline.jitter_buffer = MagicMock()
            session.pipeline.jitter_buffer.size_ms = 10.0
        return await original_start_session(session_id)

    manager.start_session = mocked_start_session  # type: ignore[method-assign]
    return manager


@pytest.mark.asyncio
async def test_session_recovery(session_manager: SessionManager) -> None:
    """Test session recovery from FAILED and DEGRADED states."""
    session = session_manager.create(source_id="src_1", target_id="tgt_1")
    session.transition_to(SessionState.PREPARING)
    session.transition_to(SessionState.READY)
    await session_manager.start_session(session.session_id)

    # 1. Recover from FAILED
    session.transition_to(SessionState.FAILED)
    assert session.state == SessionState.FAILED
    await session_manager.recover(session.session_id)
    assert session.state == SessionState.PLAYING  # type: ignore[comparison-overlap]

    # 2. Recover from DEGRADED
    session.transition_to(SessionState.HEALING)
    session.transition_to(SessionState.DEGRADED)
    assert session.state == SessionState.DEGRADED
    await session_manager.recover(session.session_id)
    assert session.state == SessionState.PLAYING


@pytest.mark.asyncio
async def test_session_lifecycle(session_manager: SessionManager, event_bus: EventBus) -> None:
    """Test full successful session lifecycle."""
    # 1. Create
    session = session_manager.create(source_id="src_1", target_id="tgt_1")
    assert session.session_id.startswith("sess_")
    assert session.state == SessionState.CREATED
    assert session.source_id == "src_1"
    assert session.target_id == "tgt_1"

    # 2. Start (direct from CREATED)
    await session_manager.start_session(session.session_id)
    assert session.state == SessionState.PLAYING  # type: ignore[comparison-overlap]
    assert session.started_at is not None

    # 4. Stop
    await session_manager.stop_session(session.session_id)
    assert session.state == SessionState.STOPPED
    assert session.stopped_at is not None

    # 5. Terminate
    session_id = session.session_id
    session_manager.terminate(session_id)
    assert session_manager.get(session_id) is None


@pytest.mark.asyncio
async def test_invalid_transitions(session_manager: SessionManager) -> None:
    """Test that invalid state transitions raise ValueError."""
    session = session_manager.create(source_id="src_1", target_id="tgt_1")

    # Cannot jump from CREATED to PLAYING
    with pytest.raises(ValueError, match="Invalid transition"):
        session.transition_to(SessionState.PLAYING)

    # Cannot jump from CREATED to STOPPED
    with pytest.raises(ValueError, match="Invalid transition"):
        session.transition_to(SessionState.STOPPED)


@pytest.mark.asyncio
async def test_event_emission(session_manager: SessionManager, event_bus: EventBus) -> None:
    """Test that events are emitted correctly."""
    queue = event_bus.subscribe()

    # Create session
    session = session_manager.create(source_id="src_1", target_id="tgt_1")

    # Wait for event
    event = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert event.type == EventType.SESSION_CREATED
    assert event.session_id == session.session_id
    assert event.payload["source_id"] == "src_1"

    # Start session
    # Note: start() does transitions STARTING -> PLAYING
    await session_manager.start_session(session.session_id)

    # Check STARTING event
    event = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert event.type == EventType.SESSION_STARTING
    assert "source_id" in event.payload

    # Check PUBLISHER_ACTIVE event
    event = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert event.type == EventType.PUBLISHER_ACTIVE
    assert "stream_url" in event.payload

    # Check SOURCE_STARTED event (emitted by SessionManager)
    event = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert event.type == EventType.SOURCE_STARTED
    assert "adapter_session_id" in event.payload
    assert event.payload["backend"] is None

    # Check RENDERER_PLAYBACK_STARTED event
    event = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert event.type == EventType.RENDERER_PLAYBACK_STARTED
    assert "target_id" in event.payload
    assert "stream_url" in event.payload

    # Check STARTED event
    event = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert event.type == EventType.SESSION_STARTED
    assert "session_id" in event.payload

    # Stop session
    await session_manager.stop_session(session.session_id)

    # Check STOPPING event
    event = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert event.type == EventType.SESSION_STOPPING
    assert "session_id" in event.payload

    # Check STOPPED event
    event = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert event.type == EventType.SESSION_STOPPED
    assert "session_id" in event.payload


@pytest.mark.asyncio
async def test_terminate_playing_session(session_manager: SessionManager) -> None:
    """Test that terminating a playing session stops it first."""
    session = session_manager.create(source_id="src_1", target_id="tgt_1")
    await session_manager.start_session(session.session_id)

    session_id = session.session_id
    await session_manager.delete(session_id)

    assert session_manager.get(session_id) is None
    # session object itself should be STOPPED before being removed from manager
    assert session.state == SessionState.STOPPED


@pytest.mark.asyncio
async def test_idempotent_start_stop(session_manager: SessionManager) -> None:
    """Test that starting/stopping is idempotent."""
    session = session_manager.create(source_id="src_1", target_id="tgt_1")

    # Start twice
    assert await session_manager.start_session(session.session_id) is True
    assert session.state == SessionState.PLAYING
    assert await session_manager.start_session(session.session_id) is True
    assert session.state == SessionState.PLAYING

    # Stop twice
    assert await session_manager.stop_session(session.session_id) is True
    assert session.state == SessionState.STOPPED  # type: ignore[comparison-overlap]
    assert await session_manager.stop_session(session.session_id) is True
    assert session.state == SessionState.STOPPED


@pytest.mark.asyncio
async def test_cancellation_during_start(session_manager: SessionManager) -> None:
    """Test that session can be stopped while starting."""
    session = session_manager.create(source_id="src_1", target_id="tgt_1")
    session.transition_to(SessionState.STARTING)

    # Should be allowed to stop from STARTING
    assert await session_manager.stop_session(session.session_id) is True
    assert session.state == SessionState.STOPPED


@pytest.mark.asyncio
async def test_windows_source_verification_happens_before_renderer_work(
    event_bus: EventBus,
    stream_publisher: MagicMock,
    target_registry: MagicMock,
) -> None:
    source_registry = MagicMock(spec=SourceRegistry)
    source_registry.prepare_source.return_value = PrepareResult(success=True, source_id="default")
    source_registry.start_source.return_value = StartResult(success=True, session_id="adapter_sess_1", backend="pyaudiowpatch")
    source_registry.resolve_source.return_value = MagicMock(
        source=SourceDescriptor(
            source_id="windows-audio-adapter:system:default",
            source_type=SourceType.SYSTEM_OUTPUT,
            display_name="Default System Sound (windows)",
            platform="windows",
            capabilities=SourceCapabilities(),
        ),
        adapter_info=MagicMock(adapter=MagicMock()),
    )
    source_registry.probe_source_health.return_value = MagicMock(
        healthy=False,
        signal_present=False,
        source_state="stream_started_no_callbacks",
        details={"startup_substate": "stream_started_no_callbacks"},
    )

    manager = SessionManager(
        event_bus=event_bus,
        source_registry=source_registry,
        target_registry=target_registry,
        stream_publisher=stream_publisher,
    )
    session = manager.create(source_id="windows-audio-adapter:system:default", target_id="tgt_1")

    with (
        patch("bridge_core.core.session_manager.StreamPipeline") as mock_pipeline_cls,
        patch("bridge_core.core.session_manager.resolve_ffmpeg_path", return_value="/usr/bin/ffmpeg"),
        patch("asyncio.sleep", return_value=None),
    ):
        mock_pipeline = mock_pipeline_cls.return_value
        mock_pipeline.start = AsyncMock()
        mock_pipeline.stop = AsyncMock()
        mock_pipeline.jitter_buffer = MagicMock()
        mock_pipeline.jitter_buffer.size_ms = 0.0

        success = await manager.start_session(session.session_id)

    assert success is False
    target_registry.prepare_target.assert_not_awaited()
    target_registry.play_stream.assert_not_awaited()


@pytest.mark.asyncio
async def test_windows_source_verification_succeeds_without_jitter_buffer_activity(
    event_bus: EventBus,
    stream_publisher: MagicMock,
    target_registry: MagicMock,
) -> None:
    source_registry = MagicMock(spec=SourceRegistry)
    source_registry.prepare_source.return_value = PrepareResult(success=True, source_id="default")
    source_registry.start_source.return_value = StartResult(success=True, session_id="adapter_sess_1", backend="pyaudiowpatch")
    source_registry.resolve_source.return_value = MagicMock(
        source=SourceDescriptor(
            source_id="windows-audio-adapter:system:default",
            source_type=SourceType.SYSTEM_OUTPUT,
            display_name="Default System Sound (windows)",
            platform="windows",
            capabilities=SourceCapabilities(),
        ),
        adapter_info=MagicMock(adapter=MagicMock()),
    )
    source_registry.probe_source_health.return_value = MagicMock(
        healthy=True,
        signal_present=True,
        source_state="active",
        details={"startup_substate": "active", "callback_count": 5, "frames_emitted": 5},
    )

    manager = SessionManager(
        event_bus=event_bus,
        source_registry=source_registry,
        target_registry=target_registry,
        stream_publisher=stream_publisher,
    )
    session = manager.create(source_id="windows-audio-adapter:system:default", target_id="tgt_1")

    with (
        patch("bridge_core.core.session_manager.StreamPipeline") as mock_pipeline_cls,
        patch("bridge_core.core.session_manager.resolve_ffmpeg_path", return_value="/usr/bin/ffmpeg"),
        patch("asyncio.sleep", return_value=None),
    ):
        mock_pipeline = mock_pipeline_cls.return_value
        mock_pipeline.start = AsyncMock()
        mock_pipeline.stop = AsyncMock()
        mock_pipeline.jitter_buffer = MagicMock()
        mock_pipeline.jitter_buffer.size_ms = 0.0

        success = await manager.start_session(session.session_id)

    assert success is True
    target_registry.prepare_target.assert_awaited_once()
    target_registry.play_stream.assert_awaited_once()


@pytest.mark.asyncio
async def test_session_manager_passes_pipeline_runtime_config(
    event_bus: EventBus,
    source_registry: MagicMock,
    target_registry: MagicMock,
    stream_publisher: MagicMock,
) -> None:
    config_store = MagicMock()
    config_values = {
        "audio_keepalive_enabled": True,
        "audio_keepalive_idle_threshold_ms": 123,
        "audio_keepalive_frame_duration_ms": 17,
        "audio_source_outage_grace_ms": 4567,
        "audio_live_jitter_target_ms": 61,
        "audio_debug_capture_enabled": True,
        "audio_debug_capture_pre_encoder_path": "/tmp/pre.wav",
        "audio_debug_capture_post_encoder_path": "/tmp/post.mp3",
        "audio_debug_pacing_logs_enabled": True,
    }
    config_store.get.side_effect = lambda key, default=None: config_values.get(key, default)

    manager = SessionManager(
        event_bus=event_bus,
        source_registry=source_registry,
        target_registry=target_registry,
        stream_publisher=stream_publisher,
        config_store=config_store,
    )
    session = manager.create(source_id="src_1", target_id="tgt_1")

    with (
        patch("bridge_core.core.session_manager.StreamPipeline") as mock_pipeline_cls,
        patch("bridge_core.core.session_manager.resolve_ffmpeg_path", return_value="/usr/bin/ffmpeg"),
    ):
        mock_pipeline = mock_pipeline_cls.return_value
        mock_pipeline.start = AsyncMock()
        mock_pipeline.stop = AsyncMock()
        mock_pipeline.push_frame = AsyncMock()
        mock_pipeline.jitter_buffer = MagicMock()
        mock_pipeline.jitter_buffer.size_ms = 10.0

        success = await manager.start_session(session.session_id)

    assert success is True
    _, kwargs = mock_pipeline_cls.call_args
    assert kwargs["keepalive_enabled"] is True
    assert kwargs["keepalive_idle_threshold_ms"] == 123
    assert kwargs["keepalive_frame_duration_ms"] == 17
    assert kwargs["source_outage_grace_ms"] == 4567
    assert kwargs["live_jitter_target_ms"] == 61
    assert kwargs["debug_capture_enabled"] is True
    assert kwargs["debug_capture_pre_encoder_path"] == "/tmp/pre.wav"
    assert kwargs["debug_capture_post_encoder_path"] == "/tmp/post.mp3"
    assert kwargs["debug_pacing_logs_enabled"] is True
    assert "source_health_provider" not in kwargs


@pytest.mark.asyncio
async def test_windows_silent_source_viability_allows_startup(
    event_bus: EventBus,
    stream_publisher: MagicMock,
    target_registry: MagicMock,
) -> None:
    source_registry = MagicMock(spec=SourceRegistry)
    source_registry.prepare_source.return_value = PrepareResult(success=True, source_id="default")
    source_registry.start_source.return_value = StartResult(success=True, session_id="adapter_sess_1", backend="pyaudiowpatch")
    source_registry.resolve_source.return_value = MagicMock(
        source=SourceDescriptor(
            source_id="windows-audio-adapter:system:default",
            source_type=SourceType.SYSTEM_OUTPUT,
            display_name="Default System Sound (windows)",
            platform="windows",
            capabilities=SourceCapabilities(),
        ),
        adapter_info=MagicMock(adapter=MagicMock()),
    )
    source_registry.probe_source_health.return_value = MagicMock(
        healthy=True,
        signal_present=False,
        source_state="stream_started_no_callbacks",
        last_error=None,
        details={
            "startup_substate": "stream_started_no_callbacks",
            "callback_count": 0,
            "frames_emitted": 0,
            "start_viability": {
                "stream_opened": True,
                "stream_started": True,
                "callback_registered": True,
            },
        },
    )

    config_store = MagicMock()
    config_store.get.side_effect = lambda key, default=None: {
        "audio_live_startup_allow_silent_source": True,
        "audio_live_startup_viability_timeout_ms": 1000,
    }.get(key, default)

    manager = SessionManager(
        event_bus=event_bus,
        source_registry=source_registry,
        target_registry=target_registry,
        stream_publisher=stream_publisher,
        config_store=config_store,
    )
    session = manager.create(source_id="windows-audio-adapter:system:default", target_id="tgt_1")

    with (
        patch("bridge_core.core.session_manager.StreamPipeline") as mock_pipeline_cls,
        patch("bridge_core.core.session_manager.resolve_ffmpeg_path", return_value="/usr/bin/ffmpeg"),
        patch("asyncio.sleep", return_value=None),
    ):
        mock_pipeline = mock_pipeline_cls.return_value
        mock_pipeline.start = AsyncMock()
        mock_pipeline.stop = AsyncMock()
        mock_pipeline.jitter_buffer = MagicMock()
        mock_pipeline.jitter_buffer.size_ms = 0.0
        mock_pipeline.get_diagnostics_snapshot.return_value = {
            "real_frames_written": 0,
            "runtime_mode": "idle_pending_signal",
            "last_real_frame_age_ms": None,
            "keepalive_to_first_real_frame_ms": None,
        }

        success = await manager.start_session(session.session_id)

    assert success is True
    assert session.state == SessionState.PLAYING
    target_registry.prepare_target.assert_awaited_once()
    target_registry.play_stream.assert_awaited_once()


@pytest.mark.asyncio
async def test_pipeline_runtime_config_only_uses_live_defaults_for_windows_system_output(
    event_bus: EventBus,
    source_registry: MagicMock,
    target_registry: MagicMock,
    stream_publisher: MagicMock,
) -> None:
    source_registry.resolve_source.return_value = MagicMock(
        source=SourceDescriptor(
            source_id="src_1",
            source_type=SourceType.SYNTHETIC_TEST_SOURCE,
            display_name="Synthetic",
            platform="linux",
            capabilities=SourceCapabilities(),
        ),
        adapter_info=MagicMock(adapter=MagicMock()),
    )
    config_store = MagicMock()
    config_store.get.side_effect = lambda key, default=None: default
    manager = SessionManager(
        event_bus=event_bus,
        source_registry=source_registry,
        target_registry=target_registry,
        stream_publisher=stream_publisher,
        config_store=config_store,
    )
    session = manager.create(source_id="src_1", target_id="tgt_1")

    with (
        patch("bridge_core.core.session_manager.StreamPipeline") as mock_pipeline_cls,
        patch("bridge_core.core.session_manager.resolve_ffmpeg_path", return_value="/usr/bin/ffmpeg"),
    ):
        mock_pipeline = mock_pipeline_cls.return_value
        mock_pipeline.start = AsyncMock()
        mock_pipeline.stop = AsyncMock()
        mock_pipeline.push_frame = AsyncMock()
        mock_pipeline.jitter_buffer = MagicMock()
        mock_pipeline.jitter_buffer.size_ms = 10.0

        success = await manager.start_session(session.session_id)

    assert success is True
    _, kwargs = mock_pipeline_cls.call_args
    assert kwargs["keepalive_idle_threshold_ms"] == 200
    assert kwargs["keepalive_frame_duration_ms"] == 20
    assert kwargs["live_jitter_target_ms"] == 250


@pytest.mark.asyncio
async def test_silent_viability_startup_degrades_if_never_active(
    event_bus: EventBus,
    stream_publisher: MagicMock,
    target_registry: MagicMock,
) -> None:
    source_registry = MagicMock(spec=SourceRegistry)
    source_registry.prepare_source.return_value = PrepareResult(success=True, source_id="default")
    source_registry.start_source.return_value = StartResult(success=True, session_id="adapter_sess_1", backend="pyaudiowpatch")
    source_registry.resolve_source.return_value = MagicMock(
        source=SourceDescriptor(
            source_id="windows-audio-adapter:system:default",
            source_type=SourceType.SYSTEM_OUTPUT,
            display_name="Default System Sound (windows)",
            platform="windows",
            capabilities=SourceCapabilities(),
        ),
        adapter_info=MagicMock(adapter=MagicMock()),
    )
    source_registry.probe_source_health.return_value = MagicMock(
        healthy=True,
        signal_present=False,
        source_state="stream_started_no_callbacks",
        last_error=None,
        details={
            "startup_substate": "stream_started_no_callbacks",
            "callback_count": 0,
            "frames_emitted": 0,
            "start_viability": {"stream_opened": True, "stream_started": True, "callback_registered": True},
        },
    )
    config_store = MagicMock()
    config_store.get.side_effect = lambda key, default=None: {
        "audio_live_startup_allow_silent_source": True,
        "audio_live_startup_viability_timeout_ms": 1000,
    }.get(key, default)

    manager = SessionManager(
        event_bus=event_bus,
        source_registry=source_registry,
        target_registry=target_registry,
        stream_publisher=stream_publisher,
        config_store=config_store,
    )
    session = manager.create(source_id="windows-audio-adapter:system:default", target_id="tgt_1")

    with (
        patch("bridge_core.core.session_manager.StreamPipeline") as mock_pipeline_cls,
        patch("bridge_core.core.session_manager.resolve_ffmpeg_path", return_value="/usr/bin/ffmpeg"),
        patch("bridge_core.core.session_manager.WINDOWS_SOURCE_ACTIVITY_WATCHDOG_MS_DEFAULT", 10),
    ):
        mock_pipeline = mock_pipeline_cls.return_value
        mock_pipeline.start = AsyncMock()
        mock_pipeline.stop = AsyncMock()
        mock_pipeline.jitter_buffer = MagicMock()
        mock_pipeline.jitter_buffer.size_ms = 0.0
        mock_pipeline.get_diagnostics_snapshot.return_value = {
            "real_frames_written": 0,
            "runtime_mode": "idle_pending_signal",
            "last_real_frame_age_ms": None,
            "keepalive_to_first_real_frame_ms": None,
        }

        success = await manager.start_session(session.session_id)
        assert success is True
        await asyncio.sleep(1.2)

    assert session.state == SessionState.DEGRADED
    await manager.stop_session(session.session_id)


@pytest.mark.asyncio
async def test_session_frame_sink_assigns_monotonic_sequences() -> None:
    pipeline = MagicMock(spec=StreamPipeline)
    pushed_frames: list[AudioFrame] = []

    async def push_frame(frame: AudioFrame) -> None:
        pushed_frames.append(frame)

    pipeline.push_frame = AsyncMock(side_effect=push_frame)
    sink = SessionFrameSink(pipeline)
    sink.start()

    sink.on_frame(b"frame1", 0, 1_000_000)
    sink.on_frame(b"frame2", 1_000_000, 1_000_000)
    sink.on_frame(b"frame3", 2_000_000, 1_000_000)
    await asyncio.wait_for(sink._queue.join(), timeout=1.0)
    sink.stop()

    assert [frame.sequence for frame in pushed_frames] == [0, 1, 2]


@pytest.mark.asyncio
async def test_session_frame_sink_sequence_resets_per_instance() -> None:
    pipeline = MagicMock(spec=StreamPipeline)
    pushed_frames: list[AudioFrame] = []

    async def push_frame(frame: AudioFrame) -> None:
        pushed_frames.append(frame)

    pipeline.push_frame = AsyncMock(side_effect=push_frame)

    first_sink = SessionFrameSink(pipeline)
    first_sink.start()
    first_sink.on_frame(b"frame1", 0, 1_000_000)
    await asyncio.wait_for(first_sink._queue.join(), timeout=1.0)
    first_sink.stop()

    second_sink = SessionFrameSink(pipeline)
    second_sink.start()
    second_sink.on_frame(b"frame2", 0, 1_000_000)
    await asyncio.wait_for(second_sink._queue.join(), timeout=1.0)
    second_sink.stop()

    assert [frame.sequence for frame in pushed_frames] == [0, 0]
