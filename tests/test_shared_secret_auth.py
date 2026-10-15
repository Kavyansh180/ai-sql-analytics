"""Tests for X-Copilot-Secret shared-secret authentication and route protection."""
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from src.api.main import create_app
from src.api.routes import get_analytics_service
from src.core.config import Settings
from src.core.logging import SensitiveDataFilter
from src.llm.provider import MockLLMProvider
from src.llm.service import NaturalLanguageSQLService


@pytest.fixture
def mock_service() -> NaturalLanguageSQLService:
    """Fixture providing mock analytics service."""
    return NaturalLanguageSQLService(provider=MockLLMProvider())


def test_production_fails_closed_when_server_secret_unset(mock_service):
    """In production, if COPILOT_API_SECRET is unset, API fails closed with 401."""
    prod_settings_no_secret = Settings(
        ENVIRONMENT="production",
        COPILOT_API_SECRET=None,
        CORS_ORIGINS="https://copilot.example.com",
    )

    with patch("src.api.auth.get_settings", return_value=prod_settings_no_secret):
        app = create_app()
        app.dependency_overrides[get_analytics_service] = lambda: mock_service
        client = TestClient(app)

        response = client.post(
            "/api/v1/analytics/query",
            json={"question": "What is our total revenue?"},
            headers={"X-Copilot-Secret": "any-key"},
        )
        assert response.status_code == 401
        assert "Server authentication is misconfigured or missing" in response.json()["detail"]


def test_production_rejects_missing_copilot_secret_header(mock_service):
    """In production with secret configured, requests missing X-Copilot-Secret return 401."""
    prod_settings = Settings(
        ENVIRONMENT="production",
        COPILOT_API_SECRET="prod-secret-token-xyz-123",
        CORS_ORIGINS="https://copilot.example.com",
    )

    with patch("src.api.auth.get_settings", return_value=prod_settings):
        app = create_app()
        app.dependency_overrides[get_analytics_service] = lambda: mock_service
        client = TestClient(app)

        response = client.post(
            "/api/v1/analytics/query",
            json={"question": "What is our total revenue?"},
            # No X-Copilot-Secret header provided
        )
        assert response.status_code == 401
        assert "Missing X-Copilot-Secret authentication header" in response.json()["detail"]


def test_production_rejects_invalid_copilot_secret_header(mock_service):
    """In production, requests with wrong X-Copilot-Secret return 401."""
    prod_settings = Settings(
        ENVIRONMENT="production",
        COPILOT_API_SECRET="prod-secret-token-xyz-123",
        CORS_ORIGINS="https://copilot.example.com",
    )

    with patch("src.api.auth.get_settings", return_value=prod_settings):
        app = create_app()
        app.dependency_overrides[get_analytics_service] = lambda: mock_service
        client = TestClient(app)

        response = client.post(
            "/api/v1/analytics/query",
            json={"question": "What is our total revenue?"},
            headers={"X-Copilot-Secret": "wrong-secret-token"},
        )
        assert response.status_code == 401
        assert "Invalid X-Copilot-Secret authentication header" in response.json()["detail"]


