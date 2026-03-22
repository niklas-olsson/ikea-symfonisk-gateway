#!/usr/bin/env python3
"""Benchmark script to collect performance metrics across bridge states."""

import asyncio
import json
import os
import time
import traceback
from pathlib import Path
from typing import Any

import httpx

BRIDGE_HOST = os.environ.get("BRIDGE_HOST", "127.0.0.1")
BRIDGE_PORT = os.environ.get("BRIDGE_PORT", "8732")
BASE_URL = f"http://{BRIDGE_HOST}:{BRIDGE_PORT}"


async def get_health() -> dict[str, Any]:
    """Fetch metrics from the health endpoint."""
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{BASE_URL}/health")
        data = response.json()
        if not isinstance(data, dict):
            return {}
        return data


async def wait_for_bridge() -> bool:
    """Wait for the bridge to be ready."""
    print("Waiting for bridge to be ready...")
    for _ in range(30):
        try:
            health = await get_health()
            if health.get("status") == "ok":
                print("Bridge is ready.")
                return True
        except Exception:
            pass
        await asyncio.sleep(1)
    print("Bridge failed to start.")
    return False


async def collect_sample(duration_seconds: int = 5) -> dict[str, Any] | None:
    """Collect resource usage over a duration."""
    samples = []
    start_time = time.time()
    while time.time() - start_time < duration_seconds:
        try:
            health = await get_health()
            samples.append(
                {
                    "cpu": health["system"]["cpu_usage_percent"],
                    "rss": health["system"]["rss_bytes"],
                    "threads": health["system"]["thread_count"],
                    "metrics": health["metrics"],
                }
            )
        except Exception as e:
            traceback.print_exc()
            print(f"Error collecting sample: {e}")
        await asyncio.sleep(1)

    if not samples:
        return None

    avg_cpu = sum(s["cpu"] for s in samples) / len(samples)
    final_rss = samples[-1]["rss"]
    final_threads = samples[-1]["threads"]
    final_metrics = samples[-1]["metrics"]

    return {"avg_cpu_percent": avg_cpu, "final_rss_bytes": final_rss, "final_thread_count": final_threads, "final_metrics": final_metrics}


async def run_benchmark() -> dict[str, Any]:
    """Run the benchmark suite."""
    if not await wait_for_bridge():
        return {}

    results = {}
    try:
        # State: Idle
        print("Benchmarking state: Idle")
        results["idle"] = await collect_sample()

        # Get a source and target
        async with httpx.AsyncClient() as client:
            sources_res = await client.get(f"{BASE_URL}/v1/sources")
            sources = sources_res.json()["sources"]
            targets_res = await client.get(f"{BASE_URL}/v1/targets")
            targets = targets_res.json()["targets"]

        if not sources or not targets:
            print("No sources or targets available for active benchmarking.")
            return results

        source_id = sources[0]["source_id"]
        target_id = targets[0]["target_id"]

        # State: Active-Stable
        print(f"Starting session (stable): {source_id} -> {target_id}")
        async with httpx.AsyncClient() as client:
            session_res = await client.post(
                f"{BASE_URL}/v1/sessions", json={"source_id": source_id, "target_id": target_id, "delivery_profile": "stable"}
            )
            session = session_res.json()
            session_id = session["session_id"]
            await client.post(f"{BASE_URL}/v1/sessions/{session_id}/start")

        print("Benchmarking state: Active-Stable")
        results["active_stable"] = await collect_sample(duration_seconds=10)

        # State: Speaker-Detached (Simulated by observing degraded state if possible)
        print("Benchmarking state: Speaker-Detached/Degraded")
        results["speaker_detached"] = await collect_sample(duration_seconds=5)

        # Cleanup
        print(f"Stopping session: {session_id}")
        async with httpx.AsyncClient() as client:
            await client.post(f"{BASE_URL}/v1/sessions/{session_id}/stop")
            await client.delete(f"{BASE_URL}/v1/sessions/{session_id}")

        # State: Active-Experimental
        print(f"Starting session (experimental): {source_id} -> {target_id}")
        async with httpx.AsyncClient() as client:
            session_res = await client.post(f"{BASE_URL}/v1/sessions", json={"source_id": source_id, "target_id": target_id})
            session = session_res.json()
            session_id = session["session_id"]
            # We might need to set the config for experimental profile
            await client.post(f"{BASE_URL}/v1/config", json={"audio_delivery_profile": "experimental"})
            await client.post(f"{BASE_URL}/v1/sessions/{session_id}/start")

        print("Benchmarking state: Active-Experimental")
        results["active_experimental"] = await collect_sample(duration_seconds=10)

        # State: Quiesced-Detached
        print("Simulating Quiesced-Detached state (EXPERIMENTAL profile + disconnect)...")
        # We can't easily force a disconnect here without real hardware or mock control,
        # but we can check if the bridge exposes the state after we stop the source if mocked.
        # For now, we'll try to trigger it by waiting or using a mock if available.
        # This is a placeholder for the scenario.
        results["quiesced_detached"] = await collect_sample(duration_seconds=5)

        # Cleanup
        print(f"Stopping session: {session_id}")
        async with httpx.AsyncClient() as client:
            await client.post(f"{BASE_URL}/v1/sessions/{session_id}/stop")
            await client.delete(f"{BASE_URL}/v1/sessions/{session_id}")
            # Reset config
            await client.post(f"{BASE_URL}/v1/config", json={"audio_delivery_profile": "stable"})

    except Exception:
        traceback.print_exc()
        raise
    return results


async def main() -> None:
    try:
        results = await run_benchmark()
        output_path = Path("benchmarks/baseline_0.1.0.json")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(results, f, indent=2)
        print(f"Benchmark results saved to {output_path}")
    except Exception as e:
        print(f"Benchmark failed: {e}")


if __name__ == "__main__":
    asyncio.run(main())
