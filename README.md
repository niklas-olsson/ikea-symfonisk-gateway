# IKEA Symfonisk Gateway

Use your IKEA Symfonisk speakers as audio input targets for any source—turntables, game consoles, or anything with line-level or Bluetooth output.

## Quick Start

```bash
# 1. Clone and install
git clone https://github.com/your-repo/ikea-symfonisk-gateway.git
cd ikea-symfonisk-gateway
uv sync

# 2. Configure
cp .env.example .env
# Edit .env with your settings (BRIDGE_AUTH_TOKEN recommended)

# 3. Run
uv run python -m bridge_core
```

The gateway starts at `http://localhost:8732` (API) and `http://localhost:8080` (audio stream).

---

## Prerequisites

### Required

- **Python 3.12+** — [Install via pyenv or python.org](https://www.python.org/downloads/)
- **uv** — Fast Python package manager
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```
- **FFmpeg** — Required for audio transcoding.
  - **Linux**: `sudo apt install ffmpeg`
  - **macOS**: `brew install ffmpeg`
  - **Windows**: Download from [ffmpeg.org](https://ffmpeg.org/download.html) and add `bin` to your PATH.

### Optional

- **Docker & Docker Compose** — For containerized deployment
- **Home Assistant** — For home automation integration (v2024.1+)
- **Linux with PulseAudio/BlueZ** — For Linux audio/Bluetooth adapters

### Network Requirements

- The gateway and your Sonos/Symfonisk speakers must be on the same local network
- Sonos devices are discovered via SSDP multicast—ensure your network allows this

---

## Installation

### 1. Clone the Repository

```bash
git clone https://github.com/your-repo/ikea-symfonisk-gateway.git
cd ikea-symfonisk-gateway
```

### 2. Install Dependencies

```bash
uv sync
```

This installs all packages from the workspace, including:
- `bridge_core` — Main FastAPI application
- `ui_web` — Web interface
- `renderer_sonos` — Sonos/Symfonisk speaker adapter
- `adapter-linux-audio` — Linux audio input adapter
- `adapter-linux-bluetooth` — Linux Bluetooth adapter
- `integration_homeassistant` — Home Assistant integration

### 3. Configure Environment

```bash
cp .env.example .env
```

Edit `.env` with your settings (see [Configuration](#configuration) below).

---

## Configuration

Environment variables control the gateway behavior:

| Variable | Default | Description |
|----------|---------|-------------|
| `BRIDGE_HOST` | `localhost` | Host to bind the API server |
| `BRIDGE_PORT` | `8732` | Port for the REST API |
| `BRIDGE_API_PORT` | `8732` | Port for API endpoints |
| `BRIDGE_STREAM_PORT` | `8080` | Port for audio streaming |
| `BRIDGE_AUTH_TOKEN` | _(empty)_ | Bearer token for API authentication. **Set this for production!** |
| `FFMPEG_PATH` | `ffmpeg` | Explicit path to FFmpeg executable |
| `LOG_LEVEL` | `INFO` | Logging verbosity: `DEBUG`, `INFO`, `WARNING`, `ERROR` |

### FFmpeg Configuration

The bridge requires FFmpeg to encode audio streams. It resolves the executable path in the following order:
1.  **Config API**: `PUT /v1/config/ffmpeg_path` with `{"value": "/path/to/ffmpeg"}`
2.  **Environment Variable**: `FFMPEG_PATH` (e.g., in `.env` file)
3.  **System PATH**: Default `shutil.which("ffmpeg")` resolution

### Production Security

Always set `BRIDGE_AUTH_TOKEN` when exposing the gateway:

```bash
BRIDGE_AUTH_TOKEN=$(openssl rand -hex 32)
```

---

## Running the Application

### Option A: Local Development

```bash
# Run with uvicorn
uv run python -m bridge_core

# Or explicitly
uv run uvicorn bridge_core.main:app --host 0.0.0.0 --port 8732
```

### Option B: Docker

```bash
# Build and run
docker-compose up -d

# View logs
docker-compose logs -f bridge

# Stop
docker-compose down
```

The Docker image exposes:
- `8732` — REST API
- `8080` — Audio stream

### Option C: Docker (Manual)

```bash
docker build -t symfonisk-gateway .
docker run -d \
  --name symfonisk-gateway \
  -p 8732:8732 \
  -p 8080:8080 \
  -e BRIDGE_HOST=0.0.0.0 \
  -e BRIDGE_AUTH_TOKEN=your-secret-token \
  -v ./config:/app/config \
  symfonisk-gateway
```

### Verify It's Running

```bash
curl http://localhost:8732/

# Expected response:
# {"service":"ikea-symfonisk-gateway","version":"0.1.0","status":"running"}
```

Check health endpoint:

```bash
curl http://localhost:8732/api/health
```

---

## Architecture

The gateway connects **audio sources** to **audio targets** via **sessions**:

```
┌─────────────┐     Session      ┌─────────────┐
│   Source    │ ───────────────► │   Target    │
│ (Turntable) │                  │ (Symfonisk) │
└─────────────┘                  └─────────────┘
       │                                │
  Adapter                           Adapter
       │                                │
  ┌────┴────────────────────────────────┴────┐
  │            Bridge Core                  │
  │  - Session Management                   │
  │  - Audio Streaming                      │
  │  - Event Bus                            │
  └─────────────────────────────────────────┘
```

### Adapters

**Source Adapters** capture audio input:
| Adapter | Platform | Description |
|---------|----------|-------------|
| `synthetic` | All | Test adapter generating sine waves |
| `linux-audio` | Linux | PulseAudio/ALSA line input |
| `linux-bluetooth` | Linux | Bluetooth audio input |
| `windows-audio` | Windows | Default system-output capture via WASAPI loopback |

**Target Adapters** output audio:
| Adapter | Platform | Description |
|---------|----------|-------------|
| `sonos` | All | Sonos/Symfonisk speakers via SoCo |

---

## Adapters

### Synthetic Adapter (Testing)

Included by default for testing without hardware:

```python
# Creates a test source that outputs a 440Hz sine wave
# No configuration needed
```

### Linux Audio Adapter

For capturing audio from a sound card or USB audio interface:

```bash
# Install dependencies (if not already)
sudo apt install libasound2-dev pulseaudio

# Ensure your audio device is detected
pactl list sources short
```

Configure in your `.env`:

```bash
ADAPTER_LINUX_AUDIO_SOURCE=<pulse-source-name>
# Example: ADAPTER_LINUX_AUDIO_SOURCE=alsa_input.usb-Audio-USB_Audio-Interface-00.analog-stereo
```

### Linux Bluetooth Adapter

For streaming audio via Bluetooth (e.g., from a phone or computer):

```bash
# Install dependencies
sudo apt install bluez pulseaudio-module-bluetooth

# Ensure Bluetooth is enabled
sudo systemctl enable bluetooth
sudo systemctl start bluetooth

# Pair your device (use bluetoothctl)
bluetoothctl
[bluetooth]# power on
[bluetooth]# scan on
[bluetooth]# pair XX:XX:XX:XX:XX:XX
[bluetooth]# connect XX:XX:XX:XX:XX:XX
[bluetooth]# trust XX:XX:XX:XX:XX:XX
```

### Windows Audio Adapter

For capturing the default Windows playback mix and relaying it to Sonos/SYMFONISK:

```bash
uv sync
uv run python -m bridge_core
```

- The Windows source exposed by the API is `windows-audio-adapter:system:default`.
- Its `source_type` is `system_output`.
- The Windows backend depends on `PyAudioWPatch` and is installed only on Windows.
- If the backend is missing or cannot probe the default output device, `/v1/sources` still lists the source in degraded form with backend diagnostics in `metadata`.
- Silent startup is allowed: the session can reach `playing` before Windows audio begins, and playback starts flowing once an application produces sound.

Configure in your `.env`:

```bash
ADAPTER_LINUX_BLUETOOTH_DEVICE=<bluetooth-device-mac>
```

---

## API Reference

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Service info |
| `GET` | `/health` | Health check |
| `GET` | `/v1/sources` | List available sources |
| `GET` | `/v1/targets` | List available targets |
| `POST` | `/v1/sessions` | Create a session (source → target) |
| `GET` | `/v1/sessions` | List active sessions |
| `POST` | `/v1/sessions/{id}/stop` | End a session |
| `GET` | `/v1/events` | SSE stream for events |

### Example: Create a Session

```bash
SOURCE_ID=$(curl -s http://localhost:8732/v1/sources | jq -r '.sources[0].source_id')

curl -X POST http://localhost:8732/v1/sessions \
  -H "Content-Type: application/json" \
  -d "{\"source_id\": \"${SOURCE_ID}\", \"target_id\": \"sonos://Kitchen\"}"
```

### Authentication

When `BRIDGE_AUTH_TOKEN` is set, include it in requests:

```bash
curl -H "Authorization: Bearer <TOKEN>" http://localhost:8732/v1/sources
```

---

## Home Assistant Integration

The gateway integrates with Home Assistant as a media player.

### Installation (HACS)

1. Install [HACS](https://hacs.xyz/) if not already installed
2. Add this repository as a custom repository in HACS
3. Install the "IKEA Symfonisk Gateway" integration
4. Restart Home Assistant

### Configuration (config.yaml)

```yaml
ikea_symfonisk_gateway:
  host: <gateway-ip>
  port: 8732
  token: <BRIDGE_AUTH_TOKEN>
```

Replace `<gateway-ip>` with your gateway's IP address and `<BRIDGE_AUTH_TOKEN>` with your auth token.

### Entities

After setup, you'll see:
- Media player entities for each Symfonisk speaker
- Source selection for choosing audio input
- Play/pause controls

---

## Troubleshooting

### Speakers Not Discovered

1. Ensure speakers are on the same network
2. Check that Sonos devices are responsive: `ping <speaker-ip>`
3. Enable Sonos debugging: Set `LOG_LEVEL=DEBUG` and check logs
4. Some networks block SSDP—try running on a less restricted subnet

### Audio Drops or Chops

1. Ensure stable network connection (wired Ethernet recommended)
2. Reduce network load
3. Check CPU usage on the gateway machine
4. Adjust stream buffer size (future feature)

### Docker Container Won't Start

```bash
# Check logs
docker-compose logs

# Common issues:
# - Port already in use: Change BRIDGE_PORT in .env
# - Permission denied: Check volume mounts
```

### Bluetooth Adapter Issues

```bash
# Check Bluetooth service
sudo systemctl status bluetooth

# Verify device is paired
bluetoothctl paired-devices

# Check audio profile
bluetoothctl info <device-mac>
```

### Reset Everything

```bash
# Stop all sessions
curl -X DELETE http://localhost:8732/api/sessions

# Restart gateway
# (for Docker: docker-compose restart)
```

### Debug Logging

```bash
# Enable debug output
LOG_LEVEL=DEBUG uv run python -m bridge_core

# View SSE events in real-time
curl -N http://localhost:8732/api/events
```

---

## Development

### Running Tests

```bash
uv run pytest .
```

### Code Quality

```bash
# Format code
uv run ruff format .

# Lint code
uv run ruff check .

# Type check
uv run mypy .
```

All three must pass before pushing:

```bash
uv run ruff format . && uv run ruff check . && uv run mypy . && uv run pytest .
```

### Pre-commit Hooks

Install git hooks for automatic validation:

```bash
./scripts/install-hooks.sh
```

This runs format/lint/type checks before each commit.

---

## License

MIT
