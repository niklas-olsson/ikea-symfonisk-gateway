"""Canonical Play API tests."""

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from bridge_core.main import app
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> Any:
    # Mock StreamPublisher to avoid port 8080 conflict during tests
    with patch("bridge_core.main.StreamPublisher") as mock_pub_class:
        mock_pub = MagicMock()

        # publisher.start() is awaited in lifespan
        async def async_noop() -> None:
            pass

        mock_pub.start.return_value = async_noop()
        mock_pub.stop.return_value = async_noop()

        mock_pub_class.return_value = mock_pub

        with TestClient(app) as c:
            yield c


def test_play_endpoint_takeover(client: TestClient) -> None:
    # 1. Get a source and target
    sources_res = client.get("/v1/sources")
    source_id_1 = sources_res.json()["sources"][0]["source_id"]
    # If there's only one source, use it for both for this test or assume we have another.
    # Synthetic adapter usually has one.
    source_id_2 = "synthetic-adapter:synthetic:alternate"

    targets_res = client.get("/v1/targets")
    targets = targets_res.json()["targets"]
    target_id = targets[0]["target_id"] if targets else "test_target"

    # 2. Start first session via /v1/play
    # We mock SessionManager.start_session because it involves real FFmpeg/Target calls
    with patch("bridge_core.core.SessionManager.start_session", return_value=True):
        play_res_1 = client.post(
            "/v1/play",
            json={"source_id": source_id_1, "target_id": target_id, "conflict_policy": "takeover"},
        )
        assert play_res_1.status_code == 200
        session_id_1 = play_res_1.json()["session_id"]

        # 3. Start second session via /v1/play with different source (takeover)
        play_res_2 = client.post(
            "/v1/play",
            json={"source_id": source_id_2, "target_id": target_id, "conflict_policy": "takeover"},
        )
        assert play_res_2.status_code == 200
        session_id_2 = play_res_2.json()["session_id"]
        assert session_id_1 != session_id_2

        # 4. Verify first session is superseded or stopped
        get_res_1 = client.get(f"/v1/sessions/{session_id_1}")
        assert get_res_1.json()["state"] in ("stopped", "superseded")


def test_play_endpoint_reuse(client: TestClient) -> None:
    # 1. Get a source and target
    sources_res = client.get("/v1/sources")
    source_id = sources_res.json()["sources"][0]["source_id"]

    targets_res = client.get("/v1/targets")
    targets = targets_res.json()["targets"]
    target_id = targets[0]["target_id"] if targets else "test_target"

    # 2. Start first session via /v1/play
    with patch("bridge_core.core.SessionManager.start_session", return_value=True):
        play_res_1 = client.post(
            "/v1/play",
            json={"source_id": source_id, "target_id": target_id, "conflict_policy": "reuse"},
        )
        assert play_res_1.status_code == 200
        session_id_1 = play_res_1.json()["session_id"]

        # 3. Request same source/target via /v1/play (reuse)
        play_res_2 = client.post(
            "/v1/play",
            json={"source_id": source_id, "target_id": target_id, "conflict_policy": "reuse"},
        )
        assert play_res_2.status_code == 200
        session_id_2 = play_res_2.json()["session_id"]
        assert session_id_1 == session_id_2
