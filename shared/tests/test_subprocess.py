"""Tests for SubprocessRunner."""

import subprocess
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from shared.metrics import MetricsRegistry
from shared.subprocess import SubprocessRunner


def test_subprocess_runner_caching() -> None:
    metrics = MetricsRegistry()
    runner = SubprocessRunner(metrics=metrics)
    args = ["echo", "hello"]

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="hello\n", stderr="", returncode=0)

        # First call - cache miss
        res1 = runner.run(args, ttl=5)
        assert res1.stdout == "hello\n"
        assert mock_run.call_count == 1
        assert metrics.get_snapshot().get("subprocess_cache_miss_count") == 1
        assert metrics.get_snapshot().get("subprocess_cache_hit_count") is None

        # Second call - cache hit
        res2 = runner.run(args, ttl=5)
        assert res2.stdout == "hello\n"
        assert mock_run.call_count == 1  # Still 1
        assert metrics.get_snapshot().get("subprocess_cache_hit_count") == 1


def test_subprocess_runner_ttl_expiration() -> None:
    runner = SubprocessRunner()
    args = ["echo", "hello"]

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="hello\n", stderr="", returncode=0)

        # First call
        runner.run(args, ttl=0.1)
        assert mock_run.call_count == 1

        # Wait for expiration
        time.sleep(0.2)

        # Second call - should be a miss due to expiration
        runner.run(args, ttl=0.1)
        assert mock_run.call_count == 2


def test_subprocess_runner_invalidation() -> None:
    runner = SubprocessRunner()
    args = ["echo", "hello"]

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="hello\n", stderr="", returncode=0)

        runner.run(args, ttl=5)
        assert mock_run.call_count == 1

        runner.invalidate(args)

        runner.run(args, ttl=5)
        assert mock_run.call_count == 2


def test_subprocess_runner_invalidate_prefix() -> None:
    runner = SubprocessRunner()

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="output", stderr="", returncode=0)

        runner.run(["pactl", "list", "sources"], ttl=5)
        runner.run(["pactl", "list", "cards"], ttl=5)
        runner.run(["ls", "-l"], ttl=5)

        assert mock_run.call_count == 3

        runner.invalidate_by_prefix(["pactl"])

        runner.run(["pactl", "list", "sources"], ttl=5)
        assert mock_run.call_count == 4

        runner.run(["pactl", "list", "cards"], ttl=5)
        assert mock_run.call_count == 5

        runner.run(["ls", "-l"], ttl=5)
        assert mock_run.call_count == 5  # Still 5, ls not invalidated


def test_subprocess_runner_retries() -> None:
    runner = SubprocessRunner()
    args = ["fail"]

    with patch("subprocess.run") as mock_run:
        # Fail twice, then succeed
        mock_run.side_effect = [
            subprocess.SubprocessError("fail 1"),
            subprocess.SubprocessError("fail 2"),
            MagicMock(stdout="success", stderr="", returncode=0),
        ]

        # Should succeed on 3rd attempt
        res = runner.run(args, retries=2, backoff_base=0.1)
        assert res.stdout == "success"
        assert mock_run.call_count == 3


def test_subprocess_runner_retries_failure() -> None:
    runner = SubprocessRunner()
    args = ["fail"]

    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.SubprocessError("always fail")

        with pytest.raises(subprocess.SubprocessError):
            runner.run(args, retries=2, backoff_base=0.1)

        assert mock_run.call_count == 3


@pytest.mark.asyncio
async def test_subprocess_runner_async_caching() -> None:
    metrics = MetricsRegistry()
    runner = SubprocessRunner(metrics=metrics)
    args = ["echo", "hello"]

    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        mock_process = MagicMock()
        mock_process.communicate = AsyncMock(return_value=(b"hello\n", b""))
        mock_process.returncode = 0
        mock_exec.return_value = mock_process

        # First call
        res1 = await runner.run_async(args, ttl=5)
        assert res1.stdout == "hello\n"
        assert mock_exec.call_count == 1
        assert metrics.get_snapshot().get("subprocess_cache_miss_count") == 1

        # Second call
        res2 = await runner.run_async(args, ttl=5)
        assert res2.stdout == "hello\n"
        assert mock_exec.call_count == 1
        assert metrics.get_snapshot().get("subprocess_cache_hit_count") == 1


@pytest.mark.asyncio
async def test_subprocess_runner_async_retries() -> None:
    runner = SubprocessRunner()
    args = ["fail"]

    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        mock_process = MagicMock()
        mock_process.communicate = AsyncMock(return_value=(b"success", b""))
        mock_process.returncode = 0

        # Fail once then succeed
        mock_exec.side_effect = [Exception("fail 1"), mock_process]

        res = await runner.run_async(args, retries=1, backoff_base=0.1)
        assert res.stdout == "success"
        assert mock_exec.call_count == 2
