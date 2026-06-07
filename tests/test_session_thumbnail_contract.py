from uuid import uuid4

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app

API_KEY = "change-me-in-production"
HEADERS = {"X-API-Key": API_KEY}


def test_session_thumbnail_get_put_contract_and_large_activity_limit():
    with TestClient(app) as client:
        session_response = client.post(
            "/api/v1/sessions",
            json={"distinct_id": f"thumb-test-{uuid4()}@example.com", "url": "https://trytenera.ai"},
            headers=HEADERS,
        )
        assert session_response.status_code == 201
        session_id = session_response.json()["id"]

        empty_thumbnail = client.get(f"/api/v1/sessions/{session_id}/thumbnail", headers=HEADERS)
        assert empty_thumbnail.status_code == 200
        assert empty_thumbnail.json() == {"thumbnail_url": None}

        previous_write_key = settings.write_key
        settings.write_key = "browser-write-key"
        put_thumbnail = client.put(
            f"/api/v1/sessions/{session_id}/thumbnail",
            json={"thumbnail_url": "data:image/jpeg;base64,abc123"},
            headers={"X-API-Key": "browser-write-key"},
        )
        settings.write_key = previous_write_key
        assert put_thumbnail.status_code == 204

        write_key_read = client.get(f"/api/v1/sessions/{session_id}/thumbnail", headers={"X-API-Key": "browser-write-key"})
        assert write_key_read.status_code == 401

        saved_thumbnail = client.get(f"/api/v1/sessions/{session_id}/thumbnail", headers=HEADERS)
        assert saved_thumbnail.status_code == 200
        assert saved_thumbnail.json()["thumbnail_url"] == "data:image/jpeg;base64,abc123"

        sessions = client.get("/api/v1/sessions", headers=HEADERS)
        assert sessions.status_code == 200
        assert any(
            row["id"] == session_id and row["thumbnail_url"] == "data:image/jpeg;base64,abc123"
            for row in sessions.json()["results"]
        )

        activity = client.get("/api/v1/logs/activity?limit=3000&hours=0", headers=HEADERS)
        assert activity.status_code == 200
        assert isinstance(activity.json(), list)
