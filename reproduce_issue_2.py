import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from bridge_core.core.session_manager import SessionManager
from bridge_core.core.event_bus import EventBus
from bridge_core.core.source_registry import SourceBinding, SourceRegistry
from bridge_core.core.target_registry import TargetRegistry
from ingress_sdk.types import SourceCapabilities, SourceDescriptor, SourceType, StartResult, PrepareResult

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

async def main():
    event_bus = EventBus()
    source_registry = MagicMock(spec=SourceRegistry)
    source_registry.prepare_source.return_value = PrepareResult(success=True, source_id="src_1")
    source_registry.start_source.return_value = StartResult(success=True, session_id="adapter_sess_1")

    source_desc = SourceDescriptor(
        source_id="src_1",
        source_type=SourceType.SYSTEM_AUDIO,
        display_name="Source 1",
        platform="linux",
        capabilities=SourceCapabilities(codecs=["pcm_s16le", "mp3", "aac"]),
    )

    source_registry.resolve_source.return_value = SourceBinding(
        source=source_desc,
        adapter_info=MagicMock(),
        local_source_id="src_1"
    )

    target_registry = MagicMock(spec=TargetRegistry)
    target_registry.get_target.return_value = MockTargetDescriptor("tgt_1")
    target_registry.prepare_target = AsyncMock(return_value={"success": True})
    target_registry.play_stream = AsyncMock(return_value={"success": True})

    manager = SessionManager(
        event_bus=event_bus,
        source_registry=source_registry,
        target_registry=target_registry,
        stream_publisher=MagicMock(),
    )

    session = manager.create(source_id="src_1", target_id="tgt_1", stream_profile="auto")
    print(f"Requested: {session.requested_stream_profile}")

    with patch("bridge_core.core.session_manager.StreamPipeline") as mock_pipeline_cls,          patch("bridge_core.core.session_manager.resolve_ffmpeg_path", return_value="/usr/bin/ffmpeg"):
        mock_pipeline = mock_pipeline_cls.return_value
        mock_pipeline.start = AsyncMock()
        mock_pipeline.get_diagnostics_snapshot.return_value = {}

        success = await manager.start_session(session.session_id)
        print(f"Success: {success}")
        print(f"Effective: {session.effective_stream_profile}")

if __name__ == "__main__":
    asyncio.run(main())
