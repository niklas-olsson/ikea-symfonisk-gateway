"""API integration tests."""

from unittest.mock import MagicMock, patch

import pytest
from bridge_core.main import app
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    # Mock StreamPublisher to avoid port 8080 conflict during tests
    with patch("bridge_core.main.StreamPublisher") as mock_pub_class:
        mock_pub = MagicMock()

        # publisher.start() is awaited in lifespan
        async def async_noop():
            pass

        mock_pub.start.return_value = async_noop()
        mock_pub.stop.return_value = async_noop()

        mock_pub_class.return_value = mock_pub

        with TestClient(app) as c:
            yield c


def test_get_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "version" in data
    assert "uptime" in data


def test_list_sources(client):
    response = client.get("/v1/sources")
    assert response.status_code == 200
    data = response.json()
    assert "sources" in data
    assert isinstance(data["sources"], list)
    # Synthetic adapter should provide at least one source
    assert len(data["sources"]) > 0


def test_get_source(client):
    sources_res = client.get("/v1/sources")
    source_id = sources_res.json()["sources"][0]["source_id"]

    response = client.get(f"/v1/sources/{source_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["source_id"] == source_id


def test_prepare_source(client):
    sources_res = client.get("/v1/sources")
    source_id = sources_res.json()["sources"][0]["source_id"]

    response = client.post(f"/v1/sources/{source_id}/prepare", json={})
    assert response.status_code == 200
    assert response.json()["success"] is True


def test_get_source_health_404(client):
    response = client.get("/v1/sources/non_existent/health")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "SOURCE_NOT_FOUND"


def test_list_targets(client):
    response = client.get("/v1/targets")
    assert response.status_code == 200
    data = response.json()
    assert "targets" in data
    assert isinstance(data["targets"], list)


def test_refresh_targets(client):
    response = client.post("/v1/targets/refresh")
    assert response.status_code == 200
    assert response.json()["success"] is True


def test_session_lifecycle(client):
    # 1. Get a source and target
    sources_res = client.get("/v1/sources")
    source_id = sources_res.json()["sources"][0]["source_id"]

    targets_res = client.get("/v1/targets")
    targets = targets_res.json()["targets"]

    if not targets:
        target_id = "test_target"
    else:
        target_id = targets[0]["target_id"]

    # 2. Create session
    create_res = client.post(
        "/v1/sessions",
        json={"source_id": source_id, "target_id": target_id},
    )
    assert create_res.status_code == 200
    session_id = create_res.json()["session_id"]
    assert create_res.json()["state"] == "created"

    # 3. Get session
    get_res = client.get(f"/v1/sessions/{session_id}")
    assert get_res.status_code == 200
    assert get_res.json()["session_id"] == session_id

    # 4. List sessions
    list_res = client.get("/v1/sessions")
    assert list_res.status_code == 200
    assert any(s["session_id"] == session_id for s in list_res.json()["sessions"])

    # 5. Stop session (even if not started)
    # Note: Transition from CREATED to STOPPING is not valid in session_manager.py
    # Valid from CREATED are: PREPARING, STARTING, FAILED
    # Let's try to start it first, or just acknowledge that stop fails from CREATED.
    # Actually, let's just test that it returns 400 with our structured error.
    stop_res = client.post(f"/v1/sessions/{session_id}/stop")
    assert stop_res.status_code == 400
    assert stop_res.json()["detail"]["code"] == "SESSION_STOP_FAILED"

    # Check state is still created (because transition failed)
    get_res = client.get(f"/v1/sessions/{session_id}")
    assert get_res.json()["state"] == "created"


def test_get_session_404(client):
    response = client.get("/v1/sessions/non_existent")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "SESSION_NOT_FOUND"
