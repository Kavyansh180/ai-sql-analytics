"""Core configuration module for AI-Powered SQL Analytics Copilot."""
import json
import logging
import os
from functools import lru_cache
from typing import Any, Dict, Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    """Application and database configuration settings."""
    model_config = SettingsConfigDict(
        env_file=os.getenv("ENV_FILE", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Server / Environment
    ENVIRONMENT: str = Field(default="development", description="Runtime environment")

    # PostgreSQL Host & Port
    POSTGRES_HOST: str = Field(default="localhost")
    POSTGRES_PORT: int = Field(default=5432)
    POSTGRES_DB: str = Field(default="sql_analytics_db")

    # Database Roles & Passwords
    POSTGRES_ADMIN_USER: str = Field(default="postgres")
    POSTGRES_ADMIN_PASSWORD: str = Field(default="postgres_dev_password")

    APP_DB_USER: str = Field(default="app_user")
    APP_DB_PASSWORD: str = Field(default="app_user_dev_password")

    ANALYTICS_DB_USER: str = Field(default="analytics_user")
    ANALYTICS_DB_PASSWORD: str = Field(default="analytics_user_dev_password")

    # SSL Configuration (Phase 5 / Cloud Database Migration)
    PGSSLMODE: Optional[str] = Field(
        default=None,
        description="PostgreSQL SSL mode (e.g. 'disable', 'prefer', 'require', 'verify-full')",
    )

    # Guardrails
    ANALYTICS_STATEMENT_TIMEOUT_MS: int = Field(
        default=3000,
        description="Query statement timeout in milliseconds for analytics role",
    )
    MAX_QUERY_ROW_LIMIT: int = Field(
        default=1000,
        description="Maximum row limit forced by query guardrails",
    )

    # LLM Settings (Phase 2 & 4)
    GEMINI_API_KEY: Optional[str] = Field(default=None, description="Google Gemini API key")
    GEMINI_MODEL: str = Field(default="gemini-3.1-flash-lite", description="Gemini model identifier")
    LLM_REQUEST_TIMEOUT_SECONDS: int = Field(default=30, description="Timeout for LLM API calls in seconds")
    LLM_MAX_RETRIES: int = Field(default=2, description="Maximum retry attempts for retryable LLM errors")
    LLM_RETRY_BASE_DELAY_SECONDS: float = Field(default=0.5, description="Initial retry delay in seconds")
    LLM_RETRY_MAX_DELAY_SECONDS: float = Field(default=5.0, description="Maximum retry backoff delay in seconds")

    # Logging and Observability (Phase 4)
    LOG_LEVEL: str = Field(default="INFO", description="Standard logging level (DEBUG, INFO, WARNING, ERROR)")
    LOG_FORMAT: str = Field(default="json", description="Structured log format: 'json' or 'text'")

    # Security & Rate Limiting (Phase 4)
    RATE_LIMIT_ENABLED: bool = Field(default=True, description="Enable API rate limiting")
    RATE_LIMIT_REQUESTS_PER_MINUTE: int = Field(default=60, description="Allowed queries per minute per client IP")
    MAX_REQUEST_BODY_BYTES: int = Field(default=65536, description="Maximum request body size in bytes (64 KB)")
    TRUST_PROXY_HEADERS: bool = Field(default=False, description="Whether to trust X-Forwarded-For header for client IP")

    # Serverless & CORS Configuration (Phase 5 / Step 2A)
    DB_SERVERLESS_MODE: bool = Field(
        default=False,
        description="Enable NullPool connection management to prevent connection pool exhaustion in serverless runtimes",
    )
    CORS_ORIGINS: str = Field(
        default="http://localhost:8501,http://127.0.0.1:8501,http://localhost:3000,http://127.0.0.1:3000",
        description="Allowed CORS origins as comma-separated string",
    )

    # API Endpoint Protection & Shared Secret
    COPILOT_API_SECRET: Optional[str] = Field(
        default=None,
        description="Shared secret key required in X-Copilot-Secret header to protect public API endpoints",
    )

    def is_production(self) -> bool:
        """Check whether the active environment is production."""
        return self.ENVIRONMENT.lower() in ("production", "prod")

    def validate_auth_configuration(self) -> None:
        """
        Validate authentication configuration.
        
        In production, fail-closed: COPILOT_API_SECRET must be configured and non-empty.
        """
        if self.is_production():
            if not self.COPILOT_API_SECRET or not self.COPILOT_API_SECRET.strip():
                raise ValueError(
                    "COPILOT_API_SECRET must be configured in production to protect the public API endpoint."
                )

    def validate_production_invariants(self) -> None:
        """
        Enforce strict fail-closed validation in production:
        1. Reject default development credentials.
        2. Prohibit localhost/127.0.0.1 database host.
        3. Require non-empty COPILOT_API_SECRET with at least 16 characters.
        4. Require valid GEMINI_API_KEY.
        5. Prohibit wildcard or empty CORS origins.
        """
        if not self.is_production():
            return

        # 1. Prohibit default development database credentials
        if not self.APP_DB_PASSWORD or self.APP_DB_PASSWORD == "app_user_dev_password":
            raise ValueError("Production requires a non-default APP_DB_PASSWORD.")
        if not self.ANALYTICS_DB_PASSWORD or self.ANALYTICS_DB_PASSWORD == "analytics_user_dev_password":
            raise ValueError("Production requires a non-default ANALYTICS_DB_PASSWORD.")

        # 2. Prohibit localhost database host in production
        if self.POSTGRES_HOST.lower() in ("localhost", "127.0.0.1"):
            raise ValueError(
                f"Production POSTGRES_HOST cannot be '{self.POSTGRES_HOST}'. A cloud database host is required."
            )

        # 3. Require configured shared secret with adequate length
        if not self.COPILOT_API_SECRET or not self.COPILOT_API_SECRET.strip():
            raise ValueError("COPILOT_API_SECRET must be configured and non-empty in production.")
        if len(self.COPILOT_API_SECRET.strip()) < 16:
            raise ValueError("COPILOT_API_SECRET in production must be at least 16 characters long.")

        # 4. Require configured Gemini API key
        if not self.GEMINI_API_KEY or self.GEMINI_API_KEY in ("your_gemini_api_key_here", ""):
            raise ValueError("GEMINI_API_KEY must be configured in production.")

        # 5. Validate explicit CORS origins
        self.get_cors_origins()


    def get_cors_origins(self) -> list[str]:
        """
        Parse and validate CORS origins.
        
        In development, defaults to local frontend origins.
        In production, requires explicit origins and rejects wildcard '*' or empty values.
        """
        raw = self.CORS_ORIGINS
        if isinstance(raw, str):
            origins = [o.strip() for o in raw.split(",") if o.strip()]
        elif isinstance(raw, list):
            origins = [str(o).strip() for o in raw if str(o).strip()]
        else:
            origins = []

        is_production = self.ENVIRONMENT.lower() in ("production", "prod")
        if is_production:
            if not origins or any(o == "*" for o in origins):
                raise ValueError(
                    "Wildcard or empty CORS_ORIGINS is prohibited in production. "
                    "Please configure explicit trusted frontend origins."
                )

        return origins or ["http://localhost:8501", "http://127.0.0.1:8501"]

    def get_sslmode(self) -> Optional[str]:
        """
        Resolve the effective PostgreSQL SSL mode.
        
        - If PGSSLMODE is explicitly configured (e.g. 'require', 'disable'), that value is used.
        - In production (ENVIRONMENT in ('production', 'prod')), securely defaults to 'require'
          when PGSSLMODE is not explicitly specified.
        - In development and test environments, defaults to None (omitting sslmode query parameter,
          preserving unencrypted local Docker PostgreSQL operation).
        """
        if self.PGSSLMODE is not None:
            val = self.PGSSLMODE.strip().lower()
            return val if val else None

        is_production = self.ENVIRONMENT.lower() in ("production", "prod")
        if is_production:
            return "require"

        return None

    def _build_database_url(self, username: str, password: str) -> URL:
        """Construct synchronous psycopg 3 connection URL with SSL query parameter if enabled."""
        sslmode = self.get_sslmode()
        query = {"sslmode": sslmode} if sslmode else {}
        return URL.create(
            drivername="postgresql+psycopg",
            username=username,
            password=password,
            host=self.POSTGRES_HOST,
            port=self.POSTGRES_PORT,
            database=self.POSTGRES_DB,
            query=query,
        )

    def get_admin_database_url(self) -> URL:
        """Construct synchronous psycopg 3 connection URL for administrative tasks."""
        return self._build_database_url(self.POSTGRES_ADMIN_USER, self.POSTGRES_ADMIN_PASSWORD)

    def get_app_database_url(self) -> URL:
        """Construct synchronous psycopg 3 connection URL for app metadata and migrations."""
        return self._build_database_url(self.APP_DB_USER, self.APP_DB_PASSWORD)

    def get_analytics_database_url(self) -> URL:
        """Construct synchronous psycopg 3 connection URL for restricted read-only analytics."""
        return self._build_database_url(self.ANALYTICS_DB_USER, self.ANALYTICS_DB_PASSWORD)


logger = logging.getLogger(__name__)


def fetch_ssm_parameter_json(parameter_name: str, region_name: Optional[str] = None) -> str:
    """
    Fetch a SecureString JSON parameter from AWS Systems Manager Parameter Store.
    
    Security:
    - Never logs parameter values, secrets, or decrypted payloads.
    - Logs high-level parameter name and metadata version only.
    - Decryption is performed in-flight by AWS SSM service using alias/aws/ssm.
    """
    import boto3

    effective_region = (
        region_name
        or os.getenv("AWS_REGION")
        or os.getenv("AWS_DEFAULT_REGION")
        or "ap-south-1"
    )
    ssm_client = boto3.client("ssm", region_name=effective_region)
    try:
        response = ssm_client.get_parameter(Name=parameter_name, WithDecryption=True)
        param_meta = response.get("Parameter", {})
        version = param_meta.get("Version", "unknown")
        logger.info(
            "Successfully retrieved configuration from SSM Parameter Store: %s (version: %s)",
            parameter_name,
            version,
        )
        val = param_meta.get("Value")
        if not val or not val.strip():
            raise ValueError(f"SSM Parameter '{parameter_name}' returned empty or whitespace value.")
        return val
    except Exception as exc:
        logger.error(
            "Failed to retrieve configuration from SSM Parameter Store: %s (%s)",
            parameter_name,
            type(exc).__name__,
        )
        raise


@lru_cache
def get_settings() -> Settings:
    """
    Cached settings singleton with resolution precedence:
    1. If SSM_CONFIG_PARAMETER_NAME is configured, fetch and parse JSON SecureString from SSM.
    2. In production (ENVIRONMENT in ('production', 'prod')):
       - Fails closed: Prohibits fallback to local .env file.
       - Constructs settings strictly from container environment variables (_env_file=None).
       - Validates all production security invariants (cloud host, passwords, auth secret, CORS).
    3. In development/testing:
       - If ENV_FILE is configured, load from specified file path.
       - Otherwise, loads default Pydantic Settings (reading os.environ and fallback .env).
    """
    ssm_param = os.getenv("SSM_CONFIG_PARAMETER_NAME")
    if ssm_param and ssm_param.strip():
        raw_json = fetch_ssm_parameter_json(ssm_param.strip())
        return load_settings_from_ssm_json(raw_json)

    is_production_env = os.getenv("ENVIRONMENT", "").lower() in ("production", "prod")
    if is_production_env:
        # Strict production mode: NEVER fall back to local .env file on disk!
        settings = Settings(_env_file=None)
        settings.validate_production_invariants()
        return settings

    env_file = os.getenv("ENV_FILE")
    if env_file:
        return Settings(_env_file=env_file)
    return Settings()


def clear_settings_cache() -> None:
    """Clear cached settings singleton (useful for testing and reload)."""
    get_settings.cache_clear()



def parse_ssm_json_config(raw_json: str) -> Dict[str, Any]:
    """
    Parse and validate raw JSON string from SSM Parameter Store.
    
    Ensures valid JSON and non-empty mapping without requiring AWS SDK or network access.
    """
    if not raw_json or not raw_json.strip():
        raise ValueError("SSM configuration payload cannot be empty.")
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in SSM configuration payload: {exc}")
    if not isinstance(data, dict):
        raise ValueError("SSM configuration payload must be a JSON object mapping.")
    return data


def load_settings_from_ssm_json(raw_json: str) -> Settings:
    """
    Construct a validated Settings instance from an SSM JSON configuration payload.
    
    Validates configuration keys, SSL modes, CORS origins, and production auth requirements.
    Never falls back to .env files on disk.
    """
    config_dict = parse_ssm_json_config(raw_json)
    settings = Settings(_env_file=None, **config_dict)
    settings.validate_production_invariants()
    return settings



