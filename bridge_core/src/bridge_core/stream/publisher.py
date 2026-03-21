"""HTTP stream server for publishing encoded audio streams."""

import logging
import socket

import uvicorn
from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import StreamingResponse

from bridge_core.stream.pipeline import StreamPipeline

logger = logging.getLogger(__name__)


class StreamPublisher:
    """Publishes live audio streams over HTTP using FastAPI."""

    def __init__(
        self,
        bind_address: str = "0.0.0.0",
        port: int = 8080,
        advertised_host: str | None = None,
    ):
        self.bind_address = bind_address
        self.port = port
        self.advertised_host = advertised_host or self._get_default_advertised_host()
        self.app = FastAPI(title="Bridge Stream Publisher")
        self._pipelines: dict[str, StreamPipeline] = {}
        self._setup_routes()

    def _setup_routes(self) -> None:
        """Set up FastAPI routes."""

        @self.app.get("/streams/{session_id}/live.{ext}")
        async def stream_audio(session_id: str, ext: str) -> StreamingResponse:
            pipeline, media_type, headers = self._resolve_stream(session_id, ext)
            return StreamingResponse(
                pipeline.subscribe(),
                media_type=media_type,
                headers=headers,
            )

        @self.app.head("/streams/{session_id}/live.{ext}")
        async def head_stream_audio(session_id: str, ext: str) -> Response:
            _, media_type, headers = self._resolve_stream(session_id, ext)
            return Response(
                content=None,
                media_type=media_type,
                headers=headers,
            )

        @self.app.get("/health")
        async def health() -> dict[str, str]:
            return {"status": "ok"}

    def _get_extension(self, profile_id: str) -> str:
        if "mp3" in profile_id:
            return "mp3"
        if "aac" in profile_id:
            return "aac"
        if "wav" in profile_id:
            return "wav"
        return "bin"

    def _get_media_type(self, ext: str) -> str:
        if ext == "mp3":
            return "audio/mpeg"
        if ext == "aac":
            return "audio/aac"
        if ext == "wav":
            return "audio/wav"
        return "application/octet-stream"

    def _get_default_advertised_host(self) -> str:
        """Detect the primary local IP address using a dummy UDP connection."""
        try:
            # We don't actually need to connect to this address, just use it
            # to determine which interface would be used to reach it.
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                return str(s.getsockname()[0])
        except Exception as e:
            logger.warning("Failed to auto-detect IP, falling back to localhost: %s", e)
            return "127.0.0.1"

    def get_stream_url(self, session_id: str, profile_id: str) -> str:
        """Get the stream URL for a session."""
        ext = self._get_extension(profile_id)
        return f"http://{self.advertised_host}:{self.port}/streams/{session_id}/live.{ext}"

    def register_pipeline(self, session_id: str, pipeline: StreamPipeline) -> None:
        """Register a pipeline for publishing."""
        self._pipelines[session_id] = pipeline

    def unregister_pipeline(self, session_id: str) -> None:
        """Unregister a pipeline."""
        self._pipelines.pop(session_id, None)

    async def start(self) -> None:
        """Start the stream server."""
        config = uvicorn.Config(
            self.app,
            host=self.bind_address,
            port=self.port,
            log_level="info",
        )
        self._server = uvicorn.Server(config)
        await self._server.serve()

    async def stop(self) -> None:
        """Stop the stream server."""
        if hasattr(self, "_server"):
            self._server.should_exit = True

    def _resolve_stream(self, session_id: str, ext: str) -> tuple[StreamPipeline, str, dict[str, str]]:
        """Resolve a stream session and validate the extension."""
        pipeline = self._pipelines.get(session_id)
        if not pipeline:
            raise HTTPException(status_code=404, detail="Stream session not found")

        # Validate extension matches profile
        expected_ext = self._get_extension(pipeline.profile_id)
        if ext != expected_ext:
            raise HTTPException(status_code=400, detail=f"Invalid extension for profile. Expected {expected_ext}")

        media_type = self._get_media_type(ext)
        headers = {
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
            "Connection": "keep-alive",
        }
        return pipeline, media_type, headers
