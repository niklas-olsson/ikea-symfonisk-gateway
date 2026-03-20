import asyncio
import random
from collections.abc import AsyncGenerator

import httpx
import pytest
from bridge_core.stream.publisher import StreamPublisher


@pytest.fixture
async def publisher() -> AsyncGenerator[StreamPublisher, None]:
    port = random.randint(10000, 20000)
    pub = StreamPublisher(host="127.0.0.1", port=port)
    task = asyncio.create_task(pub.start())
    # Give it a moment to start
    await asyncio.sleep(0.5)
    yield pub
    await pub.stop()
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, SystemExit):
        pass


@pytest.mark.asyncio
async def test_publisher_health(publisher: StreamPublisher) -> None:
    async with httpx.AsyncClient() as client:
        response = await client.get(f"http://127.0.0.1:{publisher.port}/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_publisher_stream_url(publisher: StreamPublisher) -> None:
    session_id = "test_session"
    profile_id = "mp3_48k_stereo_320"
    url = publisher.get_stream_url(session_id, profile_id)
    assert url == f"http://127.0.0.1:{publisher.port}/streams/{session_id}/live.mp3"


@pytest.mark.asyncio
async def test_publisher_stream_not_found(publisher: StreamPublisher) -> None:
    async with httpx.AsyncClient() as client:
        response = await client.get(f"http://127.0.0.1:{publisher.port}/streams/nonexistent/live.mp3")
        assert response.status_code == 404
