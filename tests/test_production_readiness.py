"""Comprehensive test suite for Phase 4 Production Readiness and Deployment Hardening."""
import json
import logging
import re
from unittest.mock import MagicMock, patch
import httpx
import pytest
from fastapi.testclient import TestClient
from google.genai.errors import ClientError, ServerError
from sqlalchemy.exc import OperationalError

from src.api.main import create_app
from src.api.rate_limiter import InMemoryRateLimiter, check_rate_limit, limiter
from src.api.routes import get_analytics_service
from src.core.config import get_settings
from src.core.logging import SensitiveDataFilter, StructuredJsonFormatter, request_id_ctx
from src.db.session import execute_readonly_query
from src.llm.exceptions import (
    LLMConfigurationError,
    LLMProviderError,
    LLMResponseValidationError,
    LLMTimeoutError,
)
from src.llm.models import GeneratedSQLResponse, QueryStatus
from src.llm.provider import GeminiLLMProvider, MockLLMProvider
from src.llm.service import NaturalLanguageSQLService


# ------------------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def reset_limiter():
    """Reset rate limiter state before each test."""
    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture
def client() -> TestClient:
    """Test client with MockLLMProvider injected."""
    app = create_app()
    mock_service = NaturalLanguageSQLService(provider=MockLLMProvider())
    app.dependency_overrides[get_analytics_service] = lambda: mock_service
    return TestClient(app)


# ------------------------------------------------------------------------------
# Work Package A: API Hardening Tests
# ------------------------------------------------------------------------------
def test_empty_and_whitespace_prompts_rejected(client: TestClient):
    """Empty or whitespace-only questions must be rejected with 422 Unprocessable Entity."""
    # Empty string
    r1 = client.post("/api/v1/analytics/query", json={"question": ""})
    assert r1.status_code == 422

    # Whitespace only
    r2 = client.post("/api/v1/analytics/query", json={"question": "   \n\t  "})
    assert r2.status_code == 422

    # Under 3 characters after stripping
    r3 = client.post("/api/v1/analytics/query", json={"question": "  hi  "})
    assert r3.status_code == 422


def test_oversized_question_rejected(client: TestClient):
    """Questions exceeding 500 characters must be rejected with 422."""
    long_question = "What is the total revenue for product " + ("A" * 500)
    response = client.post("/api/v1/analytics/query", json={"question": long_question})
    assert response.status_code == 422


def test_oversized_request_body_rejected(client: TestClient):
    """Request bodies exceeding MAX_REQUEST_BODY_BYTES (64 KB) must be rejected with 413."""
    # Simulate oversized payload with Content-Length header > 64 KB
    headers = {"Content-Length": "70000", "Content-Type": "application/json"}
    response = client.post("/api/v1/analytics/query", content=b'{"question": "test"}', headers=headers)
    assert response.status_code == 413
    assert "exceeds maximum allowed size" in response.json()["detail"]


def test_rate_limiting_enforcement():
    """Exceeding configured rate limit returns HTTP 429 with Retry-After header."""
    app = create_app()
    mock_service = NaturalLanguageSQLService(provider=MockLLMProvider())
    app.dependency_overrides[get_analytics_service] = lambda: mock_service

    test_client = TestClient(app)

    # Patch settings to limit = 2 requests per minute
    with patch("src.api.rate_limiter.get_settings") as mock_settings:
        mock_settings.return_value.RATE_LIMIT_ENABLED = True
        mock_settings.return_value.RATE_LIMIT_REQUESTS_PER_MINUTE = 2
        mock_settings.return_value.TRUST_PROXY_HEADERS = False

        payload = {"question": "Which product generated the highest revenue?"}

        # Request 1: Allowed
        r1 = test_client.post("/api/v1/analytics/query", json=payload)
        assert r1.status_code == 200

        # Request 2: Allowed
        r2 = test_client.post("/api/v1/analytics/query", json=payload)
        assert r2.status_code == 200

        # Request 3: Blocked (429)
        r3 = test_client.post("/api/v1/analytics/query", json=payload)
        assert r3.status_code == 429
        assert "Rate limit exceeded" in r3.json()["detail"]
        assert "Retry-After" in r3.headers
        assert int(r3.headers["Retry-After"]) >= 1


def test_rate_limiting_isolated_per_client_ip():
    """Rate limit counters must be isolated per client IP."""
    custom_limiter = InMemoryRateLimiter(window_seconds=60.0)
    # Client 1 makes 2 requests
    assert custom_limiter.is_allowed("192.168.1.1", 2)[0] is True
    assert custom_limiter.is_allowed("192.168.1.1", 2)[0] is True
    assert custom_limiter.is_allowed("192.168.1.1", 2)[0] is False

    # Client 2 is unaffected
    assert custom_limiter.is_allowed("192.168.1.2", 2)[0] is True


