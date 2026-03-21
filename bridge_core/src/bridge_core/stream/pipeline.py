"""Audio pipeline implementation with jitter buffer and FFmpeg encoding."""

import asyncio
import heapq
import logging
from collections.abc import AsyncGenerator
from typing import Any

from ingress_sdk.protocol import AudioFrame

from bridge_core.stream.profiles import STREAM_PROFILES

logger = logging.getLogger(__name__)


class JitterBuffer:
    """Buffer for incoming audio frames to handle jitter and reordering."""

    def __init__(self, target_ms: int = 250, sample_rate: int = 48000):
        self.target_ms = target_ms
        self.sample_rate = sample_rate
        self._frames: list[tuple[int, int, AudioFrame]] = []
        self._next_sequence: int | None = None
        self._last_pts: int | None = None
        self._buffer_size_ms = 0.0
        self._counter = 0
        self._lock = asyncio.Lock()
        self._ready = asyncio.Event()

    async def push(self, frame: AudioFrame) -> None:
        """Push a new frame into the jitter buffer."""
        async with self._lock:
            # Drop frames that are too old
            if self._next_sequence is not None and frame.sequence < self._next_sequence:
                logger.warning(f"Dropping late frame: seq={frame.sequence}, expected={self._next_sequence}")
                return

            self._counter += 1
            heapq.heappush(self._frames, (frame.sequence, self._counter, frame))
            self._buffer_size_ms += frame.duration_ns / 1_000_000

            if self._buffer_size_ms >= self.target_ms:
                self._ready.set()

    async def pop(self) -> AudioFrame | None:
        """Pop the next frame from the jitter buffer."""
        async with self._lock:
            if not self._frames:
                self._ready.clear()
                return None

            # If we don't have enough data yet, wait unless we're already started
            if not self._ready.is_set() and self._next_sequence is None:
                return None

            seq, _, frame = heapq.heappop(self._frames)
            self._buffer_size_ms -= frame.duration_ns / 1_000_000

            if self._next_sequence is not None and seq > self._next_sequence:
                logger.warning(f"Gap detected in jitter buffer: expected {self._next_sequence}, got {seq}")
                # We could insert silence here, but for now just update sequence

            self._next_sequence = seq + 1
            self._last_pts = frame.pts_ns

            if not self._frames:
                self._ready.clear()

            return frame

    @property
    def size_ms(self) -> float:
        """Current size of the jitter buffer in milliseconds."""
        return self._buffer_size_ms


class StreamPipeline:
    """Active audio pipeline for a session using FFmpeg for encoding."""

    def __init__(
        self,
        session_id: str,
        profile_id: str,
        ffmpeg_path: str = "ffmpeg",
        on_error: Any | None = None,
    ):
        self.session_id = session_id
        self.profile_id = profile_id
        self.ffmpeg_path = ffmpeg_path
        self.on_error = on_error
        self.profile = STREAM_PROFILES[profile_id]
        self.jitter_buffer = JitterBuffer()
        self._process: asyncio.subprocess.Process | None = None
        self._clients: list[asyncio.Queue[bytes]] = []
        self._active = False
        self._lock = asyncio.Lock()
        self._feed_task: asyncio.Task[None] | None = None
        self._read_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the pipeline and FFmpeg subprocess."""
        if self._active:
            return

        cmd = self._get_ffmpeg_cmd()
        logger.info(f"Starting FFmpeg for session {self.session_id}: {' '.join(cmd)}")

        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._active = True
        self._feed_task = asyncio.create_task(self._feed_ffmpeg())
        self._read_task = asyncio.create_task(self._read_ffmpeg())

        # Supervise tasks
        self._feed_task.add_done_callback(self._handle_task_done)
        self._read_task.add_done_callback(self._handle_task_done)

    def _handle_task_done(self, task: asyncio.Task) -> None:
        """Handle completion of background tasks and report errors."""
        if not task.cancelled() and task.exception():
            exc = task.exception()
            logger.error(f"Pipeline task for {self.session_id} failed: {exc}", exc_info=exc)
            if self.on_error:
                self.on_error(exc)

    async def stop(self) -> None:
        """Stop the pipeline and FFmpeg subprocess."""
        self._active = False
        if self._feed_task:
            self._feed_task.cancel()
        if self._read_task:
            self._read_task.cancel()

        if self._process:
            try:
                self._process.terminate()
                await self._process.wait()
            except Exception:
                pass
            self._process = None

    def _get_ffmpeg_cmd(self) -> list[str]:
        """Generate the FFmpeg command based on the profile."""
        # Input format is always canonical 48kHz 16-bit stereo PCM
        args = [
            self.ffmpeg_path,
            "-f",
            "s16le",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-i",
            "pipe:0",
        ]

        if self.profile.codec == "mp3":
            args += ["-c:a", "libmp3lame", "-b:a", f"{self.profile.bitrate_kbps}k", "-f", "mp3"]
        elif self.profile.codec == "aac":
            args += ["-c:a", "aac", "-b:a", f"{self.profile.bitrate_kbps}k", "-f", "adts"]
        elif self.profile.codec == "pcm_s16le":
            args += ["-c:a", "pcm_s16le", "-f", "wav"]
        else:
            raise ValueError(f"Unsupported codec: {self.profile.codec}")

        args += ["pipe:1"]
        return args

    async def push_frame(self, frame: AudioFrame) -> None:
        """Push an incoming audio frame to the jitter buffer."""
        await self.jitter_buffer.push(frame)

    async def _feed_ffmpeg(self) -> None:
        """Continuously feed audio data from jitter buffer to FFmpeg's stdin."""
        while self._active:
            try:
                frame = await self.jitter_buffer.pop()
                if frame and self._process and self._process.stdin:
                    self._process.stdin.write(frame.audio_data)
                    await self._process.stdin.drain()
                else:
                    await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error feeding FFmpeg for session {self.session_id}: {e}")
                break

    async def _read_ffmpeg(self) -> None:
        """Continuously read encoded data from FFmpeg's stdout and fan out to clients."""
        while self._active:
            try:
                if self._process and self._process.stdout:
                    data = await self._process.stdout.read(4096)
                    if not data:
                        break

                    async with self._lock:
                        for client_queue in self._clients:
                            try:
                                client_queue.put_nowait(data)
                            except asyncio.QueueFull:
                                # Skip slow clients
                                pass
                else:
                    await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error reading from FFmpeg for session {self.session_id}: {e}")
                break

    async def subscribe(self) -> AsyncGenerator[bytes, None]:
        """Subscribe to encoded audio data. Yields chunks of encoded data."""
        queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=100)
        async with self._lock:
            self._clients.append(queue)

        try:
            while True:
                data = await queue.get()
                yield data
        finally:
            async with self._lock:
                self._clients.remove(queue)
