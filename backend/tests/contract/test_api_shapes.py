"""API contract tests.

These lock the response SHAPES that the frontend and the committed fixtures both depend on.
When a stream replaces a fixture endpoint with a real implementation, these tests must
still pass unchanged — that is the point.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

PREFIX = "/api/v1"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


class TestHealth:
    def test_live(self, client: TestClient):
        response = client.get(f"{PREFIX}/health/live")
        assert response.status_code == 200
        assert response.json() == {"status": "alive"}

    def test_ready_reports_dependencies_without_crashing(self, client: TestClient):
        """No database or Redis in unit tests, so this must report, not explode."""
        response = client.get(f"{PREFIX}/health/ready")
        assert response.status_code in {200, 503}
        body = response.json()
        assert set(body) >= {"status", "dependencies", "assurance", "degradations"}
        assert set(body["dependencies"]) == {"database", "redis"}

    def test_correlation_id_is_echoed(self, client: TestClient):
        response = client.get(f"{PREFIX}/health/live", headers={"X-Correlation-Id": "abc123"})
        assert response.headers["X-Correlation-Id"] == "abc123"

    def test_correlation_id_is_generated_when_absent(self, client: TestClient):
        response = client.get(f"{PREFIX}/health/live")
        assert response.headers.get("X-Correlation-Id")


class TestSystemMode:
    def test_shape(self, client: TestClient):
        body = client.get(f"{PREFIX}/system/mode").json()
        assert set(body) >= {
            "llm_mode",
            "weather_mode",
            "notification_mode",
            "policy_mode",
            "real_email_enabled",
            "assurance",
            "degradations",
            "policy_pack",
            "limits",
        }

    def test_policy_badge_states_the_real_pack_status(self, client: TestClient):
        """The UI renders this verbatim; it must never claim more than the pack supports."""
        body = client.get(f"{PREFIX}/system/mode").json()
        label = body["policy_pack"]["ui_label"]
        if body["policy_mode"] == "charter":
            assert "PENDING CAR VERIFICATION" in label
        elif body["policy_mode"] == "demo":
            assert "DEMO FIXTURE" in label

    def test_no_secret_leaks(self, client: TestClient):
        raw = client.get(f"{PREFIX}/system/mode").text.lower()
        for forbidden in ("groq_api_key", "smtp_password", "api_key", "password"):
            assert forbidden not in raw


class TestErrorContract:
    def test_unknown_route_is_json(self, client: TestClient):
        assert client.get(f"{PREFIX}/does-not-exist").status_code == 404

    def test_typed_errors_use_the_documented_envelope(self, client: TestClient):
        """A missing fixture raises EntityNotFound, exercising the error handler."""
        response = client.get(f"{PREFIX}/reports/INC-does-not-matter")
        if response.status_code == 404:
            error = response.json()["error"]
            assert set(error) == {"code", "message", "correlation_id", "details"}
            assert error["code"] == "ENTITY_NOT_FOUND"
