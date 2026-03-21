import os
from unittest.mock import MagicMock, patch

import pytest
from bridge_core.stream.utils import resolve_ffmpeg_path


@pytest.fixture
def mock_config_store():
    return MagicMock()


def test_resolve_ffmpeg_path_config_store(mock_config_store, tmp_path):
    ffmpeg_file = tmp_path / "ffmpeg_config"
    ffmpeg_file.write_text("dummy")
    ffmpeg_file.chmod(0o755)

    mock_config_store.get.return_value = str(ffmpeg_file)

    assert resolve_ffmpeg_path(mock_config_store) == str(ffmpeg_file)
    mock_config_store.get.assert_called_once_with("ffmpeg_path")


def test_resolve_ffmpeg_path_env_var(tmp_path):
    ffmpeg_file = tmp_path / "ffmpeg_env"
    ffmpeg_file.write_text("dummy")
    ffmpeg_file.chmod(0o755)

    with patch.dict(os.environ, {"FFMPEG_PATH": str(ffmpeg_file)}):
        # Pass None for config_store to skip first check or ensure it returns None
        assert resolve_ffmpeg_path(None) == str(ffmpeg_file)


def test_resolve_ffmpeg_path_shutil_which():
    with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
        with patch.dict(os.environ, {}, clear=True):
            assert resolve_ffmpeg_path(None) == "/usr/bin/ffmpeg"


def test_resolve_ffmpeg_path_not_found():
    with patch("shutil.which", return_value=None):
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(RuntimeError, match="FFmpeg executable not found"):
                resolve_ffmpeg_path(None)


def test_resolve_ffmpeg_path_priority(mock_config_store, tmp_path):
    # Setup config store path
    config_file = tmp_path / "ffmpeg_config"
    config_file.write_text("dummy")
    config_file.chmod(0o755)
    mock_config_store.get.return_value = str(config_file)

    # Setup env path
    env_file = tmp_path / "ffmpeg_env"
    env_file.write_text("dummy")
    env_file.chmod(0o755)

    with patch.dict(os.environ, {"FFMPEG_PATH": str(env_file)}):
        with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
            # Should prefer config store
            assert resolve_ffmpeg_path(mock_config_store) == str(config_file)

            # Now make config store return None, should prefer env
            mock_config_store.get.return_value = None
            assert resolve_ffmpeg_path(mock_config_store) == str(env_file)

            # Now clear env, should use shutil.which
            with patch.dict(os.environ, {}, clear=True):
                assert resolve_ffmpeg_path(mock_config_store) == "/usr/bin/ffmpeg"
