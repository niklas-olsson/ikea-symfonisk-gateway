import asyncio
import random
from collections.abc import AsyncGenerator
from unittest.mock import MagicMock

import httpx
import pytest
from bridge_core.stream.publisher import StreamPublisher


@pytest.fixture
async def publisher() -> AsyncGenerator[StreamPublisher, None]:
    port = random.randint(10000, 20000)
    pub = StreamPublisher(bind_address="127.0.0.1", port=port, advertised_host="127.0.0.1")
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


@pytest.mark.asyncio
async def test_publisher_head_request(publisher: StreamPublisher) -> None:
    session_id = "test_session"
    pipeline = MagicMock()
    pipeline.profile_id = "mp3_48k_stereo_320"
    publisher.register_pipeline(session_id, pipeline)

    async with httpx.AsyncClient() as client:
        # Test HEAD request
        response = await client.head(f"http://127.0.0.1:{publisher.port}/streams/{session_id}/live.mp3")
        assert response.status_code == 200
        assert response.headers["content-type"] == "audio/mpeg"
        assert "cache-control" in response.headers
        assert response.text == ""

        # Verify pipeline.subscribe was NOT called for HEAD
        pipeline.subscribe.assert_not_called()

        # Test GET request
        async def dummy_generator():
            yield b"test"

        pipeline.subscribe.return_value = dummy_generator()
        response = await client.get(f"http://127.0.0.1:{publisher.port}/streams/{session_id}/live.mp3")
        assert response.status_code == 200
        assert response.headers["content-type"] == "audio/mpeg"
        assert response.content == b"test"

        # Verify pipeline.subscribe WAS called for GET
        pipeline.subscribe.assert_called_once()
