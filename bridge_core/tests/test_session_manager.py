"""Tests for SessionManager."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bridge_core.core.event_bus import EventBus, EventType
from bridge_core.core.session_manager import SessionManager, SessionState
from bridge_core.core.source_registry import SourceRegistry
from bridge_core.core.target_registry import TargetRegistry
from bridge_core.stream.pipeline import StreamPipeline
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
