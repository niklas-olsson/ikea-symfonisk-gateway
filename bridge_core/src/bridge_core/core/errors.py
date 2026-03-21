"""Session error models and constants."""

from pydantic import BaseModel


class SessionError(BaseModel):
    """Structured error information for a session."""

    code: str
    message: str
    subsystem: str
    action: str
    details: dict[str, object] | None = None


# Error categories
MEDIA_ENGINE_NOT_FOUND = "media_engine_not_found"
PIPELINE_START_FAILED = "pipeline_start_failed"
RENDERER_PLAYBACK_FAILED = "renderer_playback_failed"
SOURCE_START_FAILED = "source_start_failed"
FRAME_INGEST_FAILED = "frame_ingest_failed"
SOURCE_ADAPTER_PLATFORM_MISMATCH = "source_adapter_platform_mismatch"
LINUX_AUDIO_BACKEND_MISSING = "linux_audio_backend_missing"
BLUETOOTH_PACTL_MISSING = "missing_pactl"
BLUETOOTH_AUDIO_TOOLS_MISSING = "missing_audio_tools"

# Windows-specific errors
WINDOWS_LOOPBACK_BACKEND_MISSING = "windows_loopback_backend_missing"
WINDOWS_LOOPBACK_PROBE_FAILED = "windows_loopback_probe_failed"
WINDOWS_LOOPBACK_START_FAILED = "windows_loopback_start_failed"
WINDOWS_LOOPBACK_DEVICE_NOT_FOUND = "windows_loopback_device_not_found"
WINDOWS_LOOPBACK_CAPTURE_STALLED = "windows_loopback_capture_stalled"
WINDOWS_OUTPUT_DEVICE_ACCESS_DENIED = "windows_output_device_access_denied"
WINDOWS_OUTPUT_DEVICE_SILENT = "windows_output_device_silent"
WINDOWS_AUDIO_LIBRARY_MISCONFIGURED = "windows_audio_library_misconfigured"

ERROR_DETAILS = {
    MEDIA_ENGINE_NOT_FOUND: {
        "message": "FFmpeg executable not found or not executable.",
        "subsystem": "media_engine",
        "action": "Install FFmpeg using your system's package manager (e.g., 'sudo apt install ffmpeg' or 'brew install ffmpeg') and ensure it is in your system PATH.",
    },
    PIPELINE_START_FAILED: {
        "message": "Failed to initialize the audio processing pipeline.",
        "subsystem": "pipeline",
        "action": "Check if FFmpeg is installed correctly and has permission to run. If the problem persists, check the bridge logs for the specific FFmpeg command failure.",
    },
    RENDERER_PLAYBACK_FAILED: {
        "message": "The speaker failed to start playing the stream.",
        "subsystem": "renderer",
        "action": "Verify that your SYMFONISK or Sonos speaker is powered on, connected to the network, and visible in the official Sonos app. Try restarting the speaker if it remains unresponsive.",
    },
    SOURCE_START_FAILED: {
        "message": "Failed to capture audio from the source.",
        "subsystem": "source",
        "action": "Check your audio hardware connections. If using a system audio capture, ensure no other application has exclusive control of the audio device and that the bridge has necessary OS-level permissions.",
    },
    FRAME_INGEST_FAILED: {
        "message": "Session started but no audio data was received from the source.",
        "subsystem": "pipeline",
        "action": "Ensure that audio is actually playing on your source device. For Bluetooth, verify the device is actively streaming audio to the bridge.",
    },
    SOURCE_ADAPTER_PLATFORM_MISMATCH: {
        "message": "Source platform does not match adapter platform.",
        "subsystem": "source_registry",
        "action": "Check source registration and adapter selection logic.",
    },
    LINUX_AUDIO_BACKEND_MISSING: {
        "message": "Required Linux audio capture tools (parec or arecord) are missing.",
        "subsystem": "source",
        "action": "Install PulseAudio (pulseaudio-utils) or ALSA (alsa-utils) on the host system.",
    },
    BLUETOOTH_PACTL_MISSING: {
        "message": "The 'pactl' utility is missing, which is required for Bluetooth audio control.",
        "subsystem": "source",
        "action": "Install the 'pulseaudio-utils' package on your Linux system.",
    },
    BLUETOOTH_AUDIO_TOOLS_MISSING: {
        "message": "Required PipeWire or PulseAudio utilities are missing for Bluetooth audio.",
        "subsystem": "source",
        "action": "Ensure 'pulseaudio-utils' or 'pipewire' packages are installed.",
    },
    WINDOWS_LOOPBACK_BACKEND_MISSING: {
        "message": "No Windows loopback-capable backend is installed.",
        "subsystem": "source",
        "action": "Install the supported Windows system-audio backend and restart the bridge.",
    },
    WINDOWS_LOOPBACK_PROBE_FAILED: {
        "message": "The Windows loopback backend could not initialize the default output device.",
        "subsystem": "source",
        "action": "Check the default Windows playback device and backend logs, then restart the bridge.",
    },
    WINDOWS_LOOPBACK_START_FAILED: {
        "message": "Failed to start Windows loopback capture.",
        "subsystem": "source",
        "action": "Check if another application has exclusive control of the audio device.",
    },
    WINDOWS_LOOPBACK_DEVICE_NOT_FOUND: {
        "message": "The default Windows output device was not found for loopback capture.",
        "subsystem": "source",
        "action": "Verify the device is connected and visible in Windows Sound Settings.",
    },
    WINDOWS_LOOPBACK_CAPTURE_STALLED: {
        "message": "Windows loopback capture started but did not establish an active stream.",
        "subsystem": "source",
        "action": "Start audio playback on the default Windows output device and check adapter logs for backend errors.",
    },
    WINDOWS_OUTPUT_DEVICE_ACCESS_DENIED: {
        "message": "Access to the Windows output device was denied.",
        "subsystem": "source",
        "action": "Check Windows Privacy settings for Microphone/Audio access and ensure the bridge has permission.",
    },
    WINDOWS_OUTPUT_DEVICE_SILENT: {
        "message": "Windows loopback started successfully but no audio was detected.",
        "subsystem": "source",
        "action": "Ensure that some audio is actually playing on the selected output device.",
    },
    WINDOWS_AUDIO_LIBRARY_MISCONFIGURED: {
        "message": "The Windows audio capture library (sounddevice/PortAudio) is misconfigured.",
        "subsystem": "source",
        "action": "Reinstall dependencies and ensure PortAudio is correctly installed.",
    },
}


def create_session_error(code: str, custom_message: str | None = None, details: dict[str, object] | None = None) -> SessionError:
    """Create a SessionError instance from a code."""
    error_details = ERROR_DETAILS.get(
        code,
        {
            "message": custom_message or "An unknown error occurred.",
            "subsystem": "unknown",
            "action": "Check system logs for more information.",
        },
    )
    return SessionError(
        code=code,
        message=custom_message or error_details["message"],
        subsystem=error_details["subsystem"],
        action=error_details["action"],
        details=details,
    )
