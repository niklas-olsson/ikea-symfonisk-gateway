import pytest
from bridge_core.core.session_manager import SessionManager, SessionState
from bridge_core.core.event_bus import EventBus, EventType

@pytest.fixture
def event_bus():
    return EventBus()

@pytest.fixture
def session_manager(event_bus):
    return SessionManager(event_bus)

@pytest.mark.asyncio
async def test_create_session(session_manager, event_bus):
    queue = event_bus.subscribe(None)
    session = session_manager.create("source-1", "target-1")

    assert session.source_id == "source-1"
    assert session.target_id == "target-1"
    assert session.state == SessionState.CREATED
    assert session.session_id in session_manager._sessions

    # Process events
    event = await queue.get()
    assert event.type == EventType.SESSION_CREATED.value
    assert event.session_id == session.session_id

@pytest.mark.asyncio
async def test_start_session(session_manager, event_bus):
    queue = event_bus.subscribe(None)
    session = session_manager.create("source-1", "target-1")

    # consume CREATED event
    await queue.get()

    success = session_manager.start(session.session_id, "http://stream")
    assert success is True
    assert session.state == SessionState.PLAYING
    assert session.stream_url == "http://stream"
    assert session.started_at is not None

    event1 = await queue.get()
    assert event1.type == EventType.SESSION_STARTING.value

    event2 = await queue.get()
    assert event2.type == EventType.SESSION_STARTED.value

@pytest.mark.asyncio
async def test_stop_session(session_manager, event_bus):
    queue = event_bus.subscribe(None)
    session = session_manager.create("source-1", "target-1")
    session_manager.start(session.session_id, "http://stream")

    # Consume CREATED, STARTING, STARTED
    await queue.get()
    await queue.get()
    await queue.get()

    success = session_manager.stop(session.session_id)
    assert success is True
    assert session.state == SessionState.STOPPED

    event1 = await queue.get()
    assert event1.type == EventType.SESSION_STOPPING.value

    event2 = await queue.get()
    assert event2.type == EventType.SESSION_STOPPED.value

@pytest.mark.asyncio
async def test_recover_session(session_manager, event_bus):
    queue = event_bus.subscribe(None)
    session = session_manager.create("source-1", "target-1")

    # Consume CREATED
    await queue.get()

    success = session_manager.recover(session.session_id)
    assert success is True
    assert session.state == SessionState.PLAYING

    event1 = await queue.get()
    assert event1.type == EventType.HEAL_ATTEMPTED.value

    event2 = await queue.get()
    assert event2.type == EventType.HEAL_SUCCEEDED.value

@pytest.mark.asyncio
async def test_recover_session_disabled_auto_heal(session_manager, event_bus):
    session = session_manager.create("source-1", "target-1", auto_heal=False)

    success = session_manager.recover(session.session_id)
    assert success is False
    assert session.state == SessionState.CREATED

@pytest.mark.asyncio
async def test_terminate_session(session_manager, event_bus):
    queue = event_bus.subscribe(None)
    session = session_manager.create("source-1", "target-1")

    # Consume CREATED
    await queue.get()

    success = session_manager.terminate(session.session_id, reason="error")
    assert success is True
    assert session.state == SessionState.FAILED

    event = await queue.get()
    assert event.type == EventType.SESSION_FAILED.value
    assert event.payload["reason"] == "error"
