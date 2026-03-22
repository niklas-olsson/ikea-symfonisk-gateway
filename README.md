# IKEA Symfonisk Gateway

Stream supported audio sources to IKEA SYMFONISK and Sonos speakers through a Linux or Windows bridge device.

Linux can capture local system audio and accept Bluetooth audio from supported devices such as phones and Bluetooth turntables. Windows currently captures system and app output from the host PC. The bridge relays that audio to speakers on the same local network.

- Linux: system audio capture, Bluetooth audio ingest and pairing workflow, trusted-device reconnect path
- Windows: system output capture only
- Targets: Sonos and IKEA SYMFONISK speakers on the same LAN

## Quick Start

```bash
# 1. Clone and install
git clone https://github.com/your-repo/ikea-symfonisk-gateway.git
cd ikea-symfonisk-gateway
uv sync

# 2. Configure
cp .env.example .env
# Edit .env with your settings (e.g. FFMPEG_PATH)

# 3. Run
uv run python -m bridge_core
```

The gateway starts at `http://localhost:8732` (Web UI & API) and `http://localhost:8080` (Audio Stream).

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

---

## Installation & Deployment

### Recommended: Local Installation (uv)

The recommended way to run the gateway is natively on your host machine to ensure the best access to audio hardware and Bluetooth.

1.  **Clone the Repository**
    ```bash
    git clone https://github.com/your-repo/ikea-symfonisk-gateway.git
    cd ikea-symfonisk-gateway
    ```

2.  **Install Dependencies**
    ```bash
    uv sync
    ```

3.  **Configure Environment**
    ```bash
    cp .env.example .env
    ```
    Edit `.env` to customize your configuration (see [Configuration](#configuration)).

4.  **Run the Gateway**
    ```bash
    uv run python -m bridge_core
    ```

### Alternative: Docker

Docker is supported for Linux hosts. Running on Windows/macOS via Docker will not have access to host audio devices.

```bash
# Using Docker Compose (Recommended)
docker-compose up -d

# Using Docker directly
docker build -t symfonisk-gateway .
docker run -d \
  --name symfonisk-gateway \
  -p 8732:8732 \
  -p 8080:8080 \
  -v ./config:/app/config \
  symfonisk-gateway
```

#### Docker Persistence & Bluetooth
- To persist settings, mount a local directory to `/app/config`.
- Bluetooth requires host networking and BlueZ access (see `docker-compose.yml` for required capabilities and volume mounts).

---

## Configuration

Environment variables control the gateway behavior:

| Variable | Default | Description |
|----------|---------|-------------|
| `BRIDGE_HOST` | `0.0.0.0` | Host to bind the server |
| `BRIDGE_PORT` | `8732` | Port for the Web UI and REST API |
| `BRIDGE_STREAM_PORT` | `8080` | Port for audio streaming |
| `BRIDGE_CONFIG_DIR` | `config` | Directory for persistent storage |
| `FFMPEG_PATH` | `ffmpeg` | Explicit path to FFmpeg executable |
| `LOG_LEVEL` | `INFO` | Logging verbosity: `DEBUG`, `INFO`, `WARNING`, `ERROR` |

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

### Bluetooth Adapter Issues

```bash
# Check Bluetooth service
sudo systemctl status bluetooth

# Verify device is paired
bluetoothctl paired-devices

# Check audio profile
bluetoothctl info <device-mac>
```

### WiFi Stability

For deployments relying on WiFi, see the [WiFi Resilience & Host Connectivity Guidance](docs/WIFI_RESILIENCE.md) for optional host-level optimization and watchdog configuration.

---

## Development

### Running Tests

```bash
uv run pytest .
```

### Code Quality

All checks must pass before pushing:
```bash
uv run ruff format . && uv run ruff check . && uv run mypy . && uv run pytest .
```

### Pre-commit Hooks

```bash
./scripts/install-hooks.sh
```

---

## License

MIT
