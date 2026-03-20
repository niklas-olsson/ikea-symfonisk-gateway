"""Audio pipeline manager - handles resampling, encoding, and buffering."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class PipelineStats:
    """Statistics for a pipeline."""

    frames_processed: int = 0
    frames_dropped: int = 0
    buffer_fill_ms: float = 0.0
    encode_latency_ms: float = 0.0


class Pipeline:
    """Represents an active audio pipeline for a session."""

    def __init__(self, session_id: str, profile: str):
        self.session_id = session_id
        self.profile = profile
        self.active = False
        self.stats = PipelineStats()

        # Audio buffer for raw PCM (max 100 frames to prevent unbounded memory growth)
        self._buffer: asyncio.Queue[bytes] = asyncio.Queue(maxsize=100)
        self._ffmpeg_process: asyncio.subprocess.Process | None = None

        # Tasks
        self._writer_task: asyncio.Task[None] | None = None
        self._reader_task: asyncio.Task[None] | None = None

        # Callback for encoded chunks
        self.on_encoded_chunk: Callable[[str, bytes], Awaitable[None]] | None = None

    def _get_ffmpeg_command(self) -> list[str]:
        """Construct FFmpeg command based on the profile."""
        # Standard input format is canonical PCM: 48kHz, stereo, s16le
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "s16le",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-i",
            "pipe:0",
        ]

        if self.profile == "mp3_48k_stereo_320":
            cmd.extend(
                [
                    "-c:a",
                    "libmp3lame",
                    "-b:a",
                    "320k",
                    "-f",
                    "mp3",
                ]
            )
        elif self.profile == "aac_48k_stereo_256":
            cmd.extend(
                [
                    "-c:a",
                    "aac",
                    "-b:a",
                    "256k",
                    "-f",
                    "adts",
                ]
            )
        elif self.profile == "pcm_wav_48k_stereo_16":
            cmd.extend(
                [
                    "-c:a",
                    "pcm_s16le",
                    "-f",
                    "wav",
                ]
            )
        else:
            # Default to mp3 if unknown profile
            cmd.extend(
                [
                    "-c:a",
                    "libmp3lame",
                    "-b:a",
                    "320k",
                    "-f",
                    "mp3",
                ]
            )

        cmd.append("pipe:1")
        return cmd

    async def _write_loop(self) -> None:
        """Read from internal buffer and write to FFmpeg stdin."""
        if not self._ffmpeg_process or not self._ffmpeg_process.stdin:
            return

        try:
            while self.active:
                chunk = await self._buffer.get()
                if not self.active:
                    break

                try:
                    self._ffmpeg_process.stdin.write(chunk)
                    await self._ffmpeg_process.stdin.drain()
                    self.stats.frames_processed += 1
                except (BrokenPipeError, ConnectionResetError):
                    logger.error(f"FFmpeg stdin broken for session {self.session_id}")
                    break
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in pipeline write loop for {self.session_id}: {e}")
        finally:
            if self._ffmpeg_process and self._ffmpeg_process.stdin:
                try:
                    self._ffmpeg_process.stdin.close()
                except Exception:
                    pass

    async def _read_loop(self) -> None:
        """Read from FFmpeg stdout and call callback."""
        if not self._ffmpeg_process or not self._ffmpeg_process.stdout:
            return

        try:
            while self.active:
                chunk = await self._ffmpeg_process.stdout.read(4096)
                if not chunk:
                    break

                if self.on_encoded_chunk:
                    await self.on_encoded_chunk(self.session_id, chunk)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in pipeline read loop for {self.session_id}: {e}")
        finally:
            self.active = False

    async def start(self) -> bool:
        """Start the pipeline."""
        if self.active:
            return True

        cmd = self._get_ffmpeg_command()
        try:
            self._ffmpeg_process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except FileNotFoundError:
            logger.error("FFmpeg not found. Cannot start pipeline.")
            return False
        except Exception as e:
            logger.error(f"Failed to start FFmpeg: {e}")
            return False

        self.active = True
        self.stats = PipelineStats()

        loop = asyncio.get_running_loop()
        self._writer_task = loop.create_task(self._write_loop())
        self._reader_task = loop.create_task(self._read_loop())

        return True

    async def stop(self) -> bool:
        """Stop the pipeline."""
        if not self.active:
            return True

        self.active = False

        if self._writer_task:
            self._writer_task.cancel()

        if self._reader_task:
            self._reader_task.cancel()

        if self._ffmpeg_process:
            if self._ffmpeg_process.returncode is None:
                try:
                    self._ffmpeg_process.terminate()
                    await asyncio.wait_for(self._ffmpeg_process.wait(), timeout=2.0)
                except TimeoutError:
                    self._ffmpeg_process.kill()
                    await self._ffmpeg_process.wait()
            self._ffmpeg_process = None

        return True

    async def push_frame(self, frame: bytes) -> None:
        """Push a raw PCM frame into the pipeline."""
        if not self.active:
            return

        try:
            self._buffer.put_nowait(frame)
        except asyncio.QueueFull:
            self.stats.frames_dropped += 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "profile": self.profile,
            "active": self.active,
            "stats": {
                "frames_processed": self.stats.frames_processed,
                "frames_dropped": self.stats.frames_dropped,
                "buffer_fill_ms": self.stats.buffer_fill_ms,
                "encode_latency_ms": self.stats.encode_latency_ms,
            },
        }


class PipelineManager:
    """Manages audio pipelines for active sessions."""

    def __init__(self) -> None:
        self._pipelines: dict[str, Pipeline] = {}
        self.on_encoded_chunk: Callable[[str, bytes], Awaitable[None]] | None = None

    def create(self, session_id: str, profile: str) -> Pipeline:
        """Create a new pipeline."""
        pipeline = Pipeline(session_id, profile)
        if self.on_encoded_chunk:
            pipeline.on_encoded_chunk = self.on_encoded_chunk
        self._pipelines[session_id] = pipeline
        return pipeline

    def get(self, session_id: str) -> Pipeline | None:
        """Get a pipeline by session ID."""
        return self._pipelines.get(session_id)

    async def start(self, session_id: str) -> bool:
        """Start a pipeline."""
        pipeline = self._pipelines.get(session_id)
        if pipeline:
            return await pipeline.start()
        return False

    async def stop(self, session_id: str) -> bool:
        """Stop a pipeline."""
        pipeline = self._pipelines.get(session_id)
        if pipeline:
            return await pipeline.stop()
        return False

    async def delete(self, session_id: str) -> bool:
        """Delete a pipeline."""
        pipeline = self._pipelines.get(session_id)
        if pipeline:
            await pipeline.stop()
            del self._pipelines[session_id]
            return True
        return False

    def list(self) -> list[Pipeline]:
        """List all pipelines."""
        return list(self._pipelines.values())
