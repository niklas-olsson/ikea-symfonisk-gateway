"""Windows backend implementations."""

from .base import WindowsSystemAudioBackend
from .models import BackendProbeResult, BackendStartupDiagnostics
from .null_backend import NullWindowsBackend
from .pyaudiowpatch_backend import PyAudioWPatchBackend, load_pyaudiowpatch, resolve_default_loopback_triplet

__all__ = [
    "BackendProbeResult",
    "BackendStartupDiagnostics",
    "NullWindowsBackend",
    "PyAudioWPatchBackend",
    "WindowsSystemAudioBackend",
    "load_pyaudiowpatch",
    "resolve_default_loopback_triplet",
]
