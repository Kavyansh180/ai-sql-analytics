"""Unit tests for AWS SSM Parameter Store JSON configuration parsing and validation."""
import json
import pytest

from src.core.config import (
    Settings,
    load_settings_from_ssm_json,
    parse_ssm_json_config,
)


def test_parse_ssm_json_config_valid():
    """Valid JSON payload from SSM Parameter Store is parsed into dictionary."""
    payload = json.dumps({
        "ENVIRONMENT": "production",
        "POSTGRES_HOST": "ep-test-123.ap-south-1.aws.neon.tech",
        "POSTGRES_PORT": 5432,
        "POSTGRES_DB": "neondb",
        "APP_DB_USER": "app_user",
        "APP_DB_PASSWORD": "app_secret_password",
        "ANALYTICS_DB_USER": "analytics_user",
        "ANALYTICS_DB_PASSWORD": "analytics_secret_password",
        "PGSSLMODE": "require",
        "COPILOT_API_SECRET": "test-prod-secret-987",
        "GEMINI_API_KEY": "AIzaSyTestGeminiKey123456789012345678",
        "CORS_ORIGINS": "https://my-app.streamlit.app",
        "DB_SERVERLESS_MODE": True,
    })

    result = parse_ssm_json_config(payload)
    assert isinstance(result, dict)
    assert result["ENVIRONMENT"] == "production"
    assert result["POSTGRES_HOST"] == "ep-test-123.ap-south-1.aws.neon.tech"
    assert result["COPILOT_API_SECRET"] == "test-prod-secret-987"


def test_parse_ssm_json_config_empty_fails():
    """Empty or whitespace SSM payload raises ValueError."""
    with pytest.raises(ValueError, match="SSM configuration payload cannot be empty"):
        parse_ssm_json_config("")

    with pytest.raises(ValueError, match="SSM configuration payload cannot be empty"):
        parse_ssm_json_config("   ")


def test_parse_ssm_json_config_invalid_json():
    """Malformed JSON string in SSM payload raises ValueError."""
    with pytest.raises(ValueError, match="Invalid JSON in SSM configuration payload"):
        parse_ssm_json_config("{not_valid_json: 123")


def test_parse_ssm_json_config_non_dict():
    """JSON array or scalar in SSM payload raises ValueError."""
    with pytest.raises(ValueError, match="must be a JSON object mapping"):
        parse_ssm_json_config('["not", "a", "dict"]')

    with pytest.raises(ValueError, match="must be a JSON object mapping"):
        parse_ssm_json_config('"just a string"')


def test_load_settings_from_ssm_json():
    """load_settings_from_ssm_json returns a properly initialized Settings object."""
    payload = json.dumps({
        "ENVIRONMENT": "production",
        "POSTGRES_HOST": "ep-prod-db.ap-south-1.aws.neon.tech",
        "POSTGRES_DB": "neondb",
        "APP_DB_USER": "app_user",
        "APP_DB_PASSWORD": "app_secret_pass",
        "ANALYTICS_DB_USER": "analytics_user",
        "ANALYTICS_DB_PASSWORD": "analytics_secret_pass",
        "PGSSLMODE": "require",
        "COPILOT_API_SECRET": "copilot-super-secret-key",
        "GEMINI_API_KEY": "AIzaSyTestGeminiKey123456789012345678",
        "CORS_ORIGINS": "https://dashboard.example.com",
        "DB_SERVERLESS_MODE": True,
    })

    settings = load_settings_from_ssm_json(payload)
    assert isinstance(settings, Settings)
    assert settings.is_production() is True
    assert settings.COPILOT_API_SECRET == "copilot-super-secret-key"
    assert settings.get_sslmode() == "require"
    assert settings.get_cors_origins() == ["https://dashboard.example.com"]
    assert settings.DB_SERVERLESS_MODE is True

    # Validate auth configuration succeeds
    settings.validate_auth_configuration()


def test_settings_validate_auth_configuration_fails_in_production():
    """validate_auth_configuration raises ValueError when COPILOT_API_SECRET is missing in production."""
    settings = Settings(
        ENVIRONMENT="production",
        COPILOT_API_SECRET=None,
        CORS_ORIGINS="https://dashboard.example.com",
    )
    with pytest.raises(ValueError, match="COPILOT_API_SECRET must be configured in production"):
        settings.validate_auth_configuration()

    settings_empty = Settings(
        ENVIRONMENT="production",
        COPILOT_API_SECRET="   ",
        CORS_ORIGINS="https://dashboard.example.com",
    )
    with pytest.raises(ValueError, match="COPILOT_API_SECRET must be configured in production"):
        settings_empty.validate_auth_configuration()


def test_settings_validate_auth_configuration_passes_in_development():
    """validate_auth_configuration does not raise in development even if COPILOT_API_SECRET is unset."""
    settings = Settings(
        ENVIRONMENT="development",
        COPILOT_API_SECRET=None,
    )
    # Does not raise
    settings.validate_auth_configuration()


