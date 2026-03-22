"""Shared subprocess execution layer with caching, metrics, and invalidation."""

from __future__ import annotations

import asyncio
import logging
import subprocess
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from shared.metrics import MetricsRegistry

logger = logging.getLogger(__name__)


@dataclass
class CacheEntry:
    """Represents a cached subprocess result."""

    stdout: str
    stderr: str
    returncode: int
    timestamp: float
    ttl: float

    def is_expired(self, now: float) -> bool:
        """Check if the cache entry is expired."""
        return now > (self.timestamp + self.ttl)


class SubprocessRunner:
    """Executes subprocesses with TTL caching, retries, and metrics."""

    def __init__(self, metrics: MetricsRegistry | None = None) -> None:
        self._metrics = metrics
        self._cache: dict[tuple[str, ...], CacheEntry] = {}

    def run(
        self,
        args: list[str],
        ttl: float = 0,
        timeout: float | None = None,
        retries: int = 0,
        backoff_base: float = 2.0,
        **kwargs: Any,
    ) -> subprocess.CompletedProcess[str]:
        """Run a command synchronously with optional caching and retries."""
        key = tuple(args)
        now = time.time()

        if ttl > 0 and key in self._cache:
            entry = self._cache[key]
            if not entry.is_expired(now):
                if self._metrics:
                    self._metrics.increment("subprocess_cache_hit_count")
                return subprocess.CompletedProcess(
                    args=args,
                    returncode=entry.returncode,
                    stdout=entry.stdout,
                    stderr=entry.stderr,
                )

        if ttl > 0 and self._metrics:
            self._metrics.increment("subprocess_cache_miss_count")

        attempt = 0
        while True:
            attempt += 1
            if self._metrics:
                self._metrics.increment("subprocess_execution_count")

            start_time = time.time()
            try:
                # Ensure we capture output if we want to cache it
                if ttl > 0:
                    kwargs["capture_output"] = True
                    kwargs["text"] = True

                result = subprocess.run(args, timeout=timeout, **kwargs)

                if ttl > 0:
                    self._cache[key] = CacheEntry(
                        stdout=result.stdout,
                        stderr=result.stderr,
                        returncode=result.returncode,
                        timestamp=now,
                        ttl=ttl,
                    )
                return result

            except (subprocess.SubprocessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
                if self._metrics:
                    if isinstance(e, subprocess.TimeoutExpired):
                        self._metrics.increment("subprocess_timeout_count")
                    else:
                        self._metrics.increment("subprocess_error_count")

                if attempt > retries:
                    logger.error(f"Command failed after {attempt} attempts: {' '.join(args)}: {e}")
                    raise

                wait_time = backoff_base ** (attempt - 1)
                logger.warning(f"Command failed (attempt {attempt}/{retries + 1}): {' '.join(args)}: {e}. Retrying in {wait_time}s...")
                time.sleep(wait_time)
            finally:
                duration = time.time() - start_time
                if duration > 1.0:
                    logger.warning(f"Slow subprocess: {' '.join(args)} took {duration:.2f}s")

    async def run_async(
        self,
        args: list[str],
        ttl: float = 0,
        timeout: float | None = None,
        retries: int = 0,
        backoff_base: float = 2.0,
        **kwargs: Any,
    ) -> subprocess.CompletedProcess[str]:
        """Run a command asynchronously with optional caching and retries."""
        key = tuple(args)
        now = time.time()

        if ttl > 0 and key in self._cache:
            entry = self._cache[key]
            if not entry.is_expired(now):
                if self._metrics:
                    self._metrics.increment("subprocess_cache_hit_count")
                return subprocess.CompletedProcess(
                    args=args,
                    returncode=entry.returncode,
                    stdout=entry.stdout,
                    stderr=entry.stderr,
                )

        if ttl > 0 and self._metrics:
            self._metrics.increment("subprocess_cache_miss_count")

        attempt = 0
        while True:
            attempt += 1
            if self._metrics:
                self._metrics.increment("subprocess_execution_count")

            start_time = time.time()
            try:
                # Adapting some kwargs for create_subprocess_exec if provided
                env = kwargs.get("env")
                cwd = kwargs.get("cwd")

                process = await asyncio.create_subprocess_exec(
                    *args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                    cwd=cwd,
                )

                try:
                    stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=timeout)
                except TimeoutError:
                    process.kill()
                    await process.wait()
                    raise TimeoutError(f"Async command timed out after {timeout}s: {' '.join(args)}")

                stdout = stdout_bytes.decode()
                stderr = stderr_bytes.decode()
                returncode = process.returncode or 0

                if ttl > 0:
                    self._cache[key] = CacheEntry(
                        stdout=stdout,
                        stderr=stderr,
                        returncode=returncode,
                        timestamp=now,
                        ttl=ttl,
                    )

                return subprocess.CompletedProcess(
                    args=args,
                    returncode=returncode,
                    stdout=stdout,
                    stderr=stderr,
                )

            except Exception as e:
                if self._metrics:
                    if isinstance(e, (asyncio.TimeoutError, TimeoutError)):
                        self._metrics.increment("subprocess_timeout_count")
                    else:
                        self._metrics.increment("subprocess_error_count")

                if attempt > retries:
                    logger.error(f"Async command failed after {attempt} attempts: {' '.join(args)}: {e}")
                    raise

                wait_time = backoff_base ** (attempt - 1)
                logger.warning(
                    f"Async command failed (attempt {attempt}/{retries + 1}): {' '.join(args)}: {e}. Retrying in {wait_time}s..."
                )
                await asyncio.sleep(wait_time)
            finally:
                duration = time.time() - start_time
                if duration > 1.0:
                    logger.warning(f"Slow async subprocess: {' '.join(args)} took {duration:.2f}s")

    def invalidate(self, args: list[str] | None = None) -> None:
        """Invalidate cache for a specific command or all commands."""
        if args is None:
            self._cache.clear()
            logger.debug("Subprocess cache cleared")
        else:
            key = tuple(args)
            if key in self._cache:
                del self._cache[key]
                logger.debug(f"Subprocess cache invalidated for: {' '.join(args)}")

    def invalidate_by_prefix(self, prefix: list[str]) -> None:
        """Invalidate all cache entries starting with the given prefix."""
        prefix_tuple = tuple(prefix)
        keys_to_remove = [k for k in self._cache.keys() if k[: len(prefix_tuple)] == prefix_tuple]
        for k in keys_to_remove:
            del self._cache[k]
        if keys_to_remove:
            logger.debug(f"Subprocess cache invalidated for prefix: {' '.join(prefix)} ({len(keys_to_remove)} entries)")
