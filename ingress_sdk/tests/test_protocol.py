import msgpack  # type: ignore[import-untyped]
import pytest
from ingress_sdk.protocol import (
    AcceptMessage,
    AudioFrame,
    Envelope,
    ErrorMessage,
    HealthMessage,
    Heartbeat,
    HelloMessage,
    MessageType,
    StopMessage,
)
from pydantic import ValidationError


def test_hello_message_roundtrip() -> None:
    msg = HelloMessage(
        adapter_id="test-adapter",
        platform="linux",
        version="1.0.0",
        capabilities={"supports_audio": True},
    )
    assert msg.type == MessageType.HELLO

    data = msg.to_msgpack()
    decoded = Envelope.from_msgpack(data)

    assert isinstance(decoded, HelloMessage)
    assert decoded.adapter_id == "test-adapter"
    assert decoded.platform == "linux"
    assert decoded.version == "1.0.0"
    assert decoded.capabilities == {"supports_audio": True}
    assert decoded.type == MessageType.HELLO


def test_accept_message_roundtrip() -> None:
    msg = AcceptMessage(
        session_id="session-123",
        source_id="source-456",
        required_format={"sample_rate": 48000},
        buffer_target_ms=500,
    )
    assert msg.type == MessageType.ACCEPT

    data = msg.to_msgpack()
    decoded = Envelope.from_msgpack(data)

    assert isinstance(decoded, AcceptMessage)
    assert decoded.session_id == "session-123"
    assert decoded.source_id == "source-456"
    assert decoded.required_format == {"sample_rate": 48000}
    assert decoded.buffer_target_ms == 500


def test_audio_frame_roundtrip() -> None:
    audio_data = b"\x00\x01\x02\x03"
    msg = AudioFrame(
        sequence=1,
        pts_ns=1000,
        duration_ns=100,
        format={"channels": 2},
        audio_data=audio_data,
    )
    assert msg.type == MessageType.AUDIO_FRAME

    data = msg.to_msgpack()
    decoded = Envelope.from_msgpack(data)

    assert isinstance(decoded, AudioFrame)
    assert decoded.sequence == 1
    assert decoded.pts_ns == 1000
    assert decoded.duration_ns == 100
    assert decoded.format == {"channels": 2}
    assert decoded.audio_data == audio_data


def test_heartbeat_roundtrip() -> None:
    msg = Heartbeat(session_id="session-123")
    assert msg.type == MessageType.HEARTBEAT

    data = msg.to_msgpack()
    decoded = Envelope.from_msgpack(data)

    assert isinstance(decoded, Heartbeat)
    assert decoded.session_id == "session-123"


def test_health_message_roundtrip() -> None:
    msg = HealthMessage(
        source_state="running",
        signal_present=True,
        dropped_frames=5,
        last_error=None,
    )
    assert msg.type == MessageType.HEALTH

    data = msg.to_msgpack()
    decoded = Envelope.from_msgpack(data)

    assert isinstance(decoded, HealthMessage)
    assert decoded.source_state == "running"
    assert decoded.signal_present is True
    assert decoded.dropped_frames == 5
    assert decoded.last_error is None


def test_error_message_roundtrip() -> None:
    msg = ErrorMessage(code="ERR_001", message="Something went wrong")
    assert msg.type == MessageType.ERROR

    data = msg.to_msgpack()
    decoded = Envelope.from_msgpack(data)

    assert isinstance(decoded, ErrorMessage)
    assert decoded.code == "ERR_001"
    assert decoded.message == "Something went wrong"


def test_stop_message_roundtrip() -> None:
    msg = StopMessage(session_id="session-123")
    assert msg.type == MessageType.STOP

    data = msg.to_msgpack()
    decoded = Envelope.from_msgpack(data)

    assert isinstance(decoded, StopMessage)
    assert decoded.session_id == "session-123"


def test_validation_required_fields() -> None:
    # HelloMessage requires adapter_id, platform, version, capabilities
    with pytest.raises(ValidationError):
        HelloMessage()

    # AudioFrame requires sequence, pts_ns, duration_ns, format
    with pytest.raises(ValidationError):
        AudioFrame()


def test_envelope_polymorphism() -> None:
    # Test that we can unpack into a base Envelope and get the correct subclass
    msg = HelloMessage(
        adapter_id="test-adapter",
        platform="linux",
        version="1.0.0",
        capabilities={},
    )
    data = msg.to_msgpack()

    envelope = Envelope.from_msgpack(data)
    assert isinstance(envelope, HelloMessage)
    assert envelope.type == MessageType.HELLO
    assert envelope.adapter_id == "test-adapter"


def test_malformed_msgpack() -> None:
    with pytest.raises(Exception):  # msgpack.exceptions.ExtraData or similar
        Envelope.from_msgpack(b"\xc1")  # \xc1 is never used in msgpack


def test_invalid_message_type() -> None:
    data = msgpack.packb({"type": "INVALID_TYPE", "session_id": "123"})
    # Envelope.from_msgpack will try to instantiate Envelope with INVALID_TYPE,
    # which will fail validation of the 'type' field.
    with pytest.raises(ValidationError):
        Envelope.from_msgpack(data)
