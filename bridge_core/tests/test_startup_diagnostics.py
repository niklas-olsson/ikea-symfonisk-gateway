"""Tests for improved session startup diagnostics."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bridge_core.core.errors import (
    MEDIA_ENGINE_NOT_FOUND,
    RENDERER_PLAYBACK_FAILED,
    SOURCE_START_FAILED,
    WINDOWS_LOOPBACK_CAPTURE_STALLED,
    WINDOWS_OUTPUT_DEVICE_SILENT,
)
from bridge_core.core.event_bus import EventBus
from bridge_core.core.session_manager import SessionManager, SessionState
from ingress_sdk.types import SourceCapabilities, SourceDescriptor, SourceType


@pytest.fixture
def mock_event_bus():
    return MagicMock(spec=EventBus)


@pytest.fixture
def mock_source_registry():
    registry = MagicMock()
    registry.prepare_source.return_value = MagicMock(success=True)
    registry.start_source.return_value = MagicMock(success=True, session_id="adapter_sess_1")
    registry.probe_source_health.return_value = None
    return registry


@pytest.fixture
def mock_target_registry():
    registry = MagicMock()
    registry.prepare_target = AsyncMock(return_value={"success": True})
    registry.play_stream = AsyncMock(return_value={"success": True})
    registry.stop_target = AsyncMock(return_value={"success": True})
    return registry


@pytest.fixture
def session_manager(mock_event_bus, mock_source_registry, mock_target_registry):
    return SessionManager(
        event_bus=mock_event_bus,
        source_registry=mock_source_registry,
        target_registry=mock_target_registry,
        stream_publisher=MagicMock(),
        config_store=MagicMock(),
    )


@pytest.mark.asyncio
async def test_startup_failed_media_engine_not_found(session_manager, mock_source_registry):
    session = session_manager.create("source_1", "target_1")

    with patch("bridge_core.core.session_manager.resolve_ffmpeg_path") as mock_resolve:
        mock_resolve.side_effect = RuntimeError("FFmpeg not found")

        success = await session_manager.start_session(session.session_id)

        assert success is False
        # stop_session is called on failure, which transitions to STOPPED
        assert session.state == SessionState.STOPPED
        assert session.last_error is not None
        assert session.last_error.code == MEDIA_ENGINE_NOT_FOUND
        assert "FFmpeg not found" in session.last_error.message


@pytest.mark.asyncio
async def test_startup_failed_source_prepare(session_manager, mock_source_registry):
    session = session_manager.create("source_1", "target_1")
    mock_source_registry.prepare_source.return_value = MagicMock(success=False, error="Permission denied", code=None)

    with patch("bridge_core.core.session_manager.resolve_ffmpeg_path", return_value="/usr/bin/ffmpeg"):
        success = await session_manager.start_session(session.session_id)

        assert success is False
        assert session.last_error.code == SOURCE_START_FAILED
        assert "Permission denied" in session.last_error.message


@pytest.mark.asyncio
async def test_startup_failed_renderer_playback(session_manager, mock_target_registry):
    session = session_manager.create("source_1", "target_1")
    # Need to mock StreamPipeline so it doesn't actually try to start subprocesses
    with (
        patch("bridge_core.core.session_manager.StreamPipeline") as mock_pipeline_cls,
        patch("bridge_core.core.session_manager.resolve_ffmpeg_path", return_value="/usr/bin/ffmpeg"),
    ):
        mock_pipeline = mock_pipeline_cls.return_value
        mock_pipeline.start = AsyncMock()
        mock_pipeline.stop = AsyncMock()
        mock_pipeline.jitter_buffer = MagicMock()
        mock_pipeline.jitter_buffer.size_ms = 10.0  # Pretend we have frames

        mock_target_registry.play_stream.return_value = {"success": False, "error": "Connection reset"}

        success = await session_manager.start_session(session.session_id)

        assert success is False
        assert session.last_error.code == RENDERER_PLAYBACK_FAILED
        assert "Connection reset" in session.last_error.message


@pytest.mark.asyncio
async def test_startup_failed_frame_ingest_timeout(session_manager, mock_target_registry):
    session = session_manager.create("source_1", "target_1")

    with (
        patch("bridge_core.core.session_manager.StreamPipeline") as mock_pipeline_cls,
        patch("bridge_core.core.session_manager.resolve_ffmpeg_path", return_value="/usr/bin/ffmpeg"),
    ):
        mock_pipeline = mock_pipeline_cls.return_value
        mock_pipeline.start = AsyncMock()
        mock_pipeline.stop = AsyncMock()
        mock_pipeline.jitter_buffer = MagicMock()
        mock_pipeline.jitter_buffer.size_ms = 0.0  # No frames arriving

        # Speed up the test by patching asyncio.sleep
        with patch("asyncio.sleep", return_value=None):
            success = await session_manager.start_session(session.session_id)

            assert success is True
            assert session.last_error is None
            mock_target_registry.prepare_target.assert_awaited_once()
            mock_target_registry.play_stream.assert_awaited_once()


@pytest.mark.asyncio
async def test_windows_system_output_silent_startup_proceeds(session_manager, mock_source_registry, mock_target_registry):
    session = session_manager.create("windows-audio-adapter:system:default", "target_1")
    mock_source_registry.resolve_source.return_value = MagicMock(
        source=SourceDescriptor(
            source_id="windows-audio-adapter:system:default",
            source_type=SourceType.SYSTEM_OUTPUT,
            display_name="Default System Sound (windows)",
            platform="windows",
            capabilities=SourceCapabilities(),
        ),
        adapter_info=MagicMock(adapter=MagicMock()),
    )
    mock_source_registry.probe_source_health.return_value = MagicMock(
        healthy=True,
        signal_present=False,
        source_state="healthy_but_idle",
        details={
            "startup_substate": "healthy_but_idle",
            "callback_count": 1,
            "non_empty_buffer_count": 1,
            "samples_received": 960,
            "frames_emitted": 1,
            "first_callback_at": 123.4,
            "selected_host_api": {"name": "Windows WASAPI", "index": 0},
            "default_render_device": {"name": "Speakers", "index": 2},
            "loopback_device": {"name": "Speakers (loopback)", "index": 7},
            "start_viability": {
                "stream_opened": True,
                "stream_started": True,
                "callback_registered": True,
            },
        },
    )
    mock_source_registry.start_source.return_value = MagicMock(success=True, session_id="adapter_sess_1", backend="pyaudiowpatch")

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

        success = await session_manager.start_session(session.session_id)

        assert success is True
        assert session.state == SessionState.PLAYING
        assert session.last_error is not None
        assert session.last_error.code == WINDOWS_OUTPUT_DEVICE_SILENT
        assert session.last_error.details is not None
        assert session.last_error.details["startup_substate"] == "healthy_but_idle"
        mock_target_registry.prepare_target.assert_awaited_once()
        mock_target_registry.play_stream.assert_awaited_once()


@pytest.mark.asyncio
async def test_windows_system_output_stalled_capture_fails(session_manager, mock_source_registry, mock_target_registry):
    session = session_manager.create("windows-audio-adapter:system:default", "target_1")
    mock_source_registry.resolve_source.return_value = MagicMock(
        source=SourceDescriptor(
            source_id="windows-audio-adapter:system:default",
            source_type=SourceType.SYSTEM_OUTPUT,
            display_name="Default System Sound (windows)",
            platform="windows",
            capabilities=SourceCapabilities(),
        ),
        adapter_info=MagicMock(adapter=MagicMock()),
    )
    mock_source_registry.probe_source_health.return_value = MagicMock(
        healthy=False,
        signal_present=False,
        source_state="stream_started_no_callbacks",
        details={
            "startup_substate": "stream_started_no_callbacks",
            "callback_count": 0,
            "non_empty_buffer_count": 0,
            "samples_received": 0,
            "frames_emitted": 0,
            "first_callback_at": None,
            "selected_host_api": {"name": "Windows WASAPI", "index": 0},
            "default_render_device": {"name": "Speakers", "index": 2},
            "loopback_device": {"name": "Speakers (loopback)", "index": 7},
            "start_viability": {
                "stream_opened": True,
                "stream_started": True,
                "callback_registered": True,
            },
        },
    )
    mock_source_registry.start_source.return_value = MagicMock(success=True, session_id="adapter_sess_1", backend="pyaudiowpatch")

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

        success = await session_manager.start_session(session.session_id)

        assert success is False
        assert session.last_error is not None
        assert session.last_error.code == WINDOWS_LOOPBACK_CAPTURE_STALLED
        assert session.last_error.details is not None
        assert session.last_error.details["startup_substate"] == "stream_started_no_callbacks"
        mock_target_registry.prepare_target.assert_not_awaited()
        mock_target_registry.play_stream.assert_not_awaited()
