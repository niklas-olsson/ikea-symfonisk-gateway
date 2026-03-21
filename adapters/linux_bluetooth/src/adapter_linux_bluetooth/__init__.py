"""Linux Bluetooth (BlueALSA) ingress adapter.

Captures audio from Bluetooth A2DP sources (turntables, phones) via PulseAudio/BlueZ.
Emits canonical PCM 48kHz stereo frames.
"""

import asyncio
import logging
import shutil
import subprocess

from ingress_sdk.base import FrameSink, IngressAdapter
from ingress_sdk.types import (
    AdapterCapabilities,
    HealthResult,
    PairingResult,
    PrepareResult,
    SourceCapabilities,
    SourceDescriptor,
    SourceType,
    StartResult,
)

logger = logging.getLogger(__name__)


class LinuxBluetoothAdapter(IngressAdapter):
    """Adapter for capturing Bluetooth audio on Linux."""

    def __init__(self) -> None:
        self._session_id: str | None = None
        self._running = False
        self._process: asyncio.subprocess.Process | None = None
        self._capture_task: asyncio.Task[None] | None = None
        self._frame_sink: FrameSink | None = None
        self._pairing_timeout_task: asyncio.Task[None] | None = None

    def id(self) -> str:
        return "linux-bluetooth-adapter"

    def platform(self) -> str:
        return "linux"

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            supports_system_audio=False,
            supports_bluetooth_audio=True,
            supports_line_in=False,
            supports_microphone=False,
            supports_file_replay=False,
            supports_synthetic_test_source=False,
            supports_sample_rates=[48000],
            supports_channels=[2],
            supports_hotplug_events=True,
            supports_pairing=True,
        )

    def list_sources(self) -> list[SourceDescriptor]:
        """Discover connected Bluetooth A2DP sources using pactl."""
        sources: list[SourceDescriptor] = []
        if not shutil.which("pactl"):
            return sources

        try:
            result = subprocess.run(["pactl", "list", "short", "sources"], capture_output=True, text=True, check=True)
            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue
                parts = line.split("\t")
                if len(parts) < 2:
                    continue

                source_id = parts[1]
                # Bluetooth sources in PulseAudio typically have "bluez" in the name
                if "bluez" in source_id:
                    display_name = f"Bluetooth: {source_id.replace('bluez_source.', '').replace('.a2dp_source', '').replace('_', ':')}"
                    sources.append(
                        SourceDescriptor(
                            source_id=source_id,
                            source_type=SourceType.BLUETOOTH_AUDIO,
                            display_name=display_name,
                            platform="linux",
                            capabilities=SourceCapabilities(
                                sample_rates=[48000],
                                channels=[2],
                                bit_depths=[16],
                            ),
                        )
                    )
        except (subprocess.SubprocessError, FileNotFoundError) as e:
            logger.error(f"Failed to list Bluetooth sources: {e}")

        return sources

    def prepare(self, source_id: str) -> PrepareResult:
        """Verify the specified Bluetooth source exists."""
        sources = self.list_sources()
        for s in sources:
            if s.source_id == source_id:
                return PrepareResult(success=True, source_id=source_id)

        return PrepareResult(success=False, source_id=source_id, error=f"Bluetooth source {source_id} not found")

    def start(self, source_id: str, frame_sink: FrameSink) -> StartResult:
        """Start capturing audio from the Bluetooth source."""
        if self._running:
            return StartResult(success=False, message="Already running")

        self._session_id = f"sess_{id(self)}"
        self._frame_sink = frame_sink
        self._running = True
        self._capture_task = asyncio.create_task(self._capture_loop(source_id))

        logger.info(f"Started Bluetooth capture from {source_id} (session: {self._session_id})")
        return StartResult(success=True, session_id=self._session_id)

    def stop(self, session_id: str) -> None:
        """Stop the active Bluetooth capture session."""
        if self._session_id != session_id:
            return

        self._running = False

        if self._capture_task:
            self._capture_task.cancel()
            self._capture_task = None

        self._session_id = None
        self._frame_sink = None

        logger.info(f"Stopped Bluetooth capture session {session_id}")

    def probe_health(self, source_id: str) -> HealthResult:
        """Probe the health of the Bluetooth source."""
        # Simple health check based on whether the capture loop is running
        return HealthResult(
            healthy=self._running,
            source_state="active" if self._running else "idle",
            signal_present=self._running,
            dropped_frames=0,
            last_error=None,
        )

    async def _capture_loop(self, source_id: str) -> None:
        """Audio capture loop using parec."""
        if not shutil.which("parec"):
            logger.error("parec not found, cannot capture audio")
            self._running = False
            return

        cmd = ["parec", f"--device={source_id}", "--format=s16le", "--rate=48000", "--channels=2"]

        try:
            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            if self._process.stdout is None:
                logger.error("Failed to open stdout for capture process")
                return

            chunk_size = 1920  # 10ms at 48kHz, 16-bit stereo
            pts_ns = 0
            duration_ns = 10_000_000  # 10ms

            while self._running:
                try:
                    data = await self._process.stdout.readexactly(chunk_size)
                    if not data:
                        break

                    if self._frame_sink:
                        try:
                            self._frame_sink.on_frame(data, pts_ns, duration_ns)
                        except Exception as e:
                            logger.error(f"Error in frame sink: {e}")
                            self._frame_sink.on_error(e)

                    pts_ns += duration_ns
                except asyncio.IncompleteReadError:
                    break

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error capturing Bluetooth audio: {e}")
            if self._frame_sink:
                self._frame_sink.on_error(e)
        finally:
            self._running = False
            if self._process:
                try:
                    self._process.terminate()
                    await self._process.wait()
                except ProcessLookupError:
                    pass
                self._process = None

    def start_pairing(self, timeout_seconds: int = 60) -> PairingResult:
        """Start adapter pairing mode using bluetoothctl."""
        if not shutil.which("bluetoothctl"):
            return PairingResult(success=False, error="bluetoothctl not found")

        try:
            # Enable pairing and discoverability
            subprocess.run(["bluetoothctl", "power", "on"], check=True)
            subprocess.run(["bluetoothctl", "pairable", "on"], check=True)
            subprocess.run(["bluetoothctl", "discoverable", "on"], check=True)

            # Start a timer to stop pairing mode
            if self._pairing_timeout_task:
                self._pairing_timeout_task.cancel()
            self._pairing_timeout_task = asyncio.create_task(self._pairing_timeout(timeout_seconds))

            logger.info(f"Bluetooth pairing mode enabled for {timeout_seconds} seconds")
            return PairingResult(success=True, message=f"Pairing mode enabled for {timeout_seconds}s")
        except subprocess.SubprocessError as e:
            logger.error(f"Failed to enable pairing mode: {e}")
            return PairingResult(success=False, error=str(e))

    async def _pairing_timeout(self, seconds: int) -> None:
        """Stop pairing mode after timeout."""
        try:
            await asyncio.sleep(seconds)
            self.stop_pairing()
            logger.info("Bluetooth pairing mode timed out")
        except asyncio.CancelledError:
            pass

    def stop_pairing(self) -> PairingResult:
        """Stop adapter pairing mode."""
        if self._pairing_timeout_task:
            self._pairing_timeout_task.cancel()
            self._pairing_timeout_task = None

        if not shutil.which("bluetoothctl"):
            return PairingResult(success=False, error="bluetoothctl not found")

        try:
            subprocess.run(["bluetoothctl", "pairable", "off"], check=True)
            subprocess.run(["bluetoothctl", "discoverable", "off"], check=True)
            logger.info("Bluetooth pairing mode disabled")
            return PairingResult(success=True, message="Pairing mode disabled")
        except subprocess.SubprocessError as e:
            logger.error(f"Failed to disable pairing mode: {e}")
            return PairingResult(success=False, error=str(e))