def test_fetch_ssm_parameter_json_success():
    """fetch_ssm_parameter_json calls boto3 SSM client and retrieves decrypted parameter."""
    from unittest.mock import MagicMock, patch
    from src.core.config import fetch_ssm_parameter_json

    expected_payload = json.dumps({"ENVIRONMENT": "production", "POSTGRES_HOST": "ep-db.neon.tech"})

    mock_client = MagicMock()
    mock_client.get_parameter.return_value = {
        "Parameter": {
            "Name": "/sql-analytics/production/config",
            "Type": "SecureString",
            "Value": expected_payload,
            "Version": 3,
        }
    }
    mock_boto3 = MagicMock()
    mock_boto3.client.return_value = mock_client

    with patch.dict("sys.modules", {"boto3": mock_boto3}):
        val = fetch_ssm_parameter_json("/sql-analytics/production/config", region_name="ap-south-1")
        assert val == expected_payload
        mock_boto3.client.assert_called_once_with("ssm", region_name="ap-south-1")
        mock_client.get_parameter.assert_called_once_with(
            Name="/sql-analytics/production/config", WithDecryption=True
        )


def test_fetch_ssm_parameter_json_empty_value_raises():
    """fetch_ssm_parameter_json raises ValueError if SSM parameter value is empty."""
    from unittest.mock import MagicMock, patch
    from src.core.config import fetch_ssm_parameter_json

    mock_client = MagicMock()
    mock_client.get_parameter.return_value = {
        "Parameter": {
            "Name": "/sql-analytics/production/config",
            "Value": "",
        }
    }
    mock_boto3 = MagicMock()
    mock_boto3.client.return_value = mock_client

    with patch.dict("sys.modules", {"boto3": mock_boto3}):
        with pytest.raises(ValueError, match="returned empty or whitespace value"):
            fetch_ssm_parameter_json("/sql-analytics/production/config")



def test_get_settings_loads_from_ssm_when_env_var_present():
    """get_settings fetches from SSM when SSM_CONFIG_PARAMETER_NAME is configured and caches result."""
    import os
    from unittest.mock import patch
    from src.core.config import clear_settings_cache, get_settings

    payload = json.dumps({
        "ENVIRONMENT": "production",
        "POSTGRES_HOST": "ep-ssm-prod.ap-south-1.aws.neon.tech",
        "POSTGRES_DB": "neondb",
        "APP_DB_USER": "app_user",
        "APP_DB_PASSWORD": "app_password",
        "ANALYTICS_DB_USER": "analytics_user",
        "ANALYTICS_DB_PASSWORD": "analytics_password",
        "COPILOT_API_SECRET": "ssm-super-secret-key-12345",
        "GEMINI_API_KEY": "AIzaSyTest1234567890123456789012345678",
        "CORS_ORIGINS": "https://copilot.streamlit.app",
    })

    clear_settings_cache()
    try:
        with patch.dict(os.environ, {"SSM_CONFIG_PARAMETER_NAME": "/sql-analytics/production/config"}), \
             patch("src.core.config.fetch_ssm_parameter_json", return_value=payload) as mock_fetch:

            s1 = get_settings()
            assert s1.is_production() is True
            assert s1.POSTGRES_HOST == "ep-ssm-prod.ap-south-1.aws.neon.tech"
            assert s1.COPILOT_API_SECRET == "ssm-super-secret-key-12345"
            assert mock_fetch.call_count == 1

            # Subsequent call: cached, zero additional SSM calls
            s2 = get_settings()
            assert s2 is s1
            assert mock_fetch.call_count == 1
    finally:
        clear_settings_cache()


def test_validate_production_invariants_rejects_default_passwords():
    """validate_production_invariants fails if default development passwords are used in production."""
    settings = Settings(
        ENVIRONMENT="production",
        POSTGRES_HOST="ep-prod.ap-south-1.aws.neon.tech",
        APP_DB_PASSWORD="app_user_dev_password",
        ANALYTICS_DB_PASSWORD="cloud_secure_password",
        COPILOT_API_SECRET="sufficiently-long-secret-key-12345",
        GEMINI_API_KEY="AIzaSyTestKey12345",
        CORS_ORIGINS="https://my-app.streamlit.app",
    )
    with pytest.raises(ValueError, match="Production requires a non-default APP_DB_PASSWORD"):
        settings.validate_production_invariants()


def test_validate_production_invariants_rejects_localhost():
    """validate_production_invariants fails if localhost is used as DB host in production."""
    settings = Settings(
        ENVIRONMENT="production",
        POSTGRES_HOST="localhost",
        APP_DB_PASSWORD="cloud_pass_1",
        ANALYTICS_DB_PASSWORD="cloud_pass_2",
        COPILOT_API_SECRET="sufficiently-long-secret-key-12345",
        GEMINI_API_KEY="AIzaSyTestKey12345",
        CORS_ORIGINS="https://my-app.streamlit.app",
    )
    with pytest.raises(ValueError, match="Production POSTGRES_HOST cannot be 'localhost'"):
        settings.validate_production_invariants()


