import asyncio
import sys
from unittest.mock import AsyncMock, patch

# Add relevant paths to sys.path
sys.path.append("adapters/linux_audio/src")
sys.path.append("bridge_core/src")
sys.path.append("ingress_sdk/src")

from adapter_linux_audio import LinuxAudioAdapter
from ingress_sdk.base import FrameSink


class MockFrameSink(FrameSink):
    def __init__(self):
        self.frames = []
    def on_frame(self, data: bytes, pts_ns: int, duration_ns: int) -> None:
        self.frames.append((data, pts_ns, duration_ns))

async def verify_capture():
    print("Verifying audio capture...")

    # Mock subprocess creation
    mock_process = AsyncMock()
    mock_stdout = AsyncMock()
    mock_process.stdout = mock_stdout

    # Simulate 10ms of audio (1920 bytes)
    mock_stdout.readexactly.side_effect = [
        b'\x00' * 1920, # First frame
        asyncio.IncompleteReadError(b'', 1920) # End of stream
    ]

    with patch("shutil.which", return_value="parec"):
        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            adapter = LinuxAudioAdapter()
            sink = MockFrameSink()

            # Start capture
            start_res = adapter.start("default", sink)
            assert start_res.success
            print(" - Capture started successfully")

            # Give it a moment to run the loop
            await asyncio.sleep(0.5)

            # Verify frames
            assert len(sink.frames) > 0
            print(f" - Received {len(sink.frames)} frames")

            # Stop capture
            adapter.stop(start_res.session_id)
            print(" - Capture stopped successfully")

            # Health check after stop
            health = adapter.probe_health("default")
            assert health.source_state == "idle"
            print(" - Health state is idle after stop")

async def verify_restart_behavior():
    print("Verifying restart behavior...")

    mock_process = AsyncMock()
    mock_stdout = AsyncMock()
    mock_process.stdout = mock_stdout

    # First read succeeds, then it fails/exits
    mock_stdout.readexactly.side_effect = [
        b'\x00' * 1920,
        Exception("Subprocess crashed")
    ]

    with patch("shutil.which", return_value="parec"):
        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            adapter = LinuxAudioAdapter()
            sink = MockFrameSink()

            adapter.start("default", sink)

            # Wait for crash
            await asyncio.sleep(0.5)

            # State should be inactive after crash
            health = adapter.probe_health("default")
            assert not health.healthy
            assert health.source_state == "idle" # Code sets _running = False in finally block
            print(" - Adapter correctly handles capture process crash")

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(verify_capture())
        loop.run_until_complete(verify_restart_behavior())
        print("Verification completed successfully!")
    finally:
        loop.close()
