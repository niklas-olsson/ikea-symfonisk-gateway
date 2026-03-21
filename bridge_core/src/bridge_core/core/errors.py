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
