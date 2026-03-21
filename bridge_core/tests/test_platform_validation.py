import asyncio
from unittest.mock import MagicMock

import pytest
from bridge_core.core.event_bus import EventBus
from bridge_core.core.session_manager import SessionManager, SessionState
from bridge_core.core.source_registry import SourceRegistry
from bridge_core.core.target_registry import TargetRegistry
from ingress_sdk.types import AdapterCapabilities, SourceCapabilities, SourceDescriptor, SourceType


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture
def source_registry(event_bus: EventBus) -> SourceRegistry:
    return SourceRegistry(event_bus=event_bus)


@pytest.fixture
def target_registry(event_bus: EventBus) -> TargetRegistry:
    registry = TargetRegistry(event_bus=event_bus)
    # Mock some methods to avoid external dependencies
    setattr(registry, "prepare_target", MagicMock(return_value=asyncio.Future()))
    getattr(registry, "prepare_target").return_value.set_result({"success": True})
    setattr(registry, "play_stream", MagicMock(return_value=asyncio.Future()))
    getattr(registry, "play_stream").return_value.set_result({"success": True})
    setattr(registry, "stop_target", MagicMock(return_value=asyncio.Future()))
    getattr(registry, "stop_target").return_value.set_result({"success": True})
    return registry


@pytest.fixture
def session_manager(event_bus: EventBus, source_registry: SourceRegistry, target_registry: TargetRegistry) -> SessionManager:
    return SessionManager(
        event_bus=event_bus,
        source_registry=source_registry,
        target_registry=target_registry,
    )


@pytest.mark.asyncio
async def test_platform_mismatch_fails(session_manager: SessionManager, source_registry: SourceRegistry) -> None:
    linux_adapter = MagicMock()
    with pytest.raises(ValueError, match="does not match source platform"):
        source_registry.register_adapter(
            adapter_id="mismatched_adapter",
            platform="linux",
            version="1.0.0",
            capabilities=AdapterCapabilities(supports_system_audio=True),
            sources=[
                SourceDescriptor(
                    source_id="mismatched_source",
                    source_type=SourceType.SYSTEM_AUDIO,
                    display_name="Mismatched Source",
                    platform="windows",
                    capabilities=SourceCapabilities(),
                )
            ],
            adapter_instance=linux_adapter,
        )

    session = session_manager.create(source_id="mismatched_adapter:system:mismatched_source", target_id="target1")
    success = await session_manager.start_session(session.session_id)

    assert not success
    assert session.state in [SessionState.FAILED, SessionState.STOPPED]
    assert session.last_error is not None
    assert session.last_error.code == "source_start_failed"
    assert "Source not found" in session.last_error.message


@pytest.mark.asyncio
async def test_platform_any_works(session_manager: SessionManager, source_registry: SourceRegistry) -> None:
    # 1. Register a Linux adapter with an "any" source
    linux_adapter = MagicMock()
    # Mock prepare and start to return success
    linux_adapter.prepare.return_value = MagicMock(success=True)
    linux_adapter.start.return_value = MagicMock(success=True, session_id="test_sess")

    source_registry.register_adapter(
        adapter_id="linux_adapter",
        platform="linux",
        version="1.0.0",
        capabilities=AdapterCapabilities(supports_synthetic_test_source=True),
        sources=[
            SourceDescriptor(
                source_id="any_source",
                source_type=SourceType.SYNTHETIC_TEST_SOURCE,
                display_name="Any Audio",
                platform="any",
                capabilities=SourceCapabilities(),
            )
        ],
        adapter_instance=linux_adapter,
    )

    # 2. Mock FFmpeg resolution to avoid failure
    import bridge_core.core.session_manager as sm

    sm.resolve_ffmpeg_path = MagicMock(return_value="/usr/bin/ffmpeg")  # type: ignore[attr-defined]

    # 3. Create and start session
    _ = session_manager.create(source_id="linux_adapter:synthetic:any_source", target_id="target1")

    # We mock more things to let start_session succeed until frame ingestion check
    session_manager._stream_publisher = MagicMock()
    session_manager._stream_publisher.get_stream_url.return_value = "http://localhost/stream"

    # Actually, we just care if it passes the platform validation.
    # platform validation is at the very beginning of prepare_source.

    res = source_registry.prepare_source("linux_adapter:synthetic:any_source")
    assert res.success
    assert res.code != "source_adapter_platform_mismatch"

@pytest.mark.asyncio
async def test_source_listing_includes_extra_fields(session_manager: SessionManager, source_registry: SourceRegistry) -> None:
    source_registry.register_adapter(
        adapter_id="test_adapter",
        platform="linux",
        version="1.0.0",
        capabilities=AdapterCapabilities(),
        sources=[
            SourceDescriptor(
                source_id="test_source",
                source_type=SourceType.SYSTEM_AUDIO,
                display_name="Test Source",
                platform="linux",
                capabilities=SourceCapabilities(),
            )
        ],
    )

    sources = source_registry.list_sources()
    assert len(sources) == 1
    s = sources[0]
    assert s.adapter_id == "test_adapter"
    assert s.local_source_id == "test_source"
    assert s.platform == "linux"
    assert s.source_id == "test_adapter:system:test_source"

    # Verify it's in the to_dict/model_dump
    d = s.model_dump()
    assert d["adapter_id"] == "test_adapter"
    assert d["local_source_id"] == "test_source"
    assert d["platform"] == "linux"


@pytest.mark.asyncio
async def test_overlapping_local_ids_route_to_correct_adapter(source_registry: SourceRegistry) -> None:
    windows_adapter = MagicMock()
    windows_adapter.prepare.return_value = MagicMock(success=True, source_id="default")
    windows_adapter.start.return_value = MagicMock(success=True, session_id="win-session")

    linux_adapter = MagicMock()
    linux_adapter.prepare.return_value = MagicMock(success=True, source_id="default")
    linux_adapter.start.return_value = MagicMock(success=True, session_id="linux-session")

    source_registry.register_adapter(
        adapter_id="windows-audio-adapter",
        platform="windows",
        version="1.0.0",
        capabilities=AdapterCapabilities(supports_system_audio=True),
        sources=[
            SourceDescriptor(
                source_id="default",
                source_type=SourceType.SYSTEM_AUDIO,
                display_name="Windows Default",
                platform="windows",
                capabilities=SourceCapabilities(),
            )
        ],
        adapter_instance=windows_adapter,
    )
    source_registry.register_adapter(
        adapter_id="linux-audio-adapter",
        platform="linux",
        version="1.0.0",
        capabilities=AdapterCapabilities(supports_system_audio=True),
        sources=[
            SourceDescriptor(
                source_id="default",
                source_type=SourceType.SYSTEM_AUDIO,
                display_name="Linux Default",
                platform="linux",
                capabilities=SourceCapabilities(),
            )
        ],
        adapter_instance=linux_adapter,
    )

    source_registry.prepare_source("windows-audio-adapter:system:default")
    source_registry.start_source("windows-audio-adapter:system:default", MagicMock())
    source_registry.prepare_source("linux-audio-adapter:system:default")
    source_registry.start_source("linux-audio-adapter:system:default", MagicMock())

    windows_adapter.prepare.assert_called_once_with("default")
    windows_adapter.start.assert_called_once()
    linux_adapter.prepare.assert_called_once_with("default")
    linux_adapter.start.assert_called_once()