def test_safe_error_responses_no_traceback_or_secrets(client: TestClient):
    """Internal server errors (500) must return safe, sanitized responses without leaking stack trace or secrets."""
    app = create_app()

    class CrashingService(NaturalLanguageSQLService):
        def execute_question(self, question: str):
            raise RuntimeError("Database connection string: postgresql://postgres:SuperSecret123@localhost:5432/db")

    app.dependency_overrides[get_analytics_service] = lambda: CrashingService()
    test_client = TestClient(app)

    response = test_client.post("/api/v1/analytics/query", json={"question": "What is our revenue?"})
    assert response.status_code == 500
    data = response.json()
    assert "SuperSecret123" not in json.dumps(data)
    assert "traceback" not in json.dumps(data).lower()
    assert "An unexpected internal server error occurred" in data["detail"]


def test_readiness_probe_healthy(client: TestClient):
    """GET /health/ready returns 200 OK when database is accessible."""
    response = client.get("/health/ready")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"
    assert data["database"] == "connected"


def test_readiness_probe_unhealthy_on_db_down():
    """GET /health/ready returns 503 Service Unavailable when database connection fails."""
    app = create_app()
    test_client = TestClient(app)

    with patch("src.db.session.analytics_engine.connect", side_effect=OperationalError("Connection refused", {}, None)):
        response = test_client.get("/health/ready")
        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "not_ready"
        assert data["database"] == "disconnected"


# ------------------------------------------------------------------------------
# Work Package B: Logging and Request Correlation Tests
# ------------------------------------------------------------------------------
def test_request_id_middleware_generates_and_propagates(client: TestClient):
    """Middleware generates UUIDv4 or propagates valid client X-Request-ID in response header."""
    # 1. No request ID provided -> generated
    r1 = client.get("/health")
    assert r1.status_code == 200
    req_id = r1.headers.get("X-Request-ID")
    assert req_id is not None
    assert len(req_id) >= 16

    # 2. Valid custom request ID provided -> preserved
    custom_id = "req-audit-test-999"
    r2 = client.get("/health", headers={"X-Request-ID": custom_id})
    assert r2.status_code == 200
    assert r2.headers.get("X-Request-ID") == custom_id

    # 3. Invalid/malicious request ID provided -> discarded and replaced with clean UUID
    bad_id = "<script>alert('xss')</script>"
    r3 = client.get("/health", headers={"X-Request-ID": bad_id})
    assert r3.status_code == 200
    new_id = r3.headers.get("X-Request-ID")
    assert new_id != bad_id
    assert "<script>" not in new_id


def test_sensitive_data_filter_redacts_credentials_and_pii():
    """SensitiveDataFilter must scrub API keys, DB passwords, and customer emails from logs."""
    redactor = SensitiveDataFilter()

    # Database connection string
    raw_db_log = "Connected to postgresql://analytics_user:SecretAnalyticsPassword99@db:5432/analytics"
    redacted_db = redactor.redact_text(raw_db_log)
    assert "SecretAnalyticsPassword99" not in redacted_db
    assert "postgresql://analytics_user:***@db:5432/analytics" in redacted_db

    # Google Gemini API key
    raw_key_log = "Requesting model with key AIzaSyD1234567890abcdefghijklmnopqrstuv"
    redacted_key = redactor.redact_text(raw_key_log)
    assert "AIzaSyD1234567890" not in redacted_key
    assert "[REDACTED_API_KEY]" in redacted_key

    # Customer email address
    raw_email_log = "Queried customer record for alice.walker@enterprise.com successfully"
    redacted_email = redactor.redact_text(raw_email_log)
    assert "alice.walker@enterprise.com" not in redacted_email
    assert "[REDACTED_EMAIL]" in redacted_email

    # Password parameter
    raw_param = "Authentication failed: password=SuperSecretPassword123"
    redacted_param = redactor.redact_text(raw_param)
    assert "SuperSecretPassword123" not in redacted_param
    assert "password=***" in redacted_param