def test_validate_production_invariants_rejects_short_secret():
    """validate_production_invariants fails if COPILOT_API_SECRET is under 16 characters in production."""
    settings = Settings(
        ENVIRONMENT="production",
        POSTGRES_HOST="ep-prod.ap-south-1.aws.neon.tech",
        APP_DB_PASSWORD="cloud_pass_1",
        ANALYTICS_DB_PASSWORD="cloud_pass_2",
        COPILOT_API_SECRET="short-key",
        GEMINI_API_KEY="AIzaSyTestKey12345",
        CORS_ORIGINS="https://my-app.streamlit.app",
    )
    with pytest.raises(ValueError, match="at least 16 characters long"):
        settings.validate_production_invariants()


def test_get_settings_production_fails_closed_without_valid_config():
    """get_settings in production fails closed without fallback to .env when settings are missing/dev."""
    import os
    from unittest.mock import patch
    from src.core.config import clear_settings_cache, get_settings

    clear_settings_cache()
    try:
        # In production without SSM_CONFIG_PARAMETER_NAME and without cloud env vars
        with patch.dict(os.environ, {"ENVIRONMENT": "production"}, clear=True):
            with pytest.raises(ValueError):
                get_settings()
    finally:
        clear_settings_cache()


def test_load_settings_from_ssm_json_fails_closed_on_localhost():
    """load_settings_from_ssm_json fails closed if host is localhost."""
    bad_payload = json.dumps({
        "ENVIRONMENT": "production",
        "POSTGRES_HOST": "localhost",
        "APP_DB_PASSWORD": "app_secret_pass",
        "ANALYTICS_DB_PASSWORD": "analytics_secret_pass",
        "COPILOT_API_SECRET": "copilot-super-secret-key-12345",
        "GEMINI_API_KEY": "AIzaSyTestGeminiKey123456789012345678",
        "CORS_ORIGINS": "https://dashboard.example.com",
    })
    with pytest.raises(ValueError, match="cannot be 'localhost'"):
        load_settings_from_ssm_json(bad_payload)


def test_load_settings_from_ssm_json_fails_closed_on_missing_secret():
    """load_settings_from_ssm_json fails closed if COPILOT_API_SECRET is missing or empty."""
    bad_payload = json.dumps({
        "ENVIRONMENT": "production",
        "POSTGRES_HOST": "ep-prod-db.ap-south-1.aws.neon.tech",
        "APP_DB_PASSWORD": "app_secret_pass",
        "ANALYTICS_DB_PASSWORD": "analytics_secret_pass",
        "COPILOT_API_SECRET": "",
        "GEMINI_API_KEY": "AIzaSyTestGeminiKey123456789012345678",
        "CORS_ORIGINS": "https://dashboard.example.com",
    })
    with pytest.raises(ValueError, match="COPILOT_API_SECRET must be configured and non-empty"):
        load_settings_from_ssm_json(bad_payload)


def test_load_settings_from_ssm_json_fails_closed_on_missing_gemini_key():
    """load_settings_from_ssm_json fails closed if GEMINI_API_KEY is missing."""
    bad_payload = json.dumps({
        "ENVIRONMENT": "production",
        "POSTGRES_HOST": "ep-prod-db.ap-south-1.aws.neon.tech",
        "APP_DB_PASSWORD": "app_secret_pass",
        "ANALYTICS_DB_PASSWORD": "analytics_secret_pass",
        "COPILOT_API_SECRET": "copilot-super-secret-key-12345",
        "GEMINI_API_KEY": "",
        "CORS_ORIGINS": "https://dashboard.example.com",
    })
    with pytest.raises(ValueError, match="GEMINI_API_KEY must be configured in production"):
        load_settings_from_ssm_json(bad_payload)


def test_load_settings_from_ssm_json_fails_closed_on_wildcard_cors():
    """load_settings_from_ssm_json fails closed if CORS_ORIGINS contains wildcard '*'."""
    bad_payload = json.dumps({
        "ENVIRONMENT": "production",
        "POSTGRES_HOST": "ep-prod-db.ap-south-1.aws.neon.tech",
        "APP_DB_PASSWORD": "app_secret_pass",
        "ANALYTICS_DB_PASSWORD": "analytics_secret_pass",
        "COPILOT_API_SECRET": "copilot-super-secret-key-12345",
        "GEMINI_API_KEY": "AIzaSyTestGeminiKey123456789012345678",
        "CORS_ORIGINS": "*",
    })
    with pytest.raises(ValueError, match="Wildcard or empty CORS_ORIGINS is prohibited"):
        load_settings_from_ssm_json(bad_payload)


