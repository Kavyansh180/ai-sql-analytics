"""Tests for frontend HTTP API client, error handling, and deduplication logic."""
import json
import httpx
import pytest

from src.frontend.client import AnalyticsAPIError, AnalyticsClient


def test_client_successful_api_response():
    """Client successfully handles 200 OK with ready analytics response."""
    expected_data = {
        "status": "ready",
        "question": "Which product generated the highest revenue?",
        "sql": "SELECT name FROM analytics.products LIMIT 1;",
        "explanation": "Calculates highest revenue product.",
        "columns": ["name", "total_revenue"],
        "rows": [{"name": "Monitor", "total_revenue": 1197.0}],
        "row_count": 1,
        "execution_time_ms": 25.5,
        "clarification_question": None,
        "error": None,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert str(request.url) == "http://127.0.0.1:8000/api/v1/analytics/query"
        body = json.loads(request.content)
        assert body["question"] == "Which product generated the highest revenue?"
        return httpx.Response(200, json=expected_data)

    transport = httpx.MockTransport(handler)
    client = AnalyticsClient(base_url="http://127.0.0.1:8000", transport=transport)

    result = client.query("Which product generated the highest revenue?")
    assert result == expected_data
    assert result["status"] == "ready"
    assert result["row_count"] == 1
    assert len(result["rows"]) == 1


def test_client_empty_result_set():
    """Client properly handles queries that execute successfully but return 0 rows."""
    empty_data = {
        "status": "ready",
        "question": "Find products priced over 100000",
        "sql": "SELECT * FROM analytics.products WHERE price > 100000;",
        "explanation": "Returns products over $100,000.",
        "columns": ["id", "name"],
        "rows": [],
        "row_count": 0,
        "execution_time_ms": 10.2,
        "clarification_question": None,
        "error": None,
    }

    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=empty_data))
    client = AnalyticsClient(transport=transport)

    result = client.query("Find products priced over 100000")
    assert result["status"] == "ready"
    assert result["row_count"] == 0
    assert result["rows"] == []


def test_client_clarification_response():
    """Client handles clarification_needed response structure."""
    clarification_data = {
        "status": "clarification_needed",
        "question": "Show top",
        "sql": None,
        "explanation": "Query is ambiguous.",
        "columns": [],
        "rows": [],
        "row_count": 0,
        "execution_time_ms": None,
        "clarification_question": "Would you like to see top products or top customers?",
        "error": None,
    }

    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=clarification_data))
    client = AnalyticsClient(transport=transport)

    result = client.query("Show top")
    assert result["status"] == "clarification_needed"
    assert result["sql"] is None
    assert result["clarification_question"] == "Would you like to see top products or top customers?"


def test_client_unsupported_response():
    """Client handles unsupported response structure."""
    unsupported_data = {
        "status": "unsupported",
        "question": "Show me employee salaries",
        "sql": None,
        "explanation": "The database only contains retail analytics data.",
        "columns": [],
        "rows": [],
        "row_count": 0,
        "execution_time_ms": None,
        "clarification_question": None,
        "error": None,
    }

    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=unsupported_data))
    client = AnalyticsClient(transport=transport)

    result = client.query("Show me employee salaries")
    assert result["status"] == "unsupported"
    assert result["sql"] is None
    assert "retail analytics" in result["explanation"]


def test_client_connection_failure():
    """Client translates connection failure into user-friendly AnalyticsAPIError."""
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused by target machine")

    transport = httpx.MockTransport(handler)
    client = AnalyticsClient(base_url="http://127.0.0.1:8000", transport=transport)

    with pytest.raises(AnalyticsAPIError) as exc_info:
        client.query("Which product generated highest revenue?")

    assert "Could not connect to the analytics backend" in str(exc_info.value)
    assert "FastAPI server" in str(exc_info.value)


def test_client_timeout_error():
    """Client translates request timeout into informative AnalyticsAPIError."""
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("Read timed out")

    transport = httpx.MockTransport(handler)
    client = AnalyticsClient(timeout_seconds=30.0, transport=transport)

    with pytest.raises(AnalyticsAPIError) as exc_info:
        client.query("Long running query")

    assert "did not respond within 30 seconds" in str(exc_info.value)


@pytest.mark.parametrize(
    "status_code, expected_msg_fragment",
    [
        (422, "Invalid question format"),
        (502, "Failed to generate SQL from AI provider"),
        (503, "not configured properly"),
        (504, "timed out while generating SQL"),
        (500, "Backend request failed with status code 500"),
    ],
)
def test_client_http_status_errors(status_code: int, expected_msg_fragment: str):
    """Client maps HTTP error codes to clear, actionable error messages."""
    transport = httpx.MockTransport(lambda req: httpx.Response(status_code, text="Error"))
    client = AnalyticsClient(transport=transport)

    with pytest.raises(AnalyticsAPIError) as exc_info:
        client.query("Test question")

    assert exc_info.value.status_code == status_code
    assert expected_msg_fragment in str(exc_info.value)