def test_structured_json_formatter():
    """StructuredJsonFormatter produces valid JSON with required observability fields."""
    formatter = StructuredJsonFormatter()
    record = logging.LogRecord(
        name="test.logger",
        level=logging.INFO,
        pathname="test.py",
        lineno=10,
        msg="Analytics query processed successfully",
        args=(),
        exc_info=None,
    )
    record.request_id = "test-uuid-456"
    record.duration_ms = 45.2
    record.status_code = 200

    formatted = formatter.format(record)
    parsed = json.loads(formatted)
    assert parsed["level"] == "INFO"
    assert parsed["logger"] == "test.logger"
    assert parsed["message"] == "Analytics query processed successfully"
    assert parsed["request_id"] == "test-uuid-456"
    assert parsed["duration_ms"] == 45.2
    assert parsed["status_code"] == 200
    assert "timestamp" in parsed


# ------------------------------------------------------------------------------
# Work Package C: Reliability and Failure Handling Tests
# ------------------------------------------------------------------------------
def test_gemini_timeout_handling():
    """Gemini provider translates httpx.TimeoutException into LLMTimeoutError."""
    with patch("src.llm.provider.get_settings") as mock_settings:
        mock_settings.return_value.GEMINI_API_KEY = "test_key_dummy"
        mock_settings.return_value.GEMINI_MODEL = "gemini-3.1-flash-lite"
        mock_settings.return_value.LLM_REQUEST_TIMEOUT_SECONDS = 5
        mock_settings.return_value.LLM_MAX_RETRIES = 1

        provider = GeminiLLMProvider(api_key="test_key_dummy")

        with patch.object(
            provider.client.models,
            "generate_content",
            side_effect=httpx.TimeoutException("Read timed out"),
        ):
            with pytest.raises(LLMTimeoutError) as exc_info:
                provider.generate_response("What is revenue?")
            assert "timed out after 5 seconds" in str(exc_info.value)


def test_gemini_retries_transient_503_and_succeeds():
    """Gemini provider retries transient 503 ServerError and succeeds on subsequent attempt."""
    with patch("src.llm.provider.get_settings") as mock_settings:
        mock_settings.return_value.GEMINI_API_KEY = "test_key_dummy"
        mock_settings.return_value.GEMINI_MODEL = "gemini-3.1-flash-lite"
        mock_settings.return_value.LLM_REQUEST_TIMEOUT_SECONDS = 5
        mock_settings.return_value.LLM_MAX_RETRIES = 2
        mock_settings.return_value.LLM_RETRY_BASE_DELAY_SECONDS = 0.01
        mock_settings.return_value.LLM_RETRY_MAX_DELAY_SECONDS = 0.05

        provider = GeminiLLMProvider(api_key="test_key_dummy")

        mock_success_response = MagicMock()
        mock_success_response.parsed = GeneratedSQLResponse(
            status=QueryStatus.READY,
            sql="SELECT 1;",
            explanation="Calculates test query.",
        )

        mock_server_error = ServerError(503, "The service is temporarily unavailable")

        with patch.object(
            provider.client.models,
            "generate_content",
            side_effect=[mock_server_error, mock_success_response],
        ) as mock_gen:
            with patch("time.sleep"):  # Avoid sleeping in test
                res = provider.generate_response("What is revenue?")
                assert res.status == QueryStatus.READY
                assert mock_gen.call_count == 2


def test_gemini_retry_exhaustion_raises_provider_error():
    """Gemini provider raises LLMProviderError when retries are exhausted on persistent 503."""
    with patch("src.llm.provider.get_settings") as mock_settings:
        mock_settings.return_value.GEMINI_API_KEY = "test_key_dummy"
        mock_settings.return_value.GEMINI_MODEL = "gemini-3.1-flash-lite"
        mock_settings.return_value.LLM_REQUEST_TIMEOUT_SECONDS = 5
        mock_settings.return_value.LLM_MAX_RETRIES = 2
        mock_settings.return_value.LLM_RETRY_BASE_DELAY_SECONDS = 0.01
        mock_settings.return_value.LLM_RETRY_MAX_DELAY_SECONDS = 0.05

        provider = GeminiLLMProvider(api_key="test_key_dummy")

        mock_server_error = ServerError(503, "The service is unavailable")

        with patch.object(
            provider.client.models,
            "generate_content",
            side_effect=mock_server_error,
        ) as mock_gen:
            with patch("time.sleep"):
                with pytest.raises(LLMProviderError) as exc_info:
                    provider.generate_response("What is revenue?")
                assert "Gemini API error" in str(exc_info.value)
                assert mock_gen.call_count == 3  # Initial attempt + 2 retries


