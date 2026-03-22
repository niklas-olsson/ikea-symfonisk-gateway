#!/bin/bash

# Configuration
CHECK_IP="1.1.1.1" # Ping target (e.g., router/gateway or reliable DNS)
INTERFACE="wlan0"  # Network interface (e.g., wlan0, eth0)
MAX_RESTARTS=3     # Max restarts per hour
STATE_FILE="/tmp/wifi_watchdog_restarts"

# Ensure log prefix for journald
echo "[WiFi Watchdog] Connectivity check for $INTERFACE..."

# Rate limiting check
HOUR_AGO=$(date +%s -v-1H 2>/dev/null || date +%s -d '1 hour ago')
if [ -f "$STATE_FILE" ]; then
    # Keep only timestamps within the last hour
    RESTARTS_IN_WINDOW=$(awk -v limit="$HOUR_AGO" '$1 > limit' "$STATE_FILE")
    echo "$RESTARTS_IN_WINDOW" > "$STATE_FILE"

    RESTART_COUNT=$(grep -c . "$STATE_FILE")
    if [ "$RESTART_COUNT" -ge "$MAX_RESTARTS" ]; then
        echo "[WiFi Watchdog] ERROR: Rate limit exceeded ($RESTART_COUNT restarts in last hour). No action taken."
        exit 1
    fi
fi

# Connectivity check
if ping -c 1 -W 5 "$CHECK_IP" > /dev/null 2>&1; then
    echo "[WiFi Watchdog] Success: Link is healthy."
    exit 0
else
    echo "[WiFi Watchdog] WARNING: Link appears stale or down. Initiating recovery..."

    # Action: Restart interface (using nmcli)
    if command -v nmcli > /dev/null 2>&1; then
        nmcli device disconnect "$INTERFACE" && nmcli device connect "$INTERFACE"
        RECOVERY_STATUS=$?
    else
        # Fallback to ifdown/ifup if nmcli is missing
        ifconfig "$INTERFACE" down && ifconfig "$INTERFACE" up
        RECOVERY_STATUS=$?
    fi

    if [ $RECOVERY_STATUS -eq 0 ]; then
        echo "[WiFi Watchdog] Recovery successful. Interface $INTERFACE restarted."
        # Record restart timestamp
        date +%s >> "$STATE_FILE"
    else
        echo "[WiFi Watchdog] ERROR: Recovery failed for interface $INTERFACE."
    fi
fi
