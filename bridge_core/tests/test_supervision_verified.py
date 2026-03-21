"""Verification tests for supervised task failures."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bridge_core.core.event_bus import EventBus, EventType
from bridge_core.core.session_manager import (
    SessionManager,
    SessionState,
)
from bridge_core.core.source_registry import SourceRegistry
from bridge_core.core.target_registry import TargetRegistry
from bridge_core.stream.pipeline import StreamPipeline


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture
def source_registry() -> MagicMock:
    registry = MagicMock(spec=SourceRegistry)
    registry.prepare_source.return_value = MagicMock(success=True)
    registry.start_source.return_value = MagicMock(success=True, session_id="adapter_sess_1")
    return registry


@pytest.fixture
def target_registry() -> MagicMock:
    registry = MagicMock(spec=TargetRegistry)
    registry.prepare_target = AsyncMock(return_value={"success": True})
    registry.play_stream = AsyncMock(return_value={"success": True})
    registry.stop_target = AsyncMock(return_value={"success": True})
    return registry


@pytest.fixture
def session_manager(event_bus: EventBus, source_registry: MagicMock, target_registry: MagicMock) -> SessionManager:
    return SessionManager(
        event_bus=event_bus,
        source_registry=source_registry,
        target_registry=target_registry,
    )


@pytest.mark.asyncio
async def test_supervised_push_frame_failure(
    session_manager: SessionManager, event_bus: EventBus, caplog: pytest.LogCaptureFixture
) -> None:
    """
    Verify that a failure in push_frame is now supervised and triggers session failure.
    """
    session = session_manager.create(source_id="src_1", target_id="tgt_1")

    # Mock resolve_ffmpeg_path to avoid FFmpeg requirement
    with patch("bridge_core.core.session_manager.resolve_ffmpeg_path", return_value="/usr/bin/ffmpeg"):
        # Mock pipeline and its start
        with patch("bridge_core.core.session_manager.StreamPipeline") as mock_pipeline_cls:
            pipeline = mock_pipeline_cls.return_value
            pipeline.start = AsyncMock()
            pipeline.stop = AsyncMock()
            pipeline.push_frame = AsyncMock(side_effect=RuntimeError("Push failed supervised!"))
            # Mock jitter_buffer for frame ingestion verification
            pipeline.jitter_buffer = MagicMock()
            pipeline.jitter_buffer.size_ms = 10.0

            await session_manager.start_session(session.session_id)
            assert session.state == SessionState.PLAYING

            # Subscribe to events to wait for failure
            queue = event_bus.subscribe()

            # Trigger frame which should trigger push_frame failure in the ingestion loop
            sink = session_manager._frame_sinks[session.session_id]
            sink.on_frame(b"data", 0, 1000)

            # Wait for SESSION_FAILED event
            found_event = False
            for _ in range(10):
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=0.2)
                    if event.type == EventType.SESSION_FAILED:
                        assert "Push failed supervised!" in event.payload["error"]
                        found_event = True
                        break
                except TimeoutError:
                    continue

            assert found_event
            # The session should be transitioned to FAILED
            assert session.state in [SessionState.FAILED, SessionState.STOPPING, SessionState.STOPPED]


@pytest.mark.asyncio
async def test_supervised_pipeline_task_failure(
    session_manager: SessionManager, event_bus: EventBus, caplog: pytest.LogCaptureFixture
) -> None:
    """
    Verify that a failure in pipeline background tasks is now supervised.
    """
    session = session_manager.create(source_id="src_1", target_id="tgt_1")

    with patch("bridge_core.core.session_manager.resolve_ffmpeg_path", return_value="/usr/bin/ffmpeg"):
        with patch("bridge_core.core.session_manager.StreamPipeline") as mock_pipeline_cls:
            pipeline_instance = MagicMock(spec=StreamPipeline)
            mock_pipeline_cls.return_value = pipeline_instance
            pipeline_instance.start = AsyncMock()
            pipeline_instance.stop = AsyncMock()
            # Mock jitter_buffer for frame ingestion verification
            pipeline_instance.jitter_buffer = MagicMock()
            pipeline_instance.jitter_buffer.size_ms = 10.0

            await session_manager.start_session(session.session_id)

            # Get the on_error callback that was passed to mock_pipeline_cls
            # Ensure mock_pipeline_cls was called
            assert mock_pipeline_cls.called
            _, kwargs = mock_pipeline_cls.call_args
            on_error_cb = kwargs.get("on_error")
            assert on_error_cb is not None

            # Simulate task failure
            on_error_cb(RuntimeError("Pipeline task died supervised!"))

            # Check for event
            queue = event_bus.subscribe()
            found_event = False
            for _ in range(10):
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=0.2)
                    if event.type == EventType.SESSION_FAILED:
                        assert "Pipeline task died supervised!" in event.payload["error"]
                        found_event = True
                        break
                except TimeoutError:
                    continue

            assert found_event
            assert session.state in [SessionState.FAILED, SessionState.STOPPING, SessionState.STOPPED]


@pytest.mark.asyncio
async def test_adapter_on_error_propagation(session_manager: SessionManager, event_bus: EventBus) -> None:
    """
    Verify that adapter calling on_error propagates to SessionManager.
    """
    session = session_manager.create(source_id="src_1", target_id="tgt_1")

    with patch("bridge_core.core.session_manager.resolve_ffmpeg_path", return_value="/usr/bin/ffmpeg"):
        with patch("bridge_core.core.session_manager.StreamPipeline") as mock_pipeline_cls:
            pipeline = mock_pipeline_cls.return_value
            pipeline.start = AsyncMock()
            pipeline.stop = AsyncMock()
            # Mock jitter_buffer for frame ingestion verification
            pipeline.jitter_buffer = MagicMock()
            pipeline.jitter_buffer.size_ms = 10.0

            await session_manager.start_session(session.session_id)

            sink = session_manager._frame_sinks[session.session_id]

            # Simulate adapter error
            sink.on_error(RuntimeError("Adapter hardware failure!"))

            queue = event_bus.subscribe()
            found_event = False
            for _ in range(10):
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=0.2)
                    if event.type == EventType.SESSION_FAILED:
                        assert "Adapter hardware failure!" in event.payload["error"]
                        found_event = True
                        break
                except TimeoutError:
                    continue

            assert found_event
            assert session.state in [SessionState.FAILED, SessionState.STOPPING, SessionState.STOPPED]