def test_gemini_non_retryable_error_aborts_immediately():
    """Non-retryable 403 PermissionDenied or 400 InvalidArgument aborts immediately with 0 retries."""
    with patch("src.llm.provider.get_settings") as mock_settings:
        mock_settings.return_value.GEMINI_API_KEY = "test_key_dummy"
        mock_settings.return_value.GEMINI_MODEL = "gemini-3.1-flash-lite"
        mock_settings.return_value.LLM_REQUEST_TIMEOUT_SECONDS = 5
        mock_settings.return_value.LLM_MAX_RETRIES = 2

        provider = GeminiLLMProvider(api_key="test_key_dummy")

        mock_client_error = ClientError(403, "API key not valid. Please pass a valid API key.")

        with patch.object(
            provider.client.models,
            "generate_content",
            side_effect=mock_client_error,
        ) as mock_gen:
            with pytest.raises(LLMProviderError):
                provider.generate_response("What is revenue?")
            assert mock_gen.call_count == 1  # Exactly 1 call, zero retries


def test_no_retries_for_sql_security_policy_violation():
    """SQL security policy violations are rejected cleanly by validator and never retried."""
    bad_provider = MockLLMProvider(
        custom_responses={
            "drop customers": GeneratedSQLResponse(
                status=QueryStatus.READY,
                sql="DROP TABLE analytics.customers;",
                explanation="Dropping customer table.",
            )
        }
    )

    with patch.object(bad_provider, "generate_response", wraps=bad_provider.generate_response) as spy_gen:
        with patch("src.llm.service.execute_readonly_query") as mock_exec:
            service = NaturalLanguageSQLService(provider=bad_provider)
            res = service.execute_question("drop customers")

            assert res.status == QueryStatus.UNSUPPORTED
            assert "Security guardrail rejected query" in res.error
            assert spy_gen.call_count == 1
            mock_exec.assert_not_called()


def test_database_statement_timeout_maps_to_clean_explanation():
    """Database statement timeout is sanitized and recorded as TIMED_OUT in audit log."""
    provider = MockLLMProvider(
        custom_responses={
            "slow query": GeneratedSQLResponse(
                status=QueryStatus.READY,
                sql="SELECT name FROM analytics.products LIMIT 10;",
                explanation="Long running calculation.",
            )
        }
    )
    service = NaturalLanguageSQLService(provider=provider)

    with patch(
        "src.llm.service.execute_readonly_query",
        side_effect=OperationalError("canceling statement due to statement timeout", {}, None),
    ):
        with patch.object(service, "_log_audit") as mock_audit:
            res = service.execute_question("slow query")
            assert res.status == QueryStatus.UNSUPPORTED
            assert "maximum execution time limit" in res.explanation
            assert res.error == "Query execution timed out."
            mock_audit.assert_called_once()
            assert mock_audit.call_args[1]["status"] == "TIMED_OUT"


def test_database_connection_cleanup_on_failure():
    """execute_readonly_query performs rollback and closes connection on exception."""
    mock_conn = MagicMock()
    mock_conn.execute.side_effect = RuntimeError("Simulated query execution crash")

    with patch("src.db.session.analytics_engine.connect", return_value=mock_conn):
        with pytest.raises(RuntimeError):
            execute_readonly_query("SELECT id FROM analytics.products;", enforce_validation=False)

        mock_conn.rollback.assert_called_once()
        mock_conn.close.assert_called_once()


