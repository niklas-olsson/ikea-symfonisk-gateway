"""Ingress adapter SDK for the IKEA SYMFONISK bridge.

This package provides the protocol, types, and base interface
that all OS-specific ingress adapters must implement.
"""

from ingress_sdk.base import IngressAdapter
from ingress_sdk.protocol import (
    AcceptMessage,
    AudioFrame,
    Envelope,
    ErrorMessage,
    HealthMessage,
    Heartbeat,
    HelloMessage,
    MessageType,
)
from ingress_sdk.types import (
    AdapterCapabilities,
    SourceCapabilities,
    SourceDescriptor,
    SourceType,
)

__all__ = [
    "MessageType",
    "Envelope",
    "HelloMessage",
    "AcceptMessage",
    "AudioFrame",
    "Heartbeat",
    "HealthMessage",
    "ErrorMessage",
    "SourceType",
    "SourceDescriptor",
    "AdapterCapabilities",
    "SourceCapabilities",
    "IngressAdapter",
]
