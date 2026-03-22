"""Central metrics registry for the bridge."""

import threading


class MetricsRegistry:
    """A simple thread-safe registry for lightweight counters."""

    def __init__(self) -> None:
        self._counters: dict[str, int] = {}
        self._lock = threading.Lock()

    def increment(self, name: str, value: int = 1) -> None:
        """Increment a counter by the given value."""
        with self._lock:
            self._counters[name] = self._counters.get(name, 0) + value

    def get_snapshot(self) -> dict[str, int]:
        """Return a snapshot of all current counters."""
        with self._lock:
            return self._counters.copy()

    def reset(self) -> None:
        """Reset all counters to zero."""
        with self._lock:
            self._counters.clear()


# Global instance for easy access if needed, though app-state is preferred
_global_registry = MetricsRegistry()


def get_global_metrics() -> MetricsRegistry:
    """Get the global metrics registry instance."""
    return _global_registry
