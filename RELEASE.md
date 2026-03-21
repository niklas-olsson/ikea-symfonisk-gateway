# Release Matrix and Go-Public Gate

This document defines the requirements and verification steps necessary for a public release of the IKEA Symfonisk Gateway.

## Release Matrix

| Component | Platform | Feature | Status | Verification |
|-----------|----------|---------|--------|--------------|
| **Linux Audio** | Linux | System Audio Capture (Pulse/ALSA) | Required | `verify_linux_audio.py` |
| **Linux Bluetooth**| Linux | A2DP Audio Ingest | Required | Manual Soak |
| **Linux Bluetooth**| Linux | Pairing & Trust Workflow | Required | Manual UI Test |
| **Linux Bluetooth**| Linux | Trusted Device Reconnect | Required | Manual |
| **Windows Audio** | Windows | System Output Capture (WASAPI) | Required | `verify_windows_loopback.py` |
| **Discovery** | All | Sonos/SSDP Discovery | Required | `verify_source_discovery.py` |
| **Streaming** | All | PCM/L16 Streaming over HTTP | Required | Automated Tests |
| **Core API** | All | Session Management (Start/Stop) | Required | Automated Tests |
| **Core API** | All | SSE Event Bus | Required | Automated Tests |
| **Web UI** | All | Source/Target Dashboard | Required | Manual UI Test |
| **Home Assistant** | All | Media Player Integration | Required | Manual |

## Manual Soak Protocol

Before any public release, the following soak tests must be performed:

1. **Long-run Linux Stream**: 4 hours of continuous playback from a Bluetooth source to a Sonos target.
   - Criteria: Zero fatal pipeline errors, < 500ms drift, no service crashes.
2. **Long-run Windows Stream**: 4 hours of continuous playback from a Windows System Output source to a Sonos target.
   - Criteria: Zero fatal pipeline errors, no service crashes.
3. **Reconnect Stability**: 10 consecutive disconnect/reconnect cycles of a Bluetooth source.
   - Criteria: 10/10 successful session restorations (if preferred target set) or source re-discovery.

## Codebase Hygiene Gate

The following automated checks must pass:

1. **Linting**: `ruff check .` must return zero errors.
2. **Formatting**: `ruff format . --check` must return zero errors.
3. **Type Checking**: `mypy .` must return zero errors (no `--ignore-missing-imports` or `|| true` gates).
4. **Unit Tests**: `pytest .` must have 100% pass rate.
5. **Circular Imports**: `scripts/check_circular_imports.py` must pass.
6. **Package Boundaries**: `scripts/check_package_boundaries.py` must verify no illegal dependencies.

## Go-Public Branch Gate

A "Go-Public" decision is only granted if:

1. **Release Matrix** is 100% green.
2. **Soak Protocol** is completed and signed off by a maintainer.
3. **Hygiene Gate** is fully enforced in CI on the `main` branch.
4. **Architecture Documentation** is current and reflects the actual package boundaries.
5. **README** contains accurate installation and configuration instructions for all supported platforms.
