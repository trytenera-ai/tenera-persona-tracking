from fastapi.testclient import TestClient

from app.main import app


def test_dashboard_surfaces_require_access_code():
    with TestClient(app) as client:
        for path in ("/", "/docs", "/redoc", "/openapi.json", "/test"):
            response = client.get(path, follow_redirects=False)
            assert response.status_code == 401
            assert "Enter the access code" in response.text


def test_valid_access_code_sets_cookie_and_allows_dashboard():
    with TestClient(app) as client:
        response = client.get("/?code=teneraincadmin", follow_redirects=False)

        assert response.status_code == 303
        assert response.cookies.get("tpt_access") == "1"

        client.cookies.set("tpt_access", "1")
        dashboard = client.get("/")
        assert dashboard.status_code == 200
        assert "Tenera Analytics" in dashboard.text

        openapi = client.get("/openapi.json")
        assert openapi.status_code == 200
        assert openapi.json()["info"]["title"] == "Tenera Persona Tracking"


def test_health_remains_public():
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
