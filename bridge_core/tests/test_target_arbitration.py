"""Tests for Target Arbitration and Takeover semantics."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bridge_core.core.errors import SessionConflictError
from bridge_core.core.event_bus import EventBus
from bridge_core.core.session_manager import SessionManager, SessionState
from bridge_core.core.source_registry import SourceRegistry
from bridge_core.core.target_registry import TargetRegistry
from ingress_sdk.types import (
    PrepareResult,
    SourceCapabilities,
    SourceDescriptor,
    SourceType,
    StartResult,
)


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture
def source_registry() -> MagicMock:
    registry = MagicMock(spec=SourceRegistry)
    registry.prepare_source.return_value = PrepareResult(success=True, source_id="src_1")
    registry.start_source.return_value = StartResult(success=True, session_id="adapter_sess_1")
    registry.resolve_source.return_value = MagicMock(
        source=SourceDescriptor(
            source_id="src_1",
            source_type=SourceType.SYNTHETIC_TEST_SOURCE,
            display_name="Source 1",
            platform="linux",
            capabilities=SourceCapabilities(),
        ),
        adapter_info=MagicMock(adapter=MagicMock()),
    )
    return registry


@pytest.fixture
def target_registry() -> MagicMock:
    registry = MagicMock(spec=TargetRegistry)
    registry.get_target.return_value = MagicMock(
        target_id="tgt_1",
        supported_codecs=["mp3"],
        supported_sample_rates=[48000],
        supported_channels=[2],
    )
    registry.prepare_target = AsyncMock(return_value={"success": True})
    registry.play_stream = AsyncMock(return_value={"success": True})
    registry.stop_target = AsyncMock(return_value={"success": True})

    mock_adapter = MagicMock()
    mock_adapter.inspect_ownership = AsyncMock(
        return_value=MagicMock(status="owned")
    )
    registry.get_adapter_for_target.return_value = mock_adapter

    return registry


@pytest.fixture
def session_manager(
    event_bus: EventBus,
    source_registry: MagicMock,
    target_registry: MagicMock,
) -> SessionManager:
    manager = SessionManager(
        event_bus=event_bus,
        source_registry=source_registry,
        target_registry=target_registry,
        stream_publisher=MagicMock(),
    )

    # Mock the pipeline start to avoid FFmpeg dependency
    original_start_session = manager.start_session

    async def mocked_start_session(session_id: str) -> bool:
        with (
            patch("bridge_core.core.session_manager.resolve_ffmpeg_path", return_value="/usr/bin/ffmpeg"),
            patch("bridge_core.core.session_manager.negotiate_stream_profile", return_value="mp3_48k_stereo_320"),
            patch("bridge_core.core.session_manager.StreamPipeline") as mock_pipeline_cls,
        ):
            mock_pipeline = mock_pipeline_cls.return_value
            mock_pipeline.start = AsyncMock()
            mock_pipeline.stop = AsyncMock()
            mock_pipeline.push_frame = AsyncMock()
            mock_pipeline.jitter_buffer = MagicMock()
            mock_pipeline.jitter_buffer.size_ms = 10.0
            mock_pipeline.get_diagnostics_snapshot.return_value = {}

            return await original_start_session(session_id)

    manager.start_session = mocked_start_session  # type: ignore[method-assign]
    return manager


@pytest.mark.asyncio
async def test_target_takeover_at_start(session_manager: SessionManager) -> None:
    """Test that starting a new session stops an incumbent session on the same target."""
    # 1. Create and start Session A
    session_a = await session_manager.create(source_id="src_1", target_id="tgt_1")
    await session_manager.start_session(session_a.session_id)
    assert session_a.state == SessionState.PLAYING

    # 2. Create Session B for same target but different source
    # First mock source_2
    cast(MagicMock, session_manager._source_registry.resolve_source).side_effect = lambda sid: MagicMock(
        source=SourceDescriptor(
            source_id=sid,
            source_type=SourceType.SYNTHETIC_TEST_SOURCE,
            display_name=f"Source {sid}",
            platform="linux",
            capabilities=SourceCapabilities(),
        ),
        adapter_info=MagicMock(adapter=MagicMock()),
    )

    session_b = await session_manager.create(source_id="src_2", target_id="tgt_1")
    assert session_b.session_id != session_a.session_id
    assert session_b.state == SessionState.CREATED

    # 3. Start Session B - should stop Session A
    success = await session_manager.start_session(session_b.session_id)
    assert success is True
    assert session_b.state == SessionState.PLAYING  # type: ignore[comparison-overlap]
    assert session_a.state == SessionState.STOPPED


@pytest.mark.asyncio
async def test_target_exclusivity_at_create(session_manager: SessionManager) -> None:
    """Test that exclusive=True still fails at create time if target is busy."""
    # 1. Create and start Session A
    session_a = await session_manager.create(source_id="src_1", target_id="tgt_1")
    await session_manager.start_session(session_a.session_id)

    # 2. Try to create Session B with exclusive=True
    with pytest.raises(SessionConflictError):
        await session_manager.create(source_id="src_2", target_id="tgt_1", exclusive=True)


@pytest.mark.asyncio
async def test_session_reuse_idempotency(session_manager: SessionManager) -> None:
    """Test that create() returns existing session if source and target match."""
    session_a = await session_manager.create(source_id="src_1", target_id="tgt_1")

    # Create again with same source/target
    session_b = await session_manager.create(source_id="src_1", target_id="tgt_1")

    assert session_a.session_id == session_b.session_id


@pytest.mark.asyncio
async def test_start_session_ignores_failed_incumbent(session_manager: SessionManager) -> None:
    """Test that start_session doesn't explicitly stop FAILED incumbents because arbitration only targets active ones."""
    # Create Session A and fail it
    session_a = await session_manager.create(source_id="src_1", target_id="tgt_1")
    session_manager.update_state(session_a.session_id, SessionState.FAILED)

    # Mock source_2
    cast(MagicMock, session_manager._source_registry.resolve_source).side_effect = lambda sid: MagicMock(
        source=SourceDescriptor(
            source_id=sid,
            source_type=SourceType.SYNTHETIC_TEST_SOURCE,
            display_name=f"Source {sid}",
            platform="linux",
            capabilities=SourceCapabilities(),
        ),
        adapter_info=MagicMock(adapter=MagicMock()),
    )

    session_b = await session_manager.create(source_id="src_2", target_id="tgt_1")

    with patch.object(session_manager, "stop_session", wraps=session_manager.stop_session) as stop_spy:
        await session_manager.start_session(session_b.session_id)
        # Should NOT have called stop_session for session_a because it was already FAILED
        # (It would only call it if it was not in STOPPED or FAILED)
        assert session_a.session_id not in [call.args[0] for call in stop_spy.call_args_list]

@pytest.mark.asyncio
async def test_incumbent_exclusivity_blocks_new_session(session_manager: SessionManager) -> None:
    """Test that an existing exclusive session blocks creation of a new non-exclusive session."""
    # 1. Create exclusive Session A
    session_a = await session_manager.create(source_id="src_1", target_id="tgt_1", exclusive=True)
    assert session_a.exclusive is True

    # 2. Try to create Session B (non-exclusive) for same target
    with pytest.raises(SessionConflictError):
        await session_manager.create(source_id="src_2", target_id="tgt_1", exclusive=False)
