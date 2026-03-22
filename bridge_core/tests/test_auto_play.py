"""Tests for AutoPlayController."""

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bridge_core.core.auto_play import AutoPlayController
from bridge_core.core.config_store import ConfigStore
from bridge_core.core.event_bus import EventBus, EventType
from bridge_core.core.session_manager import STOP_REASON_PREFERRED, SessionManager
from bridge_core.core.source_registry import SourceRegistry
from bridge_core.core.target_registry import TargetRegistry


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture
def config_store(tmp_path: Path) -> ConfigStore:
    return ConfigStore(db_path=tmp_path / "config.db")


@pytest.fixture
def source_registry() -> MagicMock:
    registry = MagicMock(spec=SourceRegistry)
    registry.list_sources.return_value = []
    return registry


@pytest.fixture
def target_registry() -> MagicMock:
    registry = MagicMock(spec=TargetRegistry)
    target1 = MagicMock()
    target1.target_id = "tgt_1"
    target2 = MagicMock()
    target2.target_id = "tgt_2"
    registry.list_targets.return_value = [target1, target2]
    return registry


@pytest.fixture
def session_manager(
    event_bus: EventBus,
    source_registry: MagicMock,
    target_registry: MagicMock,
    config_store: ConfigStore,
) -> MagicMock:
    manager = MagicMock(spec=SessionManager)
    manager._source_registry = source_registry
    manager._config_store = config_store

    # Mock create to return a session
    async def mock_create(**kwargs: Any) -> MagicMock:
        session = MagicMock()
        session.session_id = "sess_123"
        return session

    manager.create = AsyncMock(side_effect=mock_create)
    manager.start_session = AsyncMock(return_value=True)
    manager.play = AsyncMock()

    return manager


@pytest.fixture
def auto_play_controller(
    event_bus: EventBus,
    session_manager: MagicMock,
    target_registry: MagicMock,
) -> AutoPlayController:
    return AutoPlayController(event_bus, session_manager, target_registry)


@pytest.mark.asyncio
async def test_autoplay_triggers_on_bluetooth_source_available(
    event_bus: EventBus,
    source_registry: MagicMock,
    session_manager: MagicMock,
    auto_play_controller: AutoPlayController,
) -> None:
    """Test that AutoPlayController reacts to the event and starts a session."""
    source_id = "local_bt_1"
    canonical_id = "bluetooth:mac_1"

    mock_source = MagicMock()
    mock_source.source_id = canonical_id
    mock_source.adapter_id = "linux-bluetooth-adapter"
    mock_source.local_source_id = source_id
    source_registry.list_sources.return_value = [mock_source]

    # Emit event
    event_bus.emit(EventType.BLUETOOTH_SOURCE_AVAILABLE, payload={"source_id": source_id})

    # Wait for async tasks
    await asyncio.sleep(0.5)

    # Verify canonical play path is used
    session_manager.play.assert_called_once()
    args, kwargs = session_manager.play.call_args
    assert kwargs["source_id"] == canonical_id
    assert kwargs["target_id"] is None
    assert kwargs["conflict_policy"] == "takeover"
    assert kwargs["takeover_reason"] == STOP_REASON_PREFERRED


@pytest.mark.asyncio
async def test_session_manager_resolves_preferred_target(
    event_bus: EventBus,
    source_registry: MagicMock,
    target_registry: MagicMock,
    config_store: ConfigStore,
) -> None:
    """Test that SessionManager.play resolves target_id=None using preferred_target_id."""
    manager = SessionManager(
        event_bus=event_bus,
        source_registry=source_registry,
        target_registry=target_registry,
        stream_publisher=MagicMock(),
        config_store=config_store,
    )

    preferred_target = "tgt_preferred"
    config_store.set("preferred_target_id", preferred_target)

    with (
        patch.object(manager, "create", new_callable=AsyncMock) as mock_create,
        patch.object(manager, "start_session", new_callable=AsyncMock),
    ):
        mock_create.return_value = MagicMock(session_id="sess_123")

        await manager.play(source_id="src_1", target_id=None)

        mock_create.assert_called_once()
        args, kwargs = mock_create.call_args
        assert kwargs["target_id"] == preferred_target


@pytest.mark.asyncio
async def test_session_manager_uses_deterministic_fallback(
    event_bus: EventBus,
    source_registry: MagicMock,
    target_registry: MagicMock,
    config_store: ConfigStore,
) -> None:
    """Test that SessionManager.play uses a deterministic fallback if no preferred target is set."""
    manager = SessionManager(
        event_bus=event_bus,
        source_registry=source_registry,
        target_registry=target_registry,
        stream_publisher=MagicMock(),
        config_store=config_store,
    )

    # No preferred target set

    target_b = MagicMock()
    target_b.target_id = "tgt_b"
    target_a = MagicMock()
    target_a.target_id = "tgt_a"
    target_registry.list_targets.return_value = [target_b, target_a]

    with (
        patch.object(manager, "create", new_callable=AsyncMock) as mock_create,
        patch.object(manager, "start_session", new_callable=AsyncMock),
    ):
        mock_create.return_value = MagicMock(session_id="sess_123")

        await manager.play(source_id="src_1", target_id=None)

        mock_create.assert_called_once()
        args, kwargs = mock_create.call_args
        # Should pick tgt_a because it comes first alphabetically
        assert kwargs["target_id"] == "tgt_a"
