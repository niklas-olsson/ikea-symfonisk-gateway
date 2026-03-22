"""Tests for incumbent session resolution rules."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bridge_core.adapters.base import OwnershipResult, OwnershipStatus
from bridge_core.core.event_bus import EventBus
from bridge_core.core.session_manager import SessionManager, SessionState
from bridge_core.core.source_registry import SourceRegistry
from bridge_core.core.target_registry import TargetRegistry
from bridge_core.core.errors import SessionConflictError


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture
def source_registry() -> MagicMock:
    registry = MagicMock(spec=SourceRegistry)
    registry.get_source_health.return_value = None
    return registry


@pytest.fixture
def target_registry() -> MagicMock:
    registry = MagicMock(spec=TargetRegistry)
    registry.get_adapter_for_target.return_value = MagicMock()
    registry.get_adapter_for_target.return_value.inspect_ownership = AsyncMock(
        return_value=OwnershipResult(OwnershipStatus.OWNED)
    )
    registry.stop_target = AsyncMock(return_value={"success": True})
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
async def test_same_source_idempotency(session_manager: SessionManager) -> None:
    """same target + same source + active state: return existing session idempotently."""
    s1 = await session_manager.create(source_id="src_1", target_id="tgt_1")
    session_manager.update_state(s1.session_id, SessionState.PLAYING)

    s2 = await session_manager.create(source_id="src_1", target_id="tgt_1")
    assert s1.session_id == s2.session_id
    assert s2.state == SessionState.PLAYING


@pytest.mark.asyncio
async def test_same_source_quiesced_resume(session_manager: SessionManager) -> None:
    """same target + same source + quiesced: resume that session."""
    s1 = await session_manager.create(source_id="src_1", target_id="tgt_1")
    session_manager.update_state(s1.session_id, SessionState.QUIESCED)

    with patch.object(session_manager, "resume_session", AsyncMock(return_value=True)) as mock_resume:
        s2 = await session_manager.create(source_id="src_1", target_id="tgt_1")
        assert s1.session_id == s2.session_id
        mock_resume.assert_awaited_once_with(s1.session_id, force_reclaim=True)


@pytest.mark.asyncio
async def test_same_source_degraded_restart(session_manager: SessionManager) -> None:
    """same target + same source + degraded: stop session (caller restarts)."""
    s1 = await session_manager.create(source_id="src_1", target_id="tgt_1")
    session_manager.update_state(s1.session_id, SessionState.DEGRADED)

    with patch.object(session_manager, "stop_session", AsyncMock(return_value=True)) as mock_stop:
        s2 = await session_manager.create(source_id="src_1", target_id="tgt_1")
        assert s1.session_id == s2.session_id
        mock_stop.assert_awaited_once_with(s1.session_id)


@pytest.mark.asyncio
async def test_different_source_takeover(session_manager: SessionManager) -> None:
    """same target + different source + takeover: stop incumbent, supersede, return new session."""
    s1 = await session_manager.create(source_id="src_1", target_id="tgt_1")
    session_manager.update_state(s1.session_id, SessionState.PLAYING)

    with patch.object(session_manager, "stop_session", AsyncMock(return_value=True)) as mock_stop:
        s2 = await session_manager.create(source_id="src_2", target_id="tgt_1", takeover=True)
        assert s1.session_id != s2.session_id
        assert s1.state == SessionState.SUPERSEDED
        mock_stop.assert_awaited_once_with(s1.session_id)


@pytest.mark.asyncio
async def test_different_source_reject(session_manager: SessionManager) -> None:
    """same target + different source + reject: raise SessionConflictError."""
    s1 = await session_manager.create(source_id="src_1", target_id="tgt_1")
    session_manager.update_state(s1.session_id, SessionState.PLAYING)

    with pytest.raises(SessionConflictError):
        await session_manager.create(source_id="src_2", target_id="tgt_1", takeover=False)


@pytest.mark.asyncio
async def test_stale_incumbent_ownership_reclaim(session_manager: SessionManager, target_registry: MagicMock) -> None:
    """stale incumbent ownership: reclaim target, clean up stale session, proceed with new session."""
    s1 = await session_manager.create(source_id="src_1", target_id="tgt_1")
    session_manager.update_state(s1.session_id, SessionState.PLAYING)

    # Mock ownership as NOT_OWNED (stale)
    target_registry.get_adapter_for_target.return_value.inspect_ownership.return_value = OwnershipResult(
        OwnershipStatus.NOT_OWNED
    )

    with patch.object(session_manager, "stop_session", AsyncMock(return_value=True)) as mock_stop:
        s2 = await session_manager.create(source_id="src_2", target_id="tgt_1")
        assert s1.session_id != s2.session_id
        assert s1.state == SessionState.SUPERSEDED
        mock_stop.assert_awaited_once_with(s1.session_id)
