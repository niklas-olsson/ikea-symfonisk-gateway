"""Stream publisher subsystem."""

import asyncio
from collections.abc import AsyncGenerator
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse


class StreamPublisher:
    """Publishes live audio streams over HTTP."""

    def __init__(self, host: str = "0.0.0.0", port: int = 8080):
        self.host = host
        self.port = port
        self._streams: dict[str, dict[str, Any]] = {}
        self._subscribers: dict[str, set[asyncio.Queue[bytes]]] = {}
        self._active = False

        # Setup FastAPI
        self._app = FastAPI(title="Stream Publisher")
        self._setup_routes()

        self._server: uvicorn.Server | None = None
        self._server_task: asyncio.Task[Any] | None = None

    def _setup_routes(self) -> None:
        @self._app.get("/streams/{session_id}/live.{extension}")
        async def stream_audio(session_id: str, extension: str) -> StreamingResponse:
            if session_id not in self._streams:
                raise HTTPException(status_code=404, detail="Stream not found")

            profile_id = self._streams[session_id]["profile_id"]
            expected_ext = self._get_extension(profile_id)
            if extension != expected_ext:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid extension for profile, expected {expected_ext}",
                )

            media_type = "audio/mpeg"
            if extension == "aac":
                media_type = "audio/aac"
            elif extension == "wav":
                media_type = "audio/wav"

            return StreamingResponse(
                self._stream_generator(session_id),
                media_type=media_type,
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0",
                    "Connection": "keep-alive",
                },
            )

    async def _stream_generator(self, session_id: str) -> AsyncGenerator[bytes, None]:
        queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=100)

        if session_id not in self._subscribers:
            self._subscribers[session_id] = set()
        self._subscribers[session_id].add(queue)

        try:
            while self._active and session_id in self._streams:
                chunk = await queue.get()
                if not chunk:
                    break
                yield chunk
        except asyncio.CancelledError:
            pass
        finally:
            if session_id in self._subscribers and queue in self._subscribers[session_id]:
                self._subscribers[session_id].discard(queue)
                if not self._subscribers[session_id]:
                    del self._subscribers[session_id]

    def _get_extension(self, profile_id: str) -> str:
        if profile_id == "aac_48k_stereo_256":
            return "aac"
        if profile_id == "pcm_wav_48k_stereo_16":
            return "wav"
        return "mp3"

    def get_stream_url(self, session_id: str, profile_id: str = "mp3_48k_stereo_320") -> str:
        """Get the stream URL for a session."""
        extension = self._get_extension(profile_id)
        return f"http://{self.host}:{self.port}/streams/{session_id}/live.{extension}"

    def publish(self, session_id: str, profile_id: str) -> str:
        """Start publishing a stream for a session."""
        self._streams[session_id] = {
            "session_id": session_id,
            "profile_id": profile_id,
            "active": True,
        }
        if session_id not in self._subscribers:
            self._subscribers[session_id] = set()
        return self.get_stream_url(session_id, profile_id)

    def unpublish(self, session_id: str) -> bool:
        """Stop publishing a stream."""
        if session_id in self._streams:
            self._streams[session_id]["active"] = False
            del self._streams[session_id]
            if session_id in self._subscribers:
                for queue in list(self._subscribers[session_id]):
                    try:
                        queue.put_nowait(b"")
                    except asyncio.QueueFull:
                        pass
                self._subscribers[session_id].clear()
                del self._subscribers[session_id]
            return True
        return False

    async def broadcast(self, session_id: str, chunk: bytes) -> None:
        """Broadcast an audio chunk to all subscribers of a session."""
        if session_id not in self._subscribers:
            return

        # Create a copy of the set to iterate over to avoid modifying it while iterating
        for queue in list(self._subscribers[session_id]):
            try:
                queue.put_nowait(chunk)
            except asyncio.QueueFull:
                pass

    def get_streams(self) -> list[dict[str, Any]]:
        """List all active streams."""
        return list(self._streams.values())

    async def start(self) -> None:
        """Start the stream server."""
        if self._active:
            return
        self._active = True

        config = uvicorn.Config(
            app=self._app,
            host=self.host,
            port=self.port,
            log_level="info",
            # Run in the same loop
            loop="asyncio",
        )
        self._server = uvicorn.Server(config)

        loop = asyncio.get_running_loop()
        self._server_task = loop.create_task(self._server.serve())

    async def stop(self) -> None:
        """Stop the stream server."""
        self._active = False
        if self._server:
            self._server.should_exit = True
            if self._server_task:
                await self._server_task
        self._server = None
        self._server_task = None
