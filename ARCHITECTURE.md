# Architecture Documentation

This document describes the high-level architecture of the IKEA Symfonisk Gateway and defines the boundaries between its components.

## Component Overview

The project is organized as a monorepo with the following packages:

- `ingress_sdk/`: The core contract for audio input. Defines protocols (`IngressAdapter`, `FrameSink`) and shared types (`AudioFrame`, `SourceDescriptor`).
- `bridge_core/`: The central orchestration engine. Manages sessions, the audio pipeline (FFmpeg), source/target registries, and provides the REST API/SSE event bus.
- `shared/`: Minimal common utilities and constants used across all packages.
- `adapters/`: Concrete implementations of `IngressAdapter`.
    - `synthetic/`: Test adapter generating sine waves.
    - `linux_audio/`: Captures system audio on Linux.
    - `linux_bluetooth/`: Manages Bluetooth pairing and A2DP ingest on Linux.
    - `windows_audio/`: Captures system output on Windows.
- `renderer_sonos/`: Implementation of audio output for Sonos and IKEA SYMFONISK speakers.
- `ui_web/`: The web-based dashboard for managing the gateway.
- `integration_homeassistant/`: Home Assistant custom component code.

## Dependency Rules

To maintain a clean architecture and avoid "dependency hell," the following rules are enforced:

1. **SDK Primacy**: Adapters MUST depend on `ingress_sdk` to implement their interfaces.
2. **Core Isolation**: `bridge_core` manages adapters but should interact with them through the `IngressAdapter` protocol.
3. **No Adapter Inter-dependency**: Adapters MUST NOT depend on other adapters (e.g., `linux_bluetooth` cannot depend on `linux_audio`).
4. **Shared is Leaf**: `shared` MUST NOT depend on any other package in the workspace.
5. **SDK is Leaf-ish**: `ingress_sdk` should only depend on `shared` or external libraries.
6. **No Circular Imports**: Circular imports between packages or within packages are strictly prohibited.

## Data Flow

1. **Audio Ingress**: An `IngressAdapter` captures raw audio and pushes `AudioFrame`s to a `FrameSink`.
2. **Orchestration**: `SessionManager` in `bridge_core` sets up the `FrameSink` (usually a `SessionFrameSink`) which feeds a `StreamPipeline`.
3. **Transcoding**: `StreamPipeline` uses FFmpeg to transform the raw PCM audio into the format required by the renderer (e.g., L16 for Sonos).
4. **Distribution**: `StreamPublisher` serves the transcoded audio over HTTP.
5. **Rendering**: `SonosRendererAdapter` instructs the physical speakers to pull the audio stream from the `StreamPublisher`.

## Boundary Enforcement

Boundary rules are enforced via CI using:
- `scripts/check_package_boundaries.py`: Validates `import` statements against the dependency rules.
- `scripts/check_circular_imports.py`: Ensures no import cycles exist.
- `mypy`: Ensures type-safe interactions at the boundaries.
