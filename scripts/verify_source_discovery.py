import asyncio
import sys
from unittest.mock import patch

# Add relevant paths to sys.path
sys.path.append("adapters/linux_audio/src")
sys.path.append("bridge_core/src")
sys.path.append("ingress_sdk/src")

from adapter_linux_audio import LinuxAudioAdapter
from bridge_core.core import EventBus, SourceRegistry


async def main() -> None:
    event_bus = EventBus()
    registry = SourceRegistry(event_bus)

    # Mock shutil.which to pretend pactl is not found to avoid pactl subscribe blocking
    with patch("shutil.which", return_value=None):
        adapter = LinuxAudioAdapter()

    registry.register_adapter(
        adapter_id=adapter.id(),
        platform=adapter.platform(),
        version="0.1.0",
        capabilities=adapter.capabilities(),
        sources=adapter.list_sources(),
        adapter_instance=adapter,
    )

    sources = registry.list_sources()
    print(f"Discovered {len(sources)} sources")
    for s in sources:
        print(f" - {s.display_name} (ID: {s.source_id})")

    if any(s.source_id == "default" for s in sources):
        print("Success: Found default source fallback")
    elif len(sources) > 0:
        print("Success: Found real hardware sources")
    else:
        print("Error: No sources found")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
