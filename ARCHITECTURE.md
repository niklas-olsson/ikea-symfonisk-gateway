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
    - `windows_audio/`: Captures system output on Windows (WASAPI Loopback).
- `renderer_sonos/`: Implementation of audio output for Sonos and IKEA SYMFONISK speakers.
- `ui_web/`: The web-based dashboard for managing the gateway.
- `integration_homeassistant/`: Home Assistant custom component code.

## Package Responsibilities

- **`bridge_core`**: The "brain" of the system.
    - `bridge_core.core.session_manager`: Orchestrates the entire lifecycle of a session.
    - `bridge_core.core.source_registry`: Discovers and manages ingress adapters.
    - `bridge_core.core.target_registry`: Discovers and manages renderer adapters.
    - `bridge_core.stream.pipeline`: Manages the FFmpeg process, jitter buffering, and keepalives.
    - `bridge_core.stream.publisher`: HTTP server that serves encoded audio to renderers.
- **`ingress_sdk`**: Defines the interface between the core and any audio source. If you want to add a new audio source, you implement an `IngressAdapter`.
- **`renderer_sonos`**: Specialized adapter that speaks the Sonos/UPnP protocol to control physical speakers and point them to the bridge's stream URL.

## Dependency Rules

To maintain a clean architecture and avoid "dependency hell," the following rules are enforced:

1. **SDK Primacy**: Adapters MUST depend on `ingress_sdk` to implement their interfaces.
2. **Core Isolation**: `bridge_core` manages adapters but should interact with them through the `IngressAdapter` protocol.
3. **No Adapter Inter-dependency**: Adapters MUST NOT depend on other adapters (e.g., `linux_bluetooth` cannot depend on `linux_audio`).
4. **Shared is Leaf**: `shared` MUST NOT depend on any other package in the workspace.
5. **SDK is Leaf-ish**: `ingress_sdk` should only depend on `shared` or external libraries.
6. **No Circular Imports**: Circular imports between packages or within packages are strictly prohibited. Boundary enforcement is checked via `scripts/check_package_boundaries.py`.

## Data Flow & Supported Flows

### 1. Discovery Flow
- On startup, `SourceRegistry` and `TargetRegistry` invoke `list_sources()`/`list_targets()` on all registered adapters.
- Changes are pushed via `EventBus` (`TOPOLOGY_CHANGED`, `RENDERER_DISCOVERY_CHANGED`).

### 2. Session Lifecycle (Creation -> Playback)
1. **Creation**: `SessionManager.create()` initializes a `Session` object with a unique ID.
2. **Start**: `SessionManager.start_session()`:
    - Calls `SourceRegistry.prepare_source()` and `start_source()`.
    - Initializes a `StreamPipeline` and `SessionFrameSink`.
    - Starts the FFmpeg process via the pipeline.
    - Calls `TargetRegistry.prepare_target()` and `play_stream()`.
    - The target (Sonos) is given the HTTP URL of the bridge's `StreamPublisher`.
3. **Playback**:
    - Adapter captures PCM -> `SessionFrameSink.on_frame()` -> `StreamPipeline.push_frame()`.
    - Pipeline feeds FFmpeg via `stdin` -> FFmpeg encodes -> Pipeline reads `stdout`.
    - Pipeline fans out encoded chunks to all connected HTTP subscribers (the renderer).

### 3. Healing & Recovery
- `SessionManager` monitors the health of the source and the delivery status (via `StreamPipeline` diagnostics).
- If a renderer disconnects or a source stalls, `SessionManager` can trigger a "Heal" (e.g., restarting playback or swapping the pipeline).

## Explicit Non-Goals for Public Release

- **Multi-Renderer Synchronization**: While multiple speakers can be grouped (via Sonos grouping), the bridge does not yet support synchronized playback across different *types* of renderers or multiple independent stream pipelines with sample-accurate sync.
- **External DSP/VST Support**: The current pipeline is a fixed capture-encode-publish flow. User-configurable DSP chains are not supported in this release.
- **Cloud Dependency**: This bridge is designed to be 100% local. Integration with cloud-based music services is out of scope.

## Boundary Enforcement

Boundary rules are enforced via CI using:
- `scripts/check_package_boundaries.py`: Validates `import` statements against the dependency rules.
- `scripts/check_circular_imports.py`: Ensures no import cycles exist.
- `mypy`: Ensures type-safe interactions at the boundaries.