# ------------------------------------------------------------------------------
# Work Package D: Extended Verification & Regression Tests
# ------------------------------------------------------------------------------
def test_missing_content_length_chunked_stream_limit_enforced(client: TestClient):
    """Chunked streamed requests without Content-Length header exceeding limit are rejected with 413."""
    def gen_oversized_stream():
        # Yield 70 chunks of 1000 bytes = 70,000 bytes (> 65,536 limit)
        for _ in range(70):
            yield b"x" * 1000

    response = client.post(
        "/api/v1/analytics/query",
        content=gen_oversized_stream(),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413
    assert "exceeds maximum allowed size" in response.json()["detail"]


def test_missing_content_length_chunked_stream_within_limit_succeeds(client: TestClient):
    """Chunked streamed requests without Content-Length within limit are accepted."""
    def gen_small_stream():
        yield b'{"question": "Which product '
        yield b'generated the highest revenue?"}'

    response = client.post(
        "/api/v1/analytics/query",
        content=gen_small_stream(),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_invalid_and_negative_content_length_rejected(client: TestClient):
    """Invalid or negative Content-Length headers must be rejected with 400 Bad Request."""
    # Negative content length
    r_neg = client.post(
        "/api/v1/analytics/query",
        content=b'{"question": "test"}',
        headers={"Content-Length": "-10", "Content-Type": "application/json"},
    )
    assert r_neg.status_code == 400
    assert "Invalid Content-Length" in r_neg.json()["detail"]

    # Non-integer content length
    r_invalid = client.post(
        "/api/v1/analytics/query",
        content=b'{"question": "test"}',
        headers={"Content-Length": "not-a-number", "Content-Type": "application/json"},
    )
    assert r_invalid.status_code == 400
    assert "Invalid Content-Length" in r_invalid.json()["detail"]


def test_rate_limiter_prunes_expired_client_entries():
    """InMemoryRateLimiter prunes expired client entries preventing memory leakage."""
    limiter_instance = InMemoryRateLimiter(window_seconds=1.0)

    # Client A and Client B make requests
    limiter_instance.is_allowed("10.0.0.1", max_requests=5)
    limiter_instance.is_allowed("10.0.0.2", max_requests=5)
    assert len(limiter_instance._requests) == 2

    # Advance time beyond window_seconds
    import time
    with patch("time.time", return_value=time.time() + 2.0):
        # Explicit cleanup removes both expired clients
        purged = limiter_instance.cleanup()
        assert purged == 2
        assert len(limiter_instance._requests) == 0


def test_rate_limiter_automatic_periodic_cleanup():
    """Automatic periodic cleanup in is_allowed prunes expired clients without manual call."""
    limiter_instance = InMemoryRateLimiter(window_seconds=1.0)
    current_time = 1000.0

    with patch("time.time", return_value=current_time):
        limiter_instance.is_allowed("old_client", max_requests=5)
        assert "old_client" in limiter_instance._requests

    # Advance time by 2 seconds and a new client makes a request
    with patch("time.time", return_value=current_time + 2.0):
        limiter_instance.is_allowed("new_client", max_requests=5)
        # old_client should have been pruned automatically
        assert "old_client" not in limiter_instance._requests
        assert "new_client" in limiter_instance._requests


def test_client_ip_proxy_trust_spoofing_protection():
    """When TRUST_PROXY_HEADERS is false, X-Forwarded-For is ignored to prevent IP spoofing."""
    from src.api.rate_limiter import get_client_ip
    from starlette.requests import Request

    scope = {
        "type": "http",
        "client": ("192.168.1.100", 54321),
        "headers": [(b"x-forwarded-for", b"203.0.113.195, 70.41.3.18")],
    }
    req = Request(scope)

    # Untrusted proxy (default): uses direct connection host
    ip_untrusted = get_client_ip(req, trust_proxy_headers=False)
    assert ip_untrusted == "192.168.1.100"

    # Trusted proxy: extracts original client IP from X-Forwarded-For
    ip_trusted = get_client_ip(req, trust_proxy_headers=True)
    assert ip_trusted == "203.0.113.195"


def test_gemini_provider_disables_sdk_internal_retries():
    """GeminiLLMProvider configures HttpOptions retry_options attempts=1 to prevent nested retries."""
    with patch("src.llm.provider.get_settings") as mock_settings:
        mock_settings.return_value.GEMINI_API_KEY = "dummy_key"
        mock_settings.return_value.GEMINI_MODEL = "gemini-3.1-flash-lite"
        mock_settings.return_value.LLM_REQUEST_TIMEOUT_SECONDS = 15

        provider = GeminiLLMProvider(api_key="dummy_key")
        http_options = provider.client._api_client._http_options
        assert http_options.retry_options is not None
        assert http_options.retry_options.attempts == 1


def test_exception_traceback_sensitive_data_redacted():
    """StructuredJsonFormatter and SensitiveDataFilter redact secrets from exception tracebacks."""
    formatter = StructuredJsonFormatter()

    try:
        raise ValueError("Failed connecting to postgresql://analytics_user:SuperSecretPassword456@db:5432/analytics")
    except ValueError:
        import sys
        exc_info = sys.exc_info()

    record = logging.LogRecord(
        name="test.logger",
        level=logging.ERROR,
        pathname="test.py",
        lineno=20,
        msg="Database query failure",
        args=(),
        exc_info=exc_info,
    )

    formatted = formatter.format(record)
    parsed = json.loads(formatted)
    assert "SuperSecretPassword456" not in formatted
    assert "SuperSecretPassword456" not in parsed.get("exception", "")
    assert "postgresql://analytics_user:***@db:5432/analytics" in parsed.get("exception", "")


def test_get_analytics_db_rolls_back_on_exception():
    """get_analytics_db context manager rolls back the session when an exception is raised."""
    from src.db.session import get_analytics_db

    mock_session = MagicMock()
    with patch("src.db.session.AnalyticsSessionLocal", return_value=mock_session):
        with pytest.raises(RuntimeError):
            with get_analytics_db():
                raise RuntimeError("Simulated failure in analytics db session")

        mock_session.rollback.assert_called_once()
        mock_session.close.assert_called_once()
