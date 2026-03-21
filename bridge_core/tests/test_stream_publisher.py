from collections.abc import AsyncGenerator
from unittest.mock import MagicMock

import httpx
import pytest
from bridge_core.stream.publisher import StreamPublisher


@pytest.fixture
def publisher() -> StreamPublisher:
    return StreamPublisher(bind_address="127.0.0.1", port=18080, advertised_host="127.0.0.1")


@pytest.mark.asyncio
async def test_publisher_health(publisher: StreamPublisher) -> None:
    transport = httpx.ASGITransport(app=publisher.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_publisher_stream_url(publisher: StreamPublisher) -> None:
    session_id = "test_session"
    profile_id = "mp3_48k_stereo_320"
    url = publisher.get_stream_url(session_id, profile_id, subscriber_role="primary_renderer", delivery_path_id="tgt_1")
    assert url == f"http://127.0.0.1:{publisher.port}/streams/{session_id}/live.mp3?subscriber_role=primary_renderer&delivery_path_id=tgt_1"


@pytest.mark.asyncio
async def test_publisher_stream_not_found(publisher: StreamPublisher) -> None:
    transport = httpx.ASGITransport(app=publisher.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/streams/nonexistent/live.mp3")
        assert response.status_code == 404


@pytest.mark.asyncio
async def test_publisher_head_request(publisher: StreamPublisher) -> None:
    session_id = "test_session"
    pipeline = MagicMock()
    pipeline.profile_id = "mp3_48k_stereo_320"
    publisher.register_pipeline(session_id, pipeline)

    transport = httpx.ASGITransport(app=publisher.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Test HEAD request
        response = await client.head(f"/streams/{session_id}/live.mp3")
        assert response.status_code == 200
        assert response.headers["content-type"] == "audio/mpeg"
        assert "cache-control" in response.headers
        assert response.text == ""

        # Verify pipeline.subscribe was NOT called for HEAD
        pipeline.subscribe.assert_not_called()

        # Test GET request
        async def dummy_generator() -> AsyncGenerator[bytes, None]:
            yield b"test"

        pipeline.subscribe.return_value = dummy_generator()
        response = await client.get(
            f"/streams/{session_id}/live.mp3?subscriber_role=primary_renderer&delivery_path_id=tgt_1",
            headers={"user-agent": "Sonos/1.0"},
        )
        assert response.status_code == 200
        assert response.headers["content-type"] == "audio/mpeg"
        assert response.content == b"test"

        # Verify pipeline.subscribe WAS called for GET
        pipeline.subscribe.assert_called_once()
        subscriber_info = pipeline.subscribe.call_args.args[0]
        assert subscriber_info.role == "primary_renderer"
        assert subscriber_info.delivery_path_id == "tgt_1"
        assert subscriber_info.user_agent == "Sonos/1.0"


def test_swap_pipeline_keeps_route_stable() -> None:
    publisher = StreamPublisher(bind_address="127.0.0.1", port=18080, advertised_host="127.0.0.1")
    old_pipeline = MagicMock()
    old_pipeline.profile_id = "mp3_48k_stereo_320"
    new_pipeline = MagicMock()
    new_pipeline.profile_id = "mp3_48k_stereo_320"

    publisher.register_pipeline("test_session", old_pipeline)
    first_resolved, _, _ = publisher._resolve_stream("test_session", "mp3")
    assert first_resolved is old_pipeline

    publisher.swap_pipeline("test_session", new_pipeline)
    second_resolved, _, _ = publisher._resolve_stream("test_session", "mp3")
    assert second_resolved is new_pipeline
