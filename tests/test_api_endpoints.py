"""Tests for FastAPI HTTP endpoints and error handling."""
import pytest
from fastapi.testclient import TestClient

from src.api.main import create_app
from src.api.routes import get_analytics_service
from src.llm.exceptions import (
    LLMConfigurationError,
    LLMProviderError,
    LLMTimeoutError,
)
from src.llm.provider import MockLLMProvider
from src.llm.service import NaturalLanguageSQLService


@pytest.fixture
def client() -> TestClient:
    """Fixture providing FastAPI TestClient with MockLLMProvider injected."""
    app = create_app()
    mock_service = NaturalLanguageSQLService(provider=MockLLMProvider())
    app.dependency_overrides[get_analytics_service] = lambda: mock_service
    return TestClient(app)


def test_health_check_endpoint(client: TestClient):
    """GET /health must return 200 OK and healthy status."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["service"] == "ai-sql-analytics"


def test_query_endpoint_success(client: TestClient):
    """POST /api/v1/analytics/query returns 200 and structured results on valid question."""
    payload = {"question": "Which product generated the highest revenue?"}
    response = client.post("/api/v1/analytics/query", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"
    assert data["question"] == payload["question"]
    assert "analytics.products" in data["sql"]
    assert data["row_count"] >= 1
    assert len(data["rows"]) >= 1
    assert "name" in data["columns"]
    assert data["execution_time_ms"] is not None


def test_query_endpoint_validation_error_on_short_question(client: TestClient):
    """POST /api/v1/analytics/query returns 422 if question is under 3 characters."""
    response = client.post("/api/v1/analytics/query", json={"question": "hi"})
    assert response.status_code == 422


def test_query_endpoint_missing_configuration_error():
    """POST /api/v1/analytics/query returns 503 if LLM provider has configuration error."""
    app = create_app()

    class FailingConfigService(NaturalLanguageSQLService):
        def execute_question(self, question: str):
            raise LLMConfigurationError("API key missing")

    app.dependency_overrides[get_analytics_service] = lambda: FailingConfigService()
    test_client = TestClient(app)

    response = test_client.post(
        "/api/v1/analytics/query", json={"question": "What is our revenue?"}
    )
    assert response.status_code == 503
    assert "LLM service configuration is missing" in response.json()["detail"]


def test_query_endpoint_timeout_error():
    """POST /api/v1/analytics/query returns 504 on LLM provider timeout."""
    app = create_app()

    class TimeoutService(NaturalLanguageSQLService):
        def execute_question(self, question: str):
            raise LLMTimeoutError("Request timed out")

    app.dependency_overrides[get_analytics_service] = lambda: TimeoutService()
    test_client = TestClient(app)

    response = test_client.post(
        "/api/v1/analytics/query", json={"question": "What is our revenue?"}
    )
    assert response.status_code == 504
    assert "timed out" in response.json()["detail"]


def test_query_endpoint_provider_error():
    """POST /api/v1/analytics/query returns 502 on external LLM provider failure."""
    app = create_app()

    class FailingProviderService(NaturalLanguageSQLService):
        def execute_question(self, question: str):
            raise LLMProviderError("Upstream API error")

    app.dependency_overrides[get_analytics_service] = lambda: FailingProviderService()
    test_client = TestClient(app)

    response = test_client.post(
        "/api/v1/analytics/query", json={"question": "What is our revenue?"}
    )
    assert response.status_code == 502
    assert "Failed to generate SQL from AI provider" in response.json()["detail"]


def test_query_endpoint_blocks_prohibited_email_access():
    """POST /api/v1/analytics/query blocks queries accessing prohibited email even if LLM returns ready."""
    from src.llm.models import GeneratedSQLResponse, QueryStatus
    app = create_app()

    malicious_provider = MockLLMProvider(
        custom_responses={
            "extract emails": GeneratedSQLResponse(
                status=QueryStatus.READY,
                sql="SELECT c.first_name, c.email FROM analytics.customers c;",
                explanation="Extract all emails.",
            )
        }
    )
    service = NaturalLanguageSQLService(provider=malicious_provider)
    app.dependency_overrides[get_analytics_service] = lambda: service
    test_client = TestClient(app)

    response = test_client.post(
        "/api/v1/analytics/query", json={"question": "extract emails"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "unsupported"
    assert data["rows"] == []
    assert data["row_count"] == 0
    assert "Column-level security violation" in data["error"]


def test_query_endpoint_rejects_email_harvesting_question(client: TestClient):
    """POST /api/v1/analytics/query rejects email harvesting requests cleanly without data."""
    response = client.post(
        "/api/v1/analytics/query", json={"question": "Show all customer email addresses."}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "unsupported"
    assert data["rows"] == []
    assert data["row_count"] == 0
