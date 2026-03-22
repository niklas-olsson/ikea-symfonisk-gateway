"""Tests for session takeover and stop reasons."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bridge_core.adapters.base import OwnershipResult, OwnershipStatus
from bridge_core.core.event_bus import EventBus, EventType
from bridge_core.core.session_manager import (
    STOP_REASON_MANUAL,
    STOP_REASON_PREFERRED,
    STOP_REASON_RECLAIMED,
    STOP_REASON_SUPERSEDED,
    SessionManager,
    SessionState,
)
from bridge_core.core.source_registry import SourceRegistry
from bridge_core.core.target_registry import TargetRegistry
from ingress_sdk.types import PrepareResult, SourceCapabilities, SourceDescriptor, SourceType, StartResult


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture
def source_registry() -> MagicMock:
    registry = MagicMock(spec=SourceRegistry)
    registry.prepare_source.return_value = PrepareResult(success=True, source_id="src_1")
    registry.start_source.return_value = StartResult(success=True, session_id="adapter_sess_1")
    registry.get_source_health.return_value = None
    return registry


@pytest.fixture
def target_registry() -> MagicMock:
    registry = MagicMock(spec=TargetRegistry)
    registry.get_adapter_for_target.return_value = MagicMock()
    registry.get_adapter_for_target.return_value.inspect_ownership = AsyncMock(return_value=OwnershipResult(OwnershipStatus.OWNED))
    registry.prepare_target = AsyncMock(return_value={"success": True})
    registry.play_stream = AsyncMock(return_value={"success": True})
    registry.stop_target = AsyncMock(return_value={"success": True})
    registry.get_target.return_value = MagicMock()
    return registry


@pytest.fixture
def session_manager(
    event_bus: EventBus,
    source_registry: MagicMock,
    target_registry: MagicMock,
) -> SessionManager:
    return SessionManager(
        event_bus=event_bus,
        source_registry=source_registry,
        target_registry=target_registry,
    )


@pytest.mark.asyncio
async def test_session_preferred_device_takeover(session_manager: SessionManager, event_bus: EventBus) -> None:
    """Test that preferred device takeover reason is recorded."""
    queue = event_bus.subscribe(EventType.SESSION_STOPPED)

    await session_manager.create(source_id="src_1", target_id="tgt_1")
    await session_manager.create(
        source_id="src_2",
        target_id="tgt_1",
        takeover=True,
        takeover_reason=STOP_REASON_PREFERRED,
    )

    # Need to wait for the async stop_session task to complete and emit events
    event = None
    for _ in range(20):
        try:
            event = await asyncio.wait_for(queue.get(), timeout=0.1)
            break
        except TimeoutError:
            await asyncio.sleep(0.05)

    assert event is not None
    assert event.payload["stop_reason"] == STOP_REASON_PREFERRED


@pytest.mark.asyncio
async def test_session_manual_takeover(session_manager: SessionManager, event_bus: EventBus) -> None:
    """Test that creating a session with takeover=True stops the existing one."""
    queue = event_bus.subscribe(EventType.SESSION_STOPPED)

    # 1. Create first session
    sess1 = await session_manager.create(source_id="src_1", target_id="tgt_1")
    assert sess1.state == SessionState.CREATED

    # 2. Create second session on same target with takeover=True
    sess2 = await session_manager.create(
        source_id="src_2",
        target_id="tgt_1",
        takeover=True,
        takeover_reason=STOP_REASON_MANUAL,
    )

    # 3. Verify sess1 is being stopped
    event = None
    for _ in range(20):
        try:
            event = await asyncio.wait_for(queue.get(), timeout=0.1)
            break
        except TimeoutError:
            await asyncio.sleep(0.05)

    assert event is not None
    assert event.session_id == sess1.session_id
    assert event.payload["stop_reason"] == STOP_REASON_MANUAL
    assert sess1.stop_reason == STOP_REASON_MANUAL
    assert sess1.state == SessionState.SUPERSEDED  # type: ignore[comparison-overlap]

    # 4. Verify sess2 is created
    assert sess2.session_id != sess1.session_id
    assert sess2.source_id == "src_2"
    assert sess2.target_id == "tgt_1"


@pytest.mark.asyncio
async def test_session_superseded_default_reason(session_manager: SessionManager, event_bus: EventBus) -> None:
    """Test that default takeover reason is superseded_by_new_session."""
    queue = event_bus.subscribe(EventType.SESSION_STOPPED)

    await session_manager.create(source_id="src_1", target_id="tgt_1")
    await session_manager.create(source_id="src_2", target_id="tgt_1", takeover=True)

    event = None
    for _ in range(20):
        try:
            event = await asyncio.wait_for(queue.get(), timeout=0.1)
            break
        except TimeoutError:
            await asyncio.sleep(0.05)

    assert event is not None
    assert event.payload["stop_reason"] == STOP_REASON_SUPERSEDED


@pytest.mark.asyncio
async def test_target_reclaimed_during_monitoring(
    session_manager: SessionManager,
    event_bus: EventBus,
    target_registry: MagicMock,
    source_registry: MagicMock,
) -> None:
    """Test that a session is stopped with target_reclaimed if ownership is lost."""
    queue = event_bus.subscribe(EventType.SESSION_STOPPED)

    # 1. Setup session and source
    source_desc = SourceDescriptor(
        source_id="src_1",
        source_type=SourceType.LINE_IN,
        display_name="Line In",
        platform="linux",
        capabilities=SourceCapabilities(),
    )
    source_registry.resolve_source.return_value = MagicMock(
        source=source_desc,
        adapter_info=MagicMock(adapter=MagicMock()),
        local_source_id="line_in",
    )
    source_registry.probe_source_health.return_value = MagicMock(healthy=True, details={})

    session = await session_manager.create(source_id="src_1", target_id="tgt_1")

    # 2. Mock ownership loss (NOT_OWNED)
    target_registry.get_adapter_for_target.return_value.inspect_ownership.return_value = OwnershipResult(OwnershipStatus.NOT_OWNED)

    # 3. Start session with mocked pipeline and FFmpeg path
    with (
        patch("bridge_core.core.session_manager.resolve_ffmpeg_path", return_value="/usr/bin/ffmpeg"),
        patch("bridge_core.core.session_manager.negotiate_stream_profile", return_value="mp3_48k_stereo_320"),
        patch("bridge_core.core.session_manager.StreamPipeline") as mock_pipeline_cls,
    ):
        mock_pipeline = mock_pipeline_cls.return_value
        mock_pipeline.start = AsyncMock()
        mock_pipeline.stop = AsyncMock()
        mock_pipeline.jitter_buffer = MagicMock()
        mock_pipeline.jitter_buffer.size_ms = 10.0
        mock_pipeline.get_diagnostics_snapshot.return_value = {"transport_alive": True, "real_frames_written": 1}

        # Mock time.monotonic to jump ahead
        start_time = 100.0
        # Give enough values for the monitor loop and startup
        time_values = [start_time]
        for i in range(100):
            time_values.append(start_time + 6.0 + i * 0.1)

        with patch("time.monotonic", side_effect=time_values):
            await session_manager.start_session(session.session_id)

            # 4. Wait for the SESSION_STOPPED event
            event = None
            for _ in range(50):
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=0.1)
                    break
                except TimeoutError:
                    await asyncio.sleep(0.05)

            assert event is not None
            assert event.session_id == session.session_id
            assert event.payload["stop_reason"] == STOP_REASON_RECLAIMED
            assert session.stop_reason == STOP_REASON_RECLAIMED
            assert session.state == SessionState.STOPPED
