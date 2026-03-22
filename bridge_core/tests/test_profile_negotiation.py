"""Tests for stream profile negotiation and fallback."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bridge_core.core.event_bus import EventBus
from bridge_core.core.session_manager import SessionManager, SessionState
from bridge_core.core.source_registry import SourceRegistry
from bridge_core.core.target_registry import TargetRegistry
from bridge_core.stream.pipeline import StreamPipeline
from ingress_sdk.types import PrepareResult, SourceCapabilities, SourceDescriptor, SourceType, StartResult


class MockTargetDescriptor:
    def __init__(self, tid, codecs=None):
        self.target_id = tid
        self.renderer = "mock"
        self.target_type = "speaker"
        self.display_name = "Mock Target"
        self.members = [tid]
        self.coordinator_id = tid
        self.supported_codecs = codecs or ["mp3", "aac", "pcm_s16le"]
        self.supported_sample_rates = [48000]
        self.supported_channels = [2]
        self.max_bitrate_kbps = None


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
            source_type=SourceType.SYSTEM_AUDIO,
            display_name="Source 1",
            platform="linux",
            capabilities=SourceCapabilities(codecs=["pcm_s16le", "mp3", "aac"]),
        )
    )
    return registry


@pytest.fixture
def target_registry() -> MagicMock:
    registry = MagicMock(spec=TargetRegistry)
    registry.get_target.return_value = MockTargetDescriptor("tgt_1")
    registry.prepare_target = AsyncMock(return_value={"success": True})
    registry.play_stream = AsyncMock(return_value={"success": True})
    registry.stop_target = AsyncMock(return_value={"success": True})
    return registry


@pytest.fixture
def config_store() -> MagicMock:
    store = MagicMock()
    store.get.return_value = None
    return store


@pytest.fixture
def session_manager(
    event_bus: EventBus,
    source_registry: MagicMock,
    target_registry: MagicMock,
    config_store: MagicMock,
) -> SessionManager:
    return SessionManager(
        event_bus=event_bus,
        source_registry=source_registry,
        target_registry=target_registry,
        config_store=config_store,
        stream_publisher=MagicMock(),
    )


@pytest.mark.asyncio
async def test_negotiate_highest_viable_profile(session_manager, target_registry):
    """Test that 'auto' picks the highest profile supported by both."""
    session = session_manager.create(source_id="src_1", target_id="tgt_1", stream_profile="auto")

    with patch("bridge_core.core.session_manager.StreamPipeline") as mock_pipeline_cls, \
         patch("bridge_core.core.session_manager.resolve_ffmpeg_path", return_value="/usr/bin/ffmpeg"):
        mock_pipeline = mock_pipeline_cls.return_value
        mock_pipeline.start = AsyncMock()

        success = await session_manager.start_session(session.session_id)

    assert success is True
    assert session.effective_stream_profile == "pcm_wav_48k_stereo_16"


@pytest.mark.asyncio
async def test_negotiate_restricted_target(session_manager, target_registry):
    """Test negotiation when target only supports a lower quality codec."""
    target_registry.get_target.return_value = MockTargetDescriptor("tgt_1", codecs=["mp3"])
    session = session_manager.create(source_id="src_1", target_id="tgt_1", stream_profile="auto")

    with patch("bridge_core.core.session_manager.StreamPipeline") as mock_pipeline_cls, \
         patch("bridge_core.core.session_manager.resolve_ffmpeg_path", return_value="/usr/bin/ffmpeg"):
        mock_pipeline = mock_pipeline_cls.return_value
        mock_pipeline.start = AsyncMock()

        success = await session_manager.start_session(session.session_id)

    assert success is True
    assert session.effective_stream_profile == "mp3_48k_stereo_320"


@pytest.mark.asyncio
async def test_fallback_ladder_on_failure(session_manager):
    """Test that if the highest profile fails, it tries the next one."""
    session = session_manager.create(source_id="src_1", target_id="tgt_1", stream_profile="auto")

    # First attempt (PCM) fails at pipeline start, second (AAC) succeeds
    pipeline_pcm = MagicMock(spec=StreamPipeline)
    pipeline_pcm.start = AsyncMock(side_effect=RuntimeError("PCM failed"))
    pipeline_pcm.stop = AsyncMock()

    pipeline_aac = MagicMock(spec=StreamPipeline)
    pipeline_aac.start = AsyncMock()

    with patch("bridge_core.core.session_manager.StreamPipeline", side_effect=[pipeline_pcm, pipeline_aac]), \
         patch("bridge_core.core.session_manager.resolve_ffmpeg_path", return_value="/usr/bin/ffmpeg"):

        success = await session_manager.start_session(session.session_id)

    assert success is True
    assert session.effective_stream_profile == "aac_48k_stereo_256"


@pytest.mark.asyncio
async def test_last_known_good_reuse(session_manager, config_store):
    """Test that LKG profile is prioritized in 'auto' mode."""
    config_store.get.return_value = "aac_48k_stereo_256"
    session = session_manager.create(source_id="src_1", target_id="tgt_1", stream_profile="auto")

    with patch("bridge_core.core.session_manager.StreamPipeline") as mock_pipeline_cls, \
         patch("bridge_core.core.session_manager.resolve_ffmpeg_path", return_value="/usr/bin/ffmpeg"):
        mock_pipeline = mock_pipeline_cls.return_value
        mock_pipeline.start = AsyncMock()

        success = await session_manager.start_session(session.session_id)

    assert success is True
    assert session.effective_stream_profile == "aac_48k_stereo_256"
    assert any(call[0][0] == "lkg_profile:src_1:tgt_1" for call in config_store.get.call_args_list)


@pytest.mark.asyncio
async def test_strict_manual_override_validation(session_manager, target_registry):
    """Test that manual override fails if unsupported, without fallback."""
    target_registry.get_target.return_value = MockTargetDescriptor("tgt_1", codecs=["mp3"])
    # Request PCM when target only supports MP3
    session = session_manager.create(source_id="src_1", target_id="tgt_1", stream_profile="pcm_wav_48k_stereo_16")

    success = await session_manager.start_session(session.session_id)

    assert success is False
    assert "not supported" in session.last_error.message


@pytest.mark.asyncio
async def test_persistence_on_success(session_manager, config_store):
    """Test that successful start persists the profile."""
    session = session_manager.create(source_id="src_1", target_id="tgt_1", stream_profile="auto")

    with patch("bridge_core.core.session_manager.StreamPipeline") as mock_pipeline_cls, \
         patch("bridge_core.core.session_manager.resolve_ffmpeg_path", return_value="/usr/bin/ffmpeg"):
        mock_pipeline = mock_pipeline_cls.return_value
        mock_pipeline.start = AsyncMock()

        await session_manager.start_session(session.session_id)

    config_store.set.assert_called_with("lkg_profile:src_1:tgt_1", "pcm_wav_48k_stereo_16")
