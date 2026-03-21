"""Linux audio (ALSA/PulseAudio) ingress adapter.

Captures system audio from default output using PulseAudio or ALSA.
Emits canonical PCM 48kHz stereo frames.
"""

import asyncio
import logging

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


class LinuxAudioAdapter(IngressAdapter):
    """Adapter for capturing system audio on Linux."""

    def __init__(self) -> None:
        self._session_id: str | None = None
        self._running = False
        self._process: asyncio.subprocess.Process | None = None
        self._capture_task: asyncio.Task[None] | None = None
        self._frame_sink: FrameSink | None = None
        self._hotplug_task: asyncio.Task[None] | None = None

        # We start listening to hotplug events asynchronously when instantiated
        # or it can be started on demand
        self._start_hotplug_listener()

    def id(self) -> str:
        return "linux-audio-adapter"

    def _start_hotplug_listener(self) -> None:
        """Starts a background task to listen for pactl subscribe events."""
        import shutil

        if shutil.which("pactl"):
            self._hotplug_task = asyncio.create_task(self._hotplug_loop())

    async def _hotplug_loop(self) -> None:
        """Listens for PulseAudio events using pactl subscribe."""
        try:
            process = await asyncio.create_subprocess_exec(
                "pactl",
                "subscribe",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            if process.stdout is None:
                return

            while True:
                line = await process.stdout.readline()
                if not line:
                    break

                line_str = line.decode().strip()
                if "Event 'new'" in line_str or "Event 'remove'" in line_str:
                    # In a fully integrated system we would emit this to the core
                    # For now we log it. The interface requires capabilities reporting
                    # supports_hotplug_events=True, and implies an eventual callback or polling
                    # from the core.
                    logger.debug(f"Audio source changed: {line_str}")

        except asyncio.CancelledError:
            if process:
                try:
                    process.terminate()
                    await process.wait()
                except ProcessLookupError:
                    pass
        except Exception as e:
            logger.error(f"Hotplug listener error: {e}")

    def platform(self) -> str:
        return "linux"

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            supports_system_audio=True,
            supports_bluetooth_audio=False,
            supports_line_in=True,
            supports_microphone=True,
            supports_file_replay=False,
            supports_synthetic_test_source=False,
            supports_sample_rates=[48000],
            supports_channels=[2],
            supports_hotplug_events=True,
            supports_pairing=False,
        )

    def list_sources(self) -> list[SourceDescriptor]:
        # Simple static return for now, but will make it dynamic using pactl/arecord
        sources = []

        # Try to discover PulseAudio sinks
        import subprocess

        try:
            result = subprocess.run(["pactl", "list", "short", "sources"], capture_output=True, text=True, check=True)
            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue
                parts = line.split("\t")
                if len(parts) >= 2:
                    source_id = parts[1]
                    display_name = f"PulseAudio: {source_id}"
                    source_type = SourceType.SYSTEM_AUDIO

                    if "monitor" in source_id:
                        display_name = f"System Audio ({source_id})"
                        source_type = SourceType.SYSTEM_AUDIO
                    elif "alsa_input" in source_id:
                        display_name = f"Microphone/Line-In ({source_id})"
                        source_type = SourceType.MICROPHONE

                    sources.append(
                        SourceDescriptor(
                            source_id=source_id,
                            source_type=source_type,
                            display_name=display_name,
                            platform="linux",
                            capabilities=SourceCapabilities(
                                sample_rates=[48000],
                                channels=[2],
                                bit_depths=[16],
                            ),
                        )
                    )
        except (subprocess.SubprocessError, FileNotFoundError):
            # Fallback to ALSA if pactl fails or not found
            try:
                result = subprocess.run(["arecord", "-l"], capture_output=True, text=True, check=True)
                for line in result.stdout.split("\n"):
                    if line.startswith("card "):
                        # e.g. card 0: PCH [HDA Intel PCH], device 0: ALC294 Analog [ALC294 Analog]
                        # Extract card number and device number to form plughw:X,Y
                        parts = line.split(",")
                        card_str = parts[0].split(":")[0].replace("card ", "").strip()
                        dev_str = parts[1].split(":")[0].replace(" device ", "").strip()
                        source_id = f"plughw:{card_str},{dev_str}"
                        display_name = f"ALSA: {parts[0].split(':')[1].strip()}"

                        sources.append(
                            SourceDescriptor(
                                source_id=source_id,
                                source_type=SourceType.LINE_IN,  # hard to know, assume Line-in
                                display_name=display_name,
                                platform="linux",
                                capabilities=SourceCapabilities(
                                    sample_rates=[48000],
                                    channels=[2],
                                    bit_depths=[16],
                                ),
                            )
                        )
            except (subprocess.SubprocessError, FileNotFoundError):
                pass

        # If nothing found, provide a fallback default
        if not sources:
            sources.append(
                SourceDescriptor(
                    source_id="default",
                    source_type=SourceType.SYSTEM_AUDIO,
                    display_name="Default System Audio",
                    platform="linux",
                    capabilities=SourceCapabilities(
                        sample_rates=[48000],
                        channels=[2],
                        bit_depths=[16],
                    ),
                )
            )

        return sources

    def prepare(self, source_id: str) -> PrepareResult:
        """Verify the specified source exists in the system."""
        # Special case for default
        if source_id == "default":
            return PrepareResult(success=True, source_id=source_id)

        sources = self.list_sources()
        for s in sources:
            if s.source_id == source_id:
                return PrepareResult(success=True, source_id=source_id)

        return PrepareResult(success=False, source_id=source_id, error=f"Source {source_id} not found in system")

    def start(self, source_id: str, frame_sink: FrameSink) -> StartResult:
        self._session_id = f"sess_{id(self)}"
        self._frame_sink = frame_sink
        self._running = True
        self._capture_task = asyncio.create_task(self._capture_loop(source_id))
        return StartResult(success=True, session_id=self._session_id)

    def stop(self, session_id: str) -> None:
        if self._session_id != session_id:
            return

        self._running = False

        if self._capture_task:
            self._capture_task.cancel()
            # In a real sync stop we'd probably wait for this task
            self._capture_task = None

        self._session_id = None
        self._frame_sink = None

        # Clean up process if it's still around
        if self._process:
            try:
                self._process.terminate()
            except ProcessLookupError:
                pass
            # we can't await wait() here since stop is sync,
            # but the cancelled capture task will run its finally block
            # and do await self._cleanup_process()

    async def _cleanup_process(self) -> None:
        if self._process:
            try:
                self._process.terminate()
                try:
                    await asyncio.wait_for(self._process.wait(), timeout=1.0)
                except TimeoutError:
                    self._process.kill()
                    await self._process.wait()
            except ProcessLookupError:
                pass
            self._process = None

    def probe_health(self, source_id: str) -> HealthResult:
        return HealthResult(
            healthy=self._running,
            source_state="active" if self._running else "idle",
            signal_present=self._running,
            dropped_frames=0,
            last_error=None,
        )

    def start_pairing(self, timeout_seconds: int = 60) -> PairingResult:
        """Linux audio adapter doesn't support pairing."""
        return PairingResult(success=False, error="Pairing not supported by linux audio adapter")

    def stop_pairing(self) -> PairingResult:
        """Linux audio adapter doesn't support pairing."""
        return PairingResult(success=True)

    async def _capture_loop(self, source_id: str) -> None:
        # Check if pactl exists to decide between parec and arecord
        import shutil

        cmd = []

        if shutil.which("parec"):
            # Use pulse audio
            if source_id == "default":
                cmd = ["parec", "--format=s16le", "--rate=48000", "--channels=2"]
            else:
                cmd = ["parec", f"--device={source_id}", "--format=s16le", "--rate=48000", "--channels=2"]
        elif shutil.which("arecord"):
            # Use ALSA
            if source_id == "default":
                cmd = ["arecord", "-f", "S16_LE", "-r", "48000", "-c", "2", "-t", "raw"]
            else:
                cmd = ["arecord", "-D", source_id, "-f", "S16_LE", "-r", "48000", "-c", "2", "-t", "raw"]
        else:
            logger.error("Neither parec nor arecord found, cannot capture audio")
            self._running = False
            return

        try:
            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            if self._process.stdout is None:
                logger.error("Failed to open stdout for capture process")
                return

            # Read 48kHz, 2 channels, 16-bit = 4 bytes per sample
            # Let's read 10ms chunks = 480 samples = 1920 bytes
            chunk_size = 1920

            pts_ns = 0
            duration_ns = int(480 * 1_000_000_000 / 48000)  # 10ms

            while self._running:
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
            # Reached EOF normally
            pass
        except asyncio.CancelledError:
            # Task was cancelled
            pass
        except Exception as e:
            logger.error(f"Error capturing audio: {e}")
            if self._frame_sink:
                self._frame_sink.on_error(e)
        finally:
            self._running = False
            await self._cleanup_process()