def test_client_invalid_json():
    """Client detects non-JSON backend responses."""
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, content=b"<!DOCTYPE html><html>Bad Gateway</html>", headers={"Content-Type": "text/html"})
    )
    client = AnalyticsClient(transport=transport)

    with pytest.raises(AnalyticsAPIError, match="Invalid JSON response"):
        client.query("Test question")


def test_client_unexpected_response_structure():
    """Client validates response structure against required schema keys."""
    invalid_schema = {"unexpected_key": "some_value"}
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=invalid_schema))
    client = AnalyticsClient(transport=transport)

    with pytest.raises(AnalyticsAPIError, match="Unexpected response structure"):
        client.query("Test question")


def test_client_empty_question_rejected():
    """Empty or whitespace-only questions are rejected client-side without HTTP call."""
    client = AnalyticsClient()
    with pytest.raises(AnalyticsAPIError, match="Question cannot be empty"):
        client.query("   ")


def test_client_no_accidental_duplicate_request():
    """Client suppresses redundant network requests for identical consecutive queries."""
    call_count = 0
    resp_payload = {
        "status": "ready",
        "question": "Which product generated highest revenue?",
        "sql": "SELECT 1;",
        "explanation": "Calculates revenue.",
        "columns": ["col"],
        "rows": [{"col": 1}],
        "row_count": 1,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json=resp_payload)

    transport = httpx.MockTransport(handler)
    client = AnalyticsClient(transport=transport)

    # First call: hits transport
    res1 = client.query("Which product generated highest revenue?")
    assert call_count == 1
    assert res1["status"] == "ready"

    # Second call with identical question: served from cache, no network call
    res2 = client.query("Which product generated highest revenue?")
    assert call_count == 1
    assert res2 == res1

    # Forced call: hits transport
    res3 = client.query("Which product generated highest revenue?", force=True)
    assert call_count == 2
    assert res3 == res1

    # Different question: hits transport
    client.query("Show monthly revenue trends.")
    assert call_count == 3


def test_client_health_check():
    """Client check_health returns healthy when /health responds 200."""
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json={"status": "healthy", "service": "ai-sql-analytics"})
    )
    client = AnalyticsClient(transport=transport)
    health = client.check_health()
    assert health["status"] == "healthy"
    assert health["service"] == "ai-sql-analytics"


def test_client_health_check_failure():
    """Client check_health returns unhealthy when /health fails or is unreachable."""
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused")

    transport = httpx.MockTransport(handler)
    client = AnalyticsClient(transport=transport)
    health = client.check_health()
    assert health["status"] == "unhealthy"
    assert "unreachable" in health["error"]


def test_client_health_check_cold_start_timeout():
    """Client check_health returns warming when /health times out during cold start."""
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("Read timed out during container spin up")

    transport = httpx.MockTransport(handler)
    client = AnalyticsClient(transport=transport)
    health = client.check_health(timeout_seconds=5.0)
    assert health["status"] == "warming"
    assert "warming up" in health["error"]


def test_client_sends_copilot_secret_header():
    """Client automatically attaches X-Copilot-Secret header to outgoing requests."""
    captured_headers = {}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_headers
        captured_headers = dict(request.headers)
        return httpx.Response(
            200,
            json={
                "status": "ready",
                "question": "What is revenue?",
                "explanation": "Calculates revenue.",
                "columns": ["rev"],
                "rows": [{"rev": 100}],
            },
        )

    transport = httpx.MockTransport(handler)
    client = AnalyticsClient(
        base_url="http://127.0.0.1:8000",
        api_secret="my-super-secret-token-123",
        transport=transport,
    )

    client.query("What is revenue?")
    assert "x-copilot-secret" in captured_headers
    assert captured_headers["x-copilot-secret"] == "my-super-secret-token-123"


def test_client_handles_401_unauthorized():
    """Client raises sanitized AnalyticsAPIError on HTTP 401 Unauthorized."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "Unauthorized"})

    transport = httpx.MockTransport(handler)
    client = AnalyticsClient(transport=transport)

    with pytest.raises(AnalyticsAPIError) as exc_info:
        client.query("What is revenue?")

    assert exc_info.value.status_code == 401
    assert "Access unauthorized" in str(exc_info.value)
    assert "BACKEND_API_SECRET" in str(exc_info.value)

