# WiFi Resilience & Host Connectivity Guidance

For bridge deployments relying on WiFi, host-level network stability is critical. While the bridge application includes media-level watchdogs, it does not manage host network interfaces. This document provides optional guidance for configuring Linux hosts (specifically those using NetworkManager and systemd) for improved WiFi resilience.

## 1. NetworkManager Configuration

By default, NetworkManager may stop attempting to connect to a WiFi network after a certain number of failures. For headless bridge deployments, it is often preferred to have the host retry indefinitely.

### Recommended Connection Settings

For your primary WiFi connection, set `connection.autoconnect-retries` to `0` (infinite).

```bash
# Identify your connection name
nmcli connection show

# Set infinite retries (replace 'YourWiFi' with your connection name)
sudo nmcli connection modify "YourWiFi" connection.autoconnect-retries 0

# Apply changes
sudo nmcli connection up "YourWiFi"
```

## 2. Optional: Stale Link Watchdog

In some environments, a WiFi link may remain "connected" but stop passing traffic (stale link). A simple host-level watchdog can detect this and restart the network interface.

We provide a sample watchdog script and systemd units in `scripts/wifi-resilience/`.

### How it works:
1. **Connectivity Check**: Pings a reliable local target (e.g., your router/gateway).
2. **Rate Limiting**: Ensures that network restarts are not performed too frequently (e.g., max 3 times per hour) to avoid "flapping" loops during extended outages.
3. **Logging**: All actions are logged to the system journal for auditing.

### Installation

1. Copy the script and units to your system:
   ```bash
   sudo cp scripts/wifi-resilience/wifi-watchdog.sh /usr/local/bin/
   sudo chmod +x /usr/local/bin/wifi-watchdog.sh
   sudo cp scripts/wifi-resilience/wifi-watchdog.service /etc/systemd/system/
   sudo cp scripts/wifi-resilience/wifi-watchdog.timer /etc/systemd/system/
   ```

2. Enable the timer:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable --now wifi-watchdog.timer
   ```

## 3. Operator Tradeoffs

| Feature | Pros | Cons |
|---------|------|------|
| **Infinite Retries** | Ensures host eventually reconnects without manual intervention. | May cause high logs/power usage during long AP outages. |
| **Watchdog** | Automatically recovers from "stale" but active links. | May cause brief playback interruptions during the restart. |

## 4. Supported Environments

This guidance is specifically tailored for:
- **Operating System**: Linux (Debian, Ubuntu, Raspberry Pi OS, etc.)
- **Network Stack**: NetworkManager (`nmcli`)
- **Init System**: systemd
