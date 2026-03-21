import sys
import time

sys.path.append("adapters/windows_audio/src")
sys.path.append("ingress_sdk/src")

from adapter_windows_audio.backends import PyAudioWPatchBackend, load_pyaudiowpatch, resolve_default_loopback_triplet

OBSERVATION_SECONDS = 5.0


class SmokeFrameSink:
    def __init__(self) -> None:
        self.frames = 0
        self.bytes_received = 0
        self.errors: list[str] = []

    def on_frame(self, data: bytes, pts_ns: int, duration_ns: int) -> None:
        del pts_ns, duration_ns
        self.frames += 1
        self.bytes_received += len(data)

    def on_error(self, error: Exception) -> None:
        self.errors.append(str(error))


def main() -> int:
    backend_module = load_pyaudiowpatch()
    if backend_module is None:
        print("PyAudioWPatch is not installed.")
        return 1

    host_api, render_device, loopback_device = resolve_default_loopback_triplet(backend_module)
    print(f"Selected host API: {host_api}")
    print(f"Default render device: {render_device}")
    print(f"Chosen loopback device: {loopback_device}")

    if render_device is None or loopback_device is None:
        print("Failed to resolve the default Windows loopback device.")
        return 1

    backend = PyAudioWPatchBackend(backend_module=backend_module)
    sink = SmokeFrameSink()
    start = backend.start("default", sink)
    print(f"Stream open/start result: success={start.success} backend={start.backend} code={start.code!r} message={start.message!r}")
    if not start.success:
        return 1

    print(f"Observing callbacks for {OBSERVATION_SECONDS:.1f} seconds. Play audio on Windows now.")
    time.sleep(OBSERVATION_SECONDS)

    diagnostics = backend.get_diagnostics_snapshot()
    health = backend.probe_health("default")
    print(f"Callback count: {diagnostics.callback_count}")
    print(f"Non-empty buffer count: {diagnostics.non_empty_buffer_count}")
    print(f"Samples received: {diagnostics.samples_received}")
    print(f"Raw bytes received: {diagnostics.raw_bytes_received}")
    print(f"Frames emitted: {diagnostics.frames_emitted}")
    print(f"First callback at: {diagnostics.first_callback_at}")
    print(f"Startup substate: {diagnostics.startup_substate}")
    print(f"Start viability: {backend.get_start_viability_snapshot()}")
    print(f"Health: {health.model_dump()}")
    if sink.errors:
        print(f"Sink errors: {sink.errors}")

    backend.stop(start.session_id)

    if diagnostics.callback_count == 0:
        print("No callbacks arrived within the observation window.")
        return 1
    if diagnostics.samples_received == 0:
        print("Callbacks fired but no samples were received.")
        return 1

    print("Windows loopback smoke test succeeded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
