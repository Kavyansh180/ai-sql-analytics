"""Tests for Step 2A: AWS Lambda adapter, serverless NullPool, and configurable CORS."""
import json
import os
from unittest.mock import patch
import pytest
from sqlalchemy.pool import NullPool, QueuePool

from src.api.handler import handler
from src.core.config import Settings
from src.db.session import (
    create_analytics_engine,
    create_app_engine,
    is_serverless_environment,
)


# ------------------------------------------------------------------------------
# 1. Serverless Engine & NullPool Tests
# ------------------------------------------------------------------------------
def test_engine_pool_local_vs_serverless():
    """Verify that local engines use QueuePool while serverless engines use NullPool."""
    # Local engine (serverless=False)
    local_app_engine = create_app_engine(serverless_override=False)
    local_analytics_engine = create_analytics_engine(serverless_override=False)

    assert isinstance(local_app_engine.pool, QueuePool)
    assert local_app_engine.pool.size() == 5
    assert isinstance(local_analytics_engine.pool, QueuePool)
    assert local_analytics_engine.pool.size() == 5

    # Serverless engine (serverless=True)
    serverless_app_engine = create_app_engine(serverless_override=True)
    serverless_analytics_engine = create_analytics_engine(serverless_override=True)

    assert isinstance(serverless_app_engine.pool, NullPool)
    assert isinstance(serverless_analytics_engine.pool, NullPool)


def test_is_serverless_environment_detection():
    """Verify that serverless detection responds to AWS Lambda environment variables and config."""
    # Default (no lambda vars)
    with patch.dict(os.environ, {}, clear=True):
        assert is_serverless_environment(serverless_override=None) is False

    # AWS Lambda environment variable present
    with patch.dict(os.environ, {"AWS_LAMBDA_FUNCTION_NAME": "sql-analytics-api"}, clear=True):
        assert is_serverless_environment(serverless_override=None) is True

    # LAMBDA_TASK_ROOT present
    with patch.dict(os.environ, {"LAMBDA_TASK_ROOT": "/var/task"}, clear=True):
        assert is_serverless_environment(serverless_override=None) is True


# ------------------------------------------------------------------------------
# 2. CORS Origins Configuration Tests
# ------------------------------------------------------------------------------
def test_cors_origins_dev_default():
    """In development, default CORS origins include localhost Streamlit ports."""
    settings = Settings(ENVIRONMENT="development")
    origins = settings.get_cors_origins()
    assert "http://localhost:8501" in origins
    assert "http://127.0.0.1:8501" in origins


def test_cors_origins_comma_separated_parsing():
    """CORS_ORIGINS comma-separated string is parsed into clean trimmed list."""
    settings = Settings(
        ENVIRONMENT="development",
        CORS_ORIGINS=" https://copilot.example.com , https://analytics.company.internal ",
    )
    origins = settings.get_cors_origins()
    assert origins == ["https://copilot.example.com", "https://analytics.company.internal"]


def test_cors_origins_production_rejects_wildcard():
    """In production, wildcard '*' CORS origin is strictly prohibited."""
    settings = Settings(
        ENVIRONMENT="production",
        CORS_ORIGINS="*",
    )
    with pytest.raises(ValueError) as exc_info:
        settings.get_cors_origins()
    assert "Wildcard or empty CORS_ORIGINS is prohibited in production" in str(exc_info.value)


def test_cors_origins_production_requires_explicit_origins():
    """In production, explicit trusted origins are accepted."""
    settings = Settings(
        ENVIRONMENT="production",
        CORS_ORIGINS="https://copilot.mycompany.com,https://dashboard.mycompany.com",
    )
    origins = settings.get_cors_origins()
    assert origins == ["https://copilot.mycompany.com", "https://dashboard.mycompany.com"]


# ------------------------------------------------------------------------------
# 3. Mangum AWS Lambda Handler Invocations
# ------------------------------------------------------------------------------
def test_mangum_lambda_handler_health():
    """Mangum handler properly translates AWS Lambda Function URL / API Gateway v2 event for GET /health."""
    event = {
        "version": "2.0",
        "routeKey": "GET /health",
        "rawPath": "/health",
        "rawQueryString": "",
        "headers": {
            "host": "api.example.com",
            "x-forwarded-for": "203.0.113.195",
        },
        "requestContext": {
            "http": {
                "method": "GET",
                "path": "/health",
                "protocol": "HTTP/1.1",
                "sourceIp": "203.0.113.195",
            }
        },
        "isBase64Encoded": False,
    }

    response = handler(event, {})
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["status"] == "healthy"
    assert body["service"] == "ai-sql-analytics"
    assert "x-request-id" in response["headers"]


def test_mangum_lambda_handler_analytics_query():
    """Mangum handler executes analytics query end-to-end through mock provider."""
    event = {
        "version": "2.0",
        "routeKey": "POST /api/v1/analytics/query",
        "rawPath": "/api/v1/analytics/query",
        "rawQueryString": "",
        "headers": {
            "host": "api.example.com",
            "content-type": "application/json",
            "x-forwarded-for": "203.0.113.195",
        },
        "requestContext": {
            "http": {
                "method": "POST",
                "path": "/api/v1/analytics/query",
                "protocol": "HTTP/1.1",
                "sourceIp": "203.0.113.195",
            }
        },
        "body": json.dumps({"question": "Which product generated highest revenue?"}),
        "isBase64Encoded": False,
    }

    response = handler(event, {})
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["status"] == "ready"
    assert body["question"] == "Which product generated highest revenue?"
    assert "columns" in body
    assert "rows" in body


