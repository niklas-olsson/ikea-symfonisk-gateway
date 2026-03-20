"""HTTP stream server for publishing encoded audio streams."""

import logging

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from bridge_core.stream.pipeline import StreamPipeline

logger = logging.getLogger(__name__)


class StreamPublisher:
    """Publishes live audio streams over HTTP using FastAPI."""

    def __init__(self, host: str = "0.0.0.0", port: int = 8080):
        self.host = host
        self.port = port
        self.app = FastAPI(title="Bridge Stream Publisher")
        self._pipelines: dict[str, StreamPipeline] = {}
        self._setup_routes()

    def _setup_routes(self) -> None:
        """Set up FastAPI routes."""

        @self.app.get("/streams/{session_id}/live.{ext}")
        async def stream_audio(session_id: str, ext: str) -> StreamingResponse:
            pipeline = self._pipelines.get(session_id)
            if not pipeline:
                raise HTTPException(status_code=404, detail="Stream session not found")

            # Validate extension matches profile
            expected_ext = self._get_extension(pipeline.profile_id)
            if ext != expected_ext:
                raise HTTPException(status_code=400, detail=f"Invalid extension for profile. Expected {expected_ext}")

            media_type = self._get_media_type(ext)
            return StreamingResponse(
                pipeline.subscribe(),
                media_type=media_type,
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0",
                    "Connection": "keep-alive",
                },
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

    def get_stream_url(self, session_id: str, profile_id: str) -> str:
        """Get the stream URL for a session."""
        ext = self._get_extension(profile_id)
        return f"http://{self.host}:{self.port}/streams/{session_id}/live.{ext}"

    def register_pipeline(self, session_id: str, pipeline: StreamPipeline) -> None:
        """Register a pipeline for publishing."""
        self._pipelines[session_id] = pipeline

    def unregister_pipeline(self, session_id: str) -> None:
        """Unregister a pipeline."""
        self._pipelines.pop(session_id, None)

    async def start(self) -> None:
        """Start the stream server."""
        config = uvicorn.Config(self.app, host=self.host, port=self.port, log_level="info")
        self._server = uvicorn.Server(config)
        await self._server.serve()

    async def stop(self) -> None:
        """Stop the stream server."""
        if hasattr(self, "_server"):
            self._server.should_exit = True
