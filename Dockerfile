FROM python:3.12-slim

# Install system dependencies for audio, networking, and Bluetooth
RUN apt-get update && apt-get install -y \
    libasound2-dev \
    pulseaudio-utils \
    alsa-utils \
    ffmpeg \
    curl \
    dbus \
    bluez \
    rfkill \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Set working directory
WORKDIR /app

# Copy the monorepo
COPY . .

# Install dependencies and the project
RUN uv sync --frozen

# Expose ports
EXPOSE 8732 8080

# Environment variables
ENV BRIDGE_HOST=0.0.0.0
ENV BRIDGE_PORT=8732
ENV BRIDGE_API_PORT=8732
ENV BRIDGE_STREAM_PORT=8080

# Run the bridge
CMD ["uv", "run", "python", "-m", "bridge_core.main"]
