import asyncio

import numpy as np
from adapter_synthetic import FRAME_SIZE, SyntheticAdapter
from ingress_sdk.types import SyntheticMode


class VerifyingSink:
    def __init__(self) -> None:
        self.frame_count = 0
        self.last_pts = -1
        self.errors: list[str] = []

    def on_frame(self, data: bytes, pts_ns: int, duration_ns: int) -> None:
        self.frame_count += 1

        # Verify frame size
        if len(data) != FRAME_SIZE:
            self.errors.append(f"Invalid frame size: {len(data)}, expected {FRAME_SIZE}")

        # Verify timing
        if pts_ns <= self.last_pts:
            self.errors.append(f"Non-monotonic PTS: {pts_ns} <= {self.last_pts}")
        self.last_pts = pts_ns

        # Verify content (not all zeros if SINE_WAVE)
        if data == b"\x00" * FRAME_SIZE:
            self.errors.append("Frame is all zeros for SINE_WAVE")

        # Verify stereo property (L==R in our implementation)
        samples = np.frombuffer(data, dtype="<i2")
        left = samples[0::2]
        right = samples[1::2]
        if not np.array_equal(left, right):
            self.errors.append("Left and Right channels are not equal")


async def run_verification() -> None:
    print("Starting Synthetic Adapter Gold-Standard Verification...")
    adapter = SyntheticAdapter()
    print(f"Adapter ID: {adapter.id()}")

    # 1. List
    sources = adapter.list_sources()
    if not sources:
        print("FAILED: No sources listed")
        return
    source_id = sources[0].source_id
    print(f"Source ID: {source_id}")

    # 2. Health (Initial)
    health = adapter.probe_health(source_id)
    print(f"Initial Health: {health.source_state}")
    if health.source_state != "idle":
        print(f"FAILED: Initial state should be idle, got {health.source_state}")
        return

    # 3. Prepare
    prep = adapter.prepare(source_id)
    if not prep.success:
        print(f"FAILED: Preparation failed: {prep.message}")
        return
    print("Source prepared successfully")

    # 4. Health (After Prepare)
    health = adapter.probe_health(source_id)
    print(f"Post-Prepare Health: {health.source_state}")
    if health.source_state != "active":
        print(f"FAILED: State should be active after prepare, got {health.source_state}")
        return

    # 5. Start
    sink = VerifyingSink()
    adapter.set_mode(SyntheticMode.SINE_WAVE)
    start_res = adapter.start(source_id, sink)
    if not start_res.success:
        print("FAILED: Start failed")
        return
    print(f"Session started: {start_res.session_id}")

    # 6. Wait for data
    await asyncio.sleep(0.5)

    # 7. Health (Running)
    health = adapter.probe_health(source_id)
    print(f"Running Health: {health.source_state}, Signal: {health.signal_present}")
    if not health.signal_present:
        print("FAILED: Signal not present while running")

    # 8. Stop
    adapter.stop(start_res.session_id)
    print("Session stopped")

    # 9. Final Health
    health = adapter.probe_health(source_id)
    print(f"Final Health: {health.source_state}")
    if health.source_state != "idle":
        print(f"FAILED: Final state should be idle, got {health.source_state}")

    # 10. Report findings
    print(f"Frames captured: {sink.frame_count}")
    if sink.errors:
        print("ERRORS FOUND:")
        for err in sink.errors:
            print(f" - {err}")
    elif sink.frame_count > 0:
        print("VERIFICATION SUCCESSFUL: Synthetic adapter meets all gold-standard requirements.")
    else:
        print("FAILED: No frames captured.")


if __name__ == "__main__":
    asyncio.run(run_verification())
