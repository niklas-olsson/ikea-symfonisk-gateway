"""Ingress adapter client for connecting to the bridge core."""

import asyncio
import socket

from ingress_sdk.protocol import (
    AcceptMessage,
    Envelope,
    HelloMessage,
    MessageType,
)
from ingress_sdk.types import AdapterCapabilities


class IngressClient:
    """Client for connecting an adapter to the bridge core.

    Handles the HELLO/ACCEPT handshake and frame transmission.
    """

    def __init__(
        self,
        adapter_id: str,
        platform: str,
        version: str,
        capabilities: AdapterCapabilities,
        host: str = "localhost",
        port: int = 8731,
    ):
        self.adapter_id = adapter_id
        self.platform = platform
        self.version = version
        self.capabilities = capabilities
        self.host = host
        self.port = port
        self._socket: socket.socket | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._session_id: str | None = None

    async def connect(self) -> AcceptMessage:
        """Connect to the bridge core and perform HELLO/ACCEPT handshake."""
        self._reader, self._writer = await asyncio.open_connection(self.host, self.port)

        hello = HelloMessage(
            adapter_id=self.adapter_id,
            platform=self.platform,
            version=self.version,
            capabilities=self.capabilities.model_dump(),
        )

        self._writer.write(hello.to_msgpack())
        await self._writer.drain()

        data = await self._reader.read(8192)
        envelope = Envelope.from_msgpack(data)

        if envelope.type != MessageType.ACCEPT:
            raise RuntimeError(f"Expected ACCEPT, got {envelope.type}")

        if envelope.payload is None:
            raise RuntimeError("Accept message has no payload")

        accept = AcceptMessage.model_validate(envelope.payload)
        self._session_id = accept.session_id
        return accept

    async def send_frame(self, data: bytes, pts_ns: int, duration_ns: int) -> None:
        """Send an audio frame to the bridge core."""
        ...

    async def send_heartbeat(self) -> None:
        """Send a heartbeat message."""
        ...

    async def close(self) -> None:
        """Close the connection."""
        if self._writer:
            self._writer.close()
            await self._writer.wait_closed()