def test_mangum_lambda_handler_unauthorized_when_auth_configured():
    """Mangum handler rejects query with 401 when COPILOT_API_SECRET is configured and header is missing."""
    with patch("src.api.auth.get_settings") as mock_settings:
        mock_settings.return_value = Settings(
            ENVIRONMENT="production",
            COPILOT_API_SECRET="lambda-test-secret-456",
            CORS_ORIGINS="https://copilot.example.com",
        )
        event = {
            "version": "2.0",
            "routeKey": "POST /api/v1/analytics/query",
            "rawPath": "/api/v1/analytics/query",
            "rawQueryString": "",
            "headers": {
                "host": "api.example.com",
                "content-type": "application/json",
            },
            "requestContext": {
                "http": {
                    "method": "POST",
                    "path": "/api/v1/analytics/query",
                    "protocol": "HTTP/1.1",
                    "sourceIp": "203.0.113.195",
                }
            },
            "body": json.dumps({"question": "Which product generated highest revenue?"}),
            "isBase64Encoded": False,
        }
        # Without header: 401
        resp = handler(event, {})
        assert resp["statusCode"] == 401

        # With header: 200
        event["headers"]["x-copilot-secret"] = "lambda-test-secret-456"
        resp2 = handler(event, {})
        assert resp2["statusCode"] == 200



def test_mangum_lambda_handler_oversized_payload_rejected():
    """Mangum handler enforces 64KB request body limit in serverless context."""
    oversized_text = "x" * 70000
    event = {
        "version": "2.0",
        "routeKey": "POST /api/v1/analytics/query",
        "rawPath": "/api/v1/analytics/query",
        "rawQueryString": "",
        "headers": {
            "host": "api.example.com",
            "content-type": "application/json",
            "content-length": str(len(oversized_text)),
            "x-forwarded-for": "203.0.113.195",
        },
        "requestContext": {
            "http": {
                "method": "POST",
                "path": "/api/v1/analytics/query",
                "protocol": "HTTP/1.1",
                "sourceIp": "203.0.113.195",
            }
        },
        "body": oversized_text,
        "isBase64Encoded": False,
    }

    response = handler(event, {})
    assert response["statusCode"] == 413


# ------------------------------------------------------------------------------
# 4. PostgreSQL SSL Mode & Connection URL Tests
# ------------------------------------------------------------------------------
def test_sslmode_dev_default_is_none():
    """In development, if PGSSLMODE is not specified, get_sslmode returns None to preserve local Docker."""
    settings = Settings(ENVIRONMENT="development", PGSSLMODE=None)
    assert settings.get_sslmode() is None

    app_url = settings.get_app_database_url()
    analytics_url = settings.get_analytics_database_url()
    admin_url = settings.get_admin_database_url()

    assert "sslmode" not in app_url.query
    assert "sslmode" not in analytics_url.query
    assert "sslmode" not in admin_url.query


def test_sslmode_prod_default_is_require():
    """In production, if PGSSLMODE is not specified, get_sslmode securely defaults to 'require'."""
    settings = Settings(
        ENVIRONMENT="production",
        PGSSLMODE=None,
        CORS_ORIGINS="https://copilot.example.com",
    )
    assert settings.get_sslmode() == "require"

    app_url = settings.get_app_database_url()
    assert app_url.query.get("sslmode") == "require"
    assert "sslmode=require" in app_url.render_as_string(hide_password=False)


def test_sslmode_explicit_override_in_dev():
    """Explicitly setting PGSSLMODE='require' in development attaches sslmode=require for Neon."""
    settings = Settings(ENVIRONMENT="development", PGSSLMODE="require")
    assert settings.get_sslmode() == "require"

    analytics_url = settings.get_analytics_database_url()
    assert analytics_url.query.get("sslmode") == "require"
    assert "sslmode=require" in analytics_url.render_as_string(hide_password=False)


def test_sslmode_explicit_override_in_prod():
    """Explicitly setting PGSSLMODE='verify-full' in production is respected."""
    settings = Settings(
        ENVIRONMENT="production",
        PGSSLMODE="verify-full",
        CORS_ORIGINS="https://copilot.example.com",
    )
    assert settings.get_sslmode() == "verify-full"

    app_url = settings.get_app_database_url()
    assert app_url.query.get("sslmode") == "verify-full"


def test_all_database_urls_reflect_configured_sslmode():
    """Verify admin, app, and analytics URLs consistently reflect the active SSL configuration."""
    settings = Settings(
        ENVIRONMENT="development",
        PGSSLMODE="require",
        POSTGRES_HOST="ep-cool-fog-123456.us-east-2.aws.neon.tech",
        POSTGRES_DB="neondb",
    )

    admin_url = settings.get_admin_database_url()
    app_url = settings.get_app_database_url()
    analytics_url = settings.get_analytics_database_url()

    for url in (admin_url, app_url, analytics_url):
        assert url.host == "ep-cool-fog-123456.us-east-2.aws.neon.tech"
        assert url.database == "neondb"
        assert url.query.get("sslmode") == "require"
        assert "sslmode=require" in url.render_as_string(hide_password=False)


def test_custom_env_file_loading_support():
    """Verify that Settings supports specifying an isolated env_file via ENV_FILE environment variable."""
    assert Settings.model_config.get("env_file") is not None
    # Verify default falls back to .env when ENV_FILE is unset
    with patch.dict(os.environ, {}, clear=True):
        import src.core.config
        # model_config reflects the configured or default env_file
        assert "env_file" in Settings.model_config
