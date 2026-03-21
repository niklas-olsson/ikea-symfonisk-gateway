"""Session error models and constants."""

from pydantic import BaseModel


class SessionError(BaseModel):
    """Structured error information for a session."""

    code: str
    message: str
    subsystem: str
    action: str


# Error categories
MEDIA_ENGINE_NOT_FOUND = "media_engine_not_found"
PIPELINE_START_FAILED = "pipeline_start_failed"
RENDERER_PLAYBACK_FAILED = "renderer_playback_failed"
SOURCE_START_FAILED = "source_start_failed"
FRAME_INGEST_FAILED = "frame_ingest_failed"

# Windows-specific errors
WINDOWS_LOOPBACK_NOT_SUPPORTED = "windows_loopback_not_supported"
WINDOWS_LOOPBACK_START_FAILED = "windows_loopback_start_failed"
WINDOWS_OUTPUT_DEVICE_NOT_FOUND = "windows_output_device_not_found"
WINDOWS_OUTPUT_DEVICE_ACCESS_DENIED = "windows_output_device_access_denied"
WINDOWS_OUTPUT_DEVICE_SILENT = "windows_output_device_silent"
WINDOWS_AUDIO_LIBRARY_MISCONFIGURED = "windows_audio_library_misconfigured"

ERROR_DETAILS = {
    MEDIA_ENGINE_NOT_FOUND: {
        "message": "FFmpeg executable not found or not executable.",
        "subsystem": "media_engine",
        "action": "Install FFmpeg and ensure it's in your PATH or configure 'ffmpeg_path'.",
    },
    PIPELINE_START_FAILED: {
        "message": "Failed to initialize the audio processing pipeline.",
        "subsystem": "pipeline",
        "action": "Check system logs for FFmpeg process errors.",
    },
    RENDERER_PLAYBACK_FAILED: {
        "message": "The speaker failed to start playing the stream.",
        "subsystem": "renderer",
        "action": "Ensure the speaker is reachable and supports the selected stream profile.",
    },
    SOURCE_START_FAILED: {
        "message": "Failed to capture audio from the source.",
        "subsystem": "source",
        "action": "Verify source hardware connection and permissions.",
    },
    FRAME_INGEST_FAILED: {
        "message": "Session started but no audio data was received from the source.",
        "subsystem": "pipeline",
        "action": "Check source signal and adapter logs.",
    },
    WINDOWS_LOOPBACK_NOT_SUPPORTED: {
        "message": "Windows system audio capture requires wasapi_loopback or similar Windows-native backend",
        "subsystem": "source",
        "action": "Ensure you are on Windows 10 or later and your audio driver supports loopback.",
    },
    WINDOWS_LOOPBACK_START_FAILED: {
        "message": "Failed to start Windows loopback capture.",
        "subsystem": "source",
        "action": "Check if another application has exclusive control of the audio device.",
    },
    WINDOWS_OUTPUT_DEVICE_NOT_FOUND: {
        "message": "The specified Windows output device was not found.",
        "subsystem": "source",
        "action": "Verify the device is connected and visible in Windows Sound Settings.",
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


def create_session_error(code: str, custom_message: str | None = None) -> SessionError:
    """Create a SessionError instance from a code."""
    details = ERROR_DETAILS.get(
        code,
        {
            "message": custom_message or "An unknown error occurred.",
            "subsystem": "unknown",
            "action": "Check system logs for more information.",
        },
    )
    return SessionError(
        code=code,
        message=custom_message or details["message"],
        subsystem=details["subsystem"],
        action=details["action"],
    )
