"""Utility functions for the audio pipeline."""

import os
import shutil
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bridge_core.core.config_store import ConfigStore

logger = logging.getLogger(__name__)

def resolve_ffmpeg_path(config_store: "ConfigStore | None" = None) -> str:
    """
    Resolves the FFmpeg executable path by checking:
    1. ConfigStore (key: 'ffmpeg_path')
    2. Environment variable 'FFMPEG_PATH'
    3. System PATH using shutil.which('ffmpeg')

    Raises:
        RuntimeError: If FFmpeg cannot be found.
    """
    # 1. Check ConfigStore
    if config_store:
        config_path = config_store.get("ffmpeg_path")
        if config_path:
            if os.path.isfile(config_path) and os.access(config_path, os.X_OK):
                logger.debug(f"Resolved FFmpeg path from ConfigStore: {config_path}")
                return config_path
            else:
                logger.warning(f"FFmpeg path from ConfigStore is not a valid executable: {config_path}")

    # 2. Check environment variable
    env_path = os.environ.get("FFMPEG_PATH")
    if env_path:
        if os.path.isfile(env_path) and os.access(env_path, os.X_OK):
            logger.debug(f"Resolved FFmpeg path from environment variable FFMPEG_PATH: {env_path}")
            return env_path
        else:
            logger.warning(f"FFmpeg path from FFMPEG_PATH environment variable is not a valid executable: {env_path}")

    # 3. Check system PATH
    system_path = shutil.which("ffmpeg")
    if system_path:
        logger.debug(f"Resolved FFmpeg path from system PATH: {system_path}")
        return system_path

    # Not found
    raise RuntimeError(
        "FFmpeg executable not found. "
        "Please install FFmpeg and ensure it's in your PATH, or "
        "configure the path via the 'FFMPEG_PATH' environment variable or "
        "the 'ffmpeg_path' configuration key."
    )