def test_production_accepts_valid_copilot_secret_header(mock_service):
    """In production, requests with correct X-Copilot-Secret return 200 OK."""
    prod_settings = Settings(
        ENVIRONMENT="production",
        COPILOT_API_SECRET="prod-secret-token-xyz-123",
        CORS_ORIGINS="https://copilot.example.com",
    )

    with patch("src.api.auth.get_settings", return_value=prod_settings):
        app = create_app()
        app.dependency_overrides[get_analytics_service] = lambda: mock_service
        client = TestClient(app)

        response = client.post(
            "/api/v1/analytics/query",
            json={"question": "Which product generated highest revenue?"},
            headers={"X-Copilot-Secret": "prod-secret-token-xyz-123"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ready"
        assert data["question"] == "Which product generated highest revenue?"


def test_unauthorized_request_does_not_invoke_service():
    """Unauthorized request is rejected by dependency before analytics service or LLM is called."""
    prod_settings = Settings(
        ENVIRONMENT="production",
        COPILOT_API_SECRET="prod-secret-token-xyz-123",
        CORS_ORIGINS="https://copilot.example.com",
    )

    spy_service = MagicMock(spec=NaturalLanguageSQLService)

    with patch("src.api.auth.get_settings", return_value=prod_settings):
        app = create_app()
        app.dependency_overrides[get_analytics_service] = lambda: spy_service
        client = TestClient(app)

        response = client.post(
            "/api/v1/analytics/query",
            json={"question": "What is our total revenue?"},
            headers={"X-Copilot-Secret": "invalid-secret"},
        )
        assert response.status_code == 401
        # Crucial verification: service.execute_question was never called
        spy_service.execute_question.assert_not_called()


def test_health_check_endpoint_remains_public_in_production():
    """GET /health is public and succeeds without authentication headers in production."""
    prod_settings = Settings(
        ENVIRONMENT="production",
        COPILOT_API_SECRET="prod-secret-token-xyz-123",
        CORS_ORIGINS="https://copilot.example.com",
    )

    with patch("src.core.config.get_settings", return_value=prod_settings), \
         patch("src.api.auth.get_settings", return_value=prod_settings):
        app = create_app()
        client = TestClient(app)

        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"


def test_development_allows_access_when_secret_unset(mock_service):
    """In development, if COPILOT_API_SECRET is unset, local requests succeed seamlessly."""
    dev_settings = Settings(
        ENVIRONMENT="development",
        COPILOT_API_SECRET=None,
    )

    with patch("src.api.auth.get_settings", return_value=dev_settings):
        app = create_app()
        app.dependency_overrides[get_analytics_service] = lambda: mock_service
        client = TestClient(app)

        response = client.post(
            "/api/v1/analytics/query",
            json={"question": "Which product generated highest revenue?"},
            # No auth header
        )
        assert response.status_code == 200
        assert response.json()["status"] == "ready"


def test_development_enforces_auth_when_secret_configured(mock_service):
    """In development, if COPILOT_API_SECRET is set, requests must provide matching header."""
    dev_settings = Settings(
        ENVIRONMENT="development",
        COPILOT_API_SECRET="dev-local-secret",
    )

    with patch("src.api.auth.get_settings", return_value=dev_settings):
        app = create_app()
        app.dependency_overrides[get_analytics_service] = lambda: mock_service
        client = TestClient(app)

        # Missing header fails
        r1 = client.post(
            "/api/v1/analytics/query",
            json={"question": "Which product generated highest revenue?"},
        )
        assert r1.status_code == 401

        # Wrong header fails
        r2 = client.post(
            "/api/v1/analytics/query",
            json={"question": "Which product generated highest revenue?"},
            headers={"X-Copilot-Secret": "wrong-key"},
        )
        assert r2.status_code == 401

        # Correct header succeeds
        r3 = client.post(
            "/api/v1/analytics/query",
            json={"question": "Which product generated highest revenue?"},
            headers={"X-Copilot-Secret": "dev-local-secret"},
        )
        assert r3.status_code == 200


def test_secret_never_appears_in_error_responses(mock_service):
    """Secret value never leaks in 401 error response body."""
    secret_value = "super-secret-token-do-not-leak-998877"
    prod_settings = Settings(
        ENVIRONMENT="production",
        COPILOT_API_SECRET=secret_value,
        CORS_ORIGINS="https://copilot.example.com",
    )

    with patch("src.api.auth.get_settings", return_value=prod_settings):
        app = create_app()
        app.dependency_overrides[get_analytics_service] = lambda: mock_service
        client = TestClient(app)

        response = client.post(
            "/api/v1/analytics/query",
            json={"question": "What is our revenue?"},
            headers={"X-Copilot-Secret": "bad-token"},
        )
        assert response.status_code == 401
        assert secret_value not in response.text


def test_logging_filter_redacts_copilot_secret():
    """SensitiveDataFilter sanitizes X-Copilot-Secret in log text."""
    raw_log = 'Incoming request headers: {"X-Copilot-Secret": "sensitive-prod-key-12345", "Host": "api.com"}'
    redacted = SensitiveDataFilter.redact_text(raw_log)
    assert "sensitive-prod-key-12345" not in redacted
    assert "[REDACTED_SECRET]" in redacted


def test_production_disables_interactive_docs():
    """In production, /docs, /redoc, and /openapi.json are disabled (return 404)."""
    prod_settings = Settings(
        ENVIRONMENT="production",
        COPILOT_API_SECRET="prod-secret-token",
        CORS_ORIGINS="https://copilot.example.com",
    )

    with patch("src.core.config.get_settings", return_value=prod_settings), \
         patch("src.api.main.get_settings", return_value=prod_settings):
        app = create_app()
        client = TestClient(app)

        assert client.get("/docs").status_code == 404
        assert client.get("/redoc").status_code == 404
        assert client.get("/openapi.json").status_code == 404


def test_production_protects_health_ready_endpoint():
    """In production, /health/ready requires X-Copilot-Secret authentication to prevent DB probing."""
    prod_settings = Settings(
        ENVIRONMENT="production",
        COPILOT_API_SECRET="prod-secret-token",
        CORS_ORIGINS="https://copilot.example.com",
    )

    with patch("src.core.config.get_settings", return_value=prod_settings), \
         patch("src.api.main.get_settings", return_value=prod_settings), \
         patch("src.api.auth.get_settings", return_value=prod_settings):
        app = create_app()
        client = TestClient(app)

        # Without header: 401 Unauthorized
        r1 = client.get("/health/ready")
        assert r1.status_code == 401

        # With wrong header: 401 Unauthorized
        r2 = client.get("/health/ready", headers={"X-Copilot-Secret": "wrong"})
        assert r2.status_code == 401

        # With correct header: succeeds past auth
        with patch("src.api.main.analytics_engine.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_connect.return_value.__enter__.return_value = mock_conn
            r3 = client.get("/health/ready", headers={"X-Copilot-Secret": "prod-secret-token"})
            assert r3.status_code == 200
            assert r3.json()["status"] == "ready"


def test_unauthorized_request_rejected_before_rate_limiter():
    """In production, unauthorized requests are rejected by verify_copilot_secret without calling rate limiter."""
    prod_settings = Settings(
        ENVIRONMENT="production",
        COPILOT_API_SECRET="prod-secret-token-xyz-123",
        CORS_ORIGINS="https://copilot.example.com",
    )

    with patch("src.api.auth.get_settings", return_value=prod_settings), \
         patch("src.api.routes.check_rate_limit") as mock_rate_limit:
        app = create_app()
        client = TestClient(app)

        response = client.post(
            "/api/v1/analytics/query",
            json={"question": "What is our revenue?"},
            headers={"X-Copilot-Secret": "invalid-token"},
        )
        assert response.status_code == 401
        mock_rate_limit.assert_not_called()

