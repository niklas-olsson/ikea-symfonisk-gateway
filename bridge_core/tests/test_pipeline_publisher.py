import asyncio

import httpx
import pytest

from bridge_core.core.pipeline import PipelineManager
from bridge_core.stream.publisher import StreamPublisher


@pytest.mark.asyncio
async def test_pipeline_and_publisher() -> None:
    publisher = StreamPublisher(host="127.0.0.1", port=8080)
    await publisher.start()

    manager = PipelineManager()

    # Simple redirect to publisher broadcast
    async def handle_chunk(session_id: str, chunk: bytes) -> None:
        await publisher.broadcast(session_id, chunk)

    manager.on_encoded_chunk = handle_chunk

    session_id = "test_session"
    profile = "mp3_48k_stereo_320"

    url = publisher.publish(session_id, profile)
    pipeline = manager.create(session_id, profile)

    await manager.start(session_id)

    # Wait for server to be ready
    await asyncio.sleep(0.5)

    async def push_audio() -> None:
        chunk_size = 19200
        for _ in range(20):
            await pipeline.push_frame(b"\x00" * chunk_size)
            await asyncio.sleep(0.01)

    push_task = asyncio.create_task(push_audio())

    got_data = False
    try:
        async with httpx.AsyncClient() as client:
            async with client.stream("GET", url) as response:
                assert response.status_code == 200
                assert response.headers["content-type"] == "audio/mpeg"

                async for chunk in response.aiter_bytes():
                    if len(chunk) > 0:
                        got_data = True
                        break
    except Exception as e:
        pytest.fail(f"Failed to read stream: {e}")

    assert got_data, "Did not receive any data from stream"

    await push_task
    await manager.stop(session_id)
    publisher.unpublish(session_id)
    await publisher.stop()
