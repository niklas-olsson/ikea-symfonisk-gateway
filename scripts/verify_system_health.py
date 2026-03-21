import sys

import httpx


def verify_system_health(base_url: str = "http://localhost:8732") -> None:
    print(f"Starting SYMFONISK Bridge System Verification via {base_url}/health...")

    try:
        response = httpx.get(f"{base_url}/health", timeout=5.0)
        response.raise_for_status()
        health_data = response.json()
    except Exception as e:
        print(f"FAILED: Could not connect to bridge health endpoint: {e}")
        sys.exit(1)

    print("\n[1] Health Endpoint Structure")
    expected_keys = ["status", "version", "uptime", "system", "ffmpeg", "subsystems"]
    missing_keys = [key for key in expected_keys if key not in health_data]
    if missing_keys:
        print(f"FAILED: Missing keys in health response: {missing_keys}")
        sys.exit(1)
    print("SUCCESS: Health endpoint returned all expected diagnostic keys.")

    print("\n[2] System Information")
    system = health_data["system"]
    print(f" - OS: {system.get('os')}")
    print(f" - Arch: {system.get('arch')}")
    print(f" - Python: {system.get('python_version', '').splitlines()[0]}")
    if not all([system.get("os"), system.get("arch"), system.get("python_version")]):
        print("FAILED: Incomplete system information.")
        sys.exit(1)
    print("SUCCESS: System info is complete.")

    print("\n[3] FFmpeg Status")
    ffmpeg = health_data["ffmpeg"]
    print(f" - Path: {ffmpeg.get('path')}")
    print(f" - Available: {ffmpeg.get('available')}")
    if not ffmpeg.get("available"):
        print("WARNING: FFmpeg is reported as NOT available. Some features will not work.")
    else:
        print("SUCCESS: FFmpeg is available.")

    print("\n[4] Subsystems Summary")
    subsystems = health_data["subsystems"]
    print(f" - Registered Adapters: {subsystems.get('adapters')}")
    print(f" - Registered Sources: {subsystems.get('sources')}")
    print(f" - Registered Targets: {subsystems.get('targets')}")
    print(f" - Active Sessions: {subsystems.get('sessions')}")

    # Validation logic: we expect at least the Synthetic adapter to be present if bridge is initialized
    if subsystems.get("adapters", 0) < 1:
        print("FAILED: No adapters registered. Bridge might not be fully initialized.")
        sys.exit(1)
    print("SUCCESS: Subsystems summary is populated.")

    print("\n[5] Uptime Check")
    uptime = health_data["uptime"]
    print(f" - Uptime: {uptime:.2f} seconds")
    if uptime <= 0:
        print("FAILED: Uptime should be positive.")
        sys.exit(1)
    print("SUCCESS: Uptime is valid.")

    print("\nVERIFICATION COMPLETE: Bridge diagnostics are actionable and authoritative.")


if __name__ == "__main__":
    # If a port was specified as an argument
    url = "http://localhost:8732"
    if len(sys.argv) > 1:
        url = sys.argv[1]

    verify_system_health(url)
