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
    registry.prepare_target = MagicMock(return_value=asyncio.Future())
    registry.prepare_target.return_value.set_result({"success": True})
    registry.play_stream = MagicMock(return_value=asyncio.Future())
    registry.play_stream.return_value.set_result({"success": True})
    registry.stop_target = MagicMock(return_value=asyncio.Future())
    registry.stop_target.return_value.set_result({"success": True})
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
    # 1. Register a Windows adapter
    win_adapter = MagicMock()
    source_registry.register_adapter(
        adapter_id="win_adapter",
        platform="windows",
        version="1.0.0",
        capabilities=AdapterCapabilities(supports_system_audio=True),
        sources=[
            SourceDescriptor(
                source_id="win_source",
                source_type=SourceType.SYSTEM_AUDIO,
                display_name="Windows Audio",
                platform="windows",
                capabilities=SourceCapabilities(),
            )
        ],
        adapter_instance=win_adapter,
    )

    # 2. Register a Linux adapter
    linux_adapter = MagicMock()
    source_registry.register_adapter(
        adapter_id="linux_adapter",
        platform="linux",
        version="1.0.0",
        capabilities=AdapterCapabilities(supports_system_audio=True),
        sources=[
            SourceDescriptor(
                source_id="linux_source",
                source_type=SourceType.SYSTEM_AUDIO,
                display_name="Linux Audio",
                platform="linux",
                capabilities=SourceCapabilities(),
            )
        ],
        adapter_instance=linux_adapter,
    )

    # 3. Create a session that tries to use Windows source with Linux adapter
    # We need to manually manipulate the registry or simulate the situation.
    # Actually, the registry knows which adapter owns which source.
    # The mismatch we want to test is: a source labeled "windows" being handled by a "linux" adapter.
    # In our current implementation, _get_adapter_info_for_source(source_id) returns the adapter that registered it.
    # So if we want a mismatch, we need to register a "windows" source to a "linux" adapter.

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
                platform="windows",  # Label says Windows
                capabilities=SourceCapabilities(),
            )
        ],
        adapter_instance=linux_adapter, # Instance is Linux
    )

    # 4. Attempt to start session
    session = session_manager.create(source_id="mismatched_source", target_id="target1")
    success = await session_manager.start_session(session.session_id)

    assert not success
    # The session might end up in STOPPED after cleanup in stop_session
    assert session.state in [SessionState.FAILED, SessionState.STOPPED]
    assert session.last_error is not None
    assert session.last_error.code == "source_adapter_platform_mismatch"
    assert "Windows source (mismatched_source) was bound to Linux audio adapter." in session.last_error.message


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
    sm.resolve_ffmpeg_path = MagicMock(return_value="/usr/bin/ffmpeg")

    # 3. Create and start session
    _ = session_manager.create(source_id="any_source", target_id="target1")

    # We mock more things to let start_session succeed until frame ingestion check
    session_manager._stream_publisher = MagicMock()
    session_manager._stream_publisher.get_stream_url.return_value = "http://localhost/stream"

    # Actually, we just care if it passes the platform validation.
    # platform validation is at the very beginning of prepare_source.

    res = source_registry.prepare_source("any_source")
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
    assert s.platform == "linux"

    # Verify it's in the to_dict/model_dump
    d = s.model_dump()
    assert d["adapter_id"] == "test_adapter"
    assert d["platform"] == "linux"
