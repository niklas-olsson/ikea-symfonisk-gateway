"""Tests for SessionManager."""

import asyncio

import pytest
from bridge_core.core.event_bus import EventBus, EventType
from bridge_core.core.session_manager import SessionManager, SessionState


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture
def session_manager(event_bus: EventBus) -> SessionManager:
    return SessionManager(event_bus)


@pytest.mark.asyncio
async def test_session_lifecycle(session_manager: SessionManager, event_bus: EventBus) -> None:
    """Test full successful session lifecycle."""
    # 1. Create
    session = session_manager.create(source_id="src_1", target_id="tgt_1")
    assert session.session_id.startswith("sess_")
    assert session.state == SessionState.CREATED
    assert session.source_id == "src_1"
    assert session.target_id == "tgt_1"

    # 2. Transition to READY via PREPARING
    session.transition_to(SessionState.PREPARING)
    session.transition_to(SessionState.READY)
    assert session.state == SessionState.READY  # type: ignore[comparison-overlap]

    # 3. Start
    await session_manager.start_session(session.session_id)
    assert session.state == SessionState.PLAYING
    assert session.started_at is not None

    # 4. Stop
    await session_manager.stop_session(session.session_id)
    assert session.state == SessionState.STOPPED
    assert session.stopped_at is not None

    # 5. Terminate
    session_id = session.session_id
    session_manager.terminate(session_id)
    assert session_manager.get(session_id) is None


@pytest.mark.asyncio
async def test_invalid_transitions(session_manager: SessionManager) -> None:
    """Test that invalid state transitions raise ValueError."""
    session = session_manager.create(source_id="src_1", target_id="tgt_1")

    # Cannot jump from CREATED to PLAYING
    with pytest.raises(ValueError, match="Invalid transition"):
        session.transition_to(SessionState.PLAYING)

    # Cannot jump from CREATED to STOPPED
    with pytest.raises(ValueError, match="Invalid transition"):
        session.transition_to(SessionState.STOPPED)


@pytest.mark.asyncio
async def test_session_recovery(session_manager: SessionManager) -> None:
    """Test session recovery."""
    session = session_manager.create(source_id="src_1", target_id="tgt_1")
    session.transition_to(SessionState.PREPARING)
    session.transition_to(SessionState.READY)
    await session_manager.start_session(session.session_id)

    # Simulate failure
    session.transition_to(SessionState.FAILED)
    assert session.state == SessionState.FAILED

    # Recover
    session_manager.recover(session.session_id)
    assert session.state == SessionState.PLAYING  # type: ignore[comparison-overlap]


@pytest.mark.asyncio
async def test_event_emission(session_manager: SessionManager, event_bus: EventBus) -> None:
    """Test that events are emitted correctly."""
    queue = event_bus.subscribe()

    # Create session
    session = session_manager.create(source_id="src_1", target_id="tgt_1")

    # Wait for event
    event = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert event.type == EventType.SESSION_CREATED
    assert event.session_id == session.session_id
    assert event.payload["source_id"] == "src_1"

    # Start session
    # Note: start() does transitions STARTING -> PLAYING
    session.transition_to(SessionState.PREPARING)
    session.transition_to(SessionState.READY)
    await session_manager.start_session(session.session_id)

    # Check STARTING event
    event = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert event.type == EventType.SESSION_STARTING

    # Check STARTED event
    event = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert event.type == EventType.SESSION_STARTED


@pytest.mark.asyncio
async def test_terminate_playing_session(session_manager: SessionManager) -> None:
    """Test that terminating a playing session stops it first."""
    session = session_manager.create(source_id="src_1", target_id="tgt_1")
    session.transition_to(SessionState.PREPARING)
    session.transition_to(SessionState.READY)
    await session_manager.start_session(session.session_id)

    session_id = session.session_id
    await session_manager.delete(session_id)

    assert session_manager.get(session_id) is None
    # session object itself should be STOPPED before being removed from manager
    assert session.state == SessionState.STOPPED
