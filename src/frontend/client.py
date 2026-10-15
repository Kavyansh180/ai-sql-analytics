"""HTTP API client for communicating with the FastAPI Analytics Copilot backend."""
import logging
import os
from typing import Any, Dict, Optional
import httpx

logger = logging.getLogger(__name__)

DEFAULT_API_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_TIMEOUT_SECONDS = 45.0


def get_api_base_url() -> str:
    """
    Resolve API base URL with fallback precedence:
    1. Streamlit secrets (if running in Streamlit and configured)
    2. Environment variable API_BASE_URL
    3. Default localhost URL http://127.0.0.1:8000
    """
    try:
        import streamlit as st
        if hasattr(st, "secrets") and "API_BASE_URL" in st.secrets:
            return str(st.secrets["API_BASE_URL"]).rstrip("/")
    except Exception:
        pass

    return os.getenv("API_BASE_URL", DEFAULT_API_BASE_URL).rstrip("/")


def get_backend_api_secret() -> Optional[str]:
    """
    Resolve backend API shared secret with fallback precedence:
    1. Streamlit secrets (st.secrets["BACKEND_API_SECRET"])
    2. Environment variable BACKEND_API_SECRET
    3. Environment variable COPILOT_API_SECRET
    4. None (unauthenticated / local development mode)
    """
    try:
        import streamlit as st
        if hasattr(st, "secrets") and "BACKEND_API_SECRET" in st.secrets:
            val = str(st.secrets["BACKEND_API_SECRET"]).strip()
            if val:
                return val
    except Exception:
        pass

    for env_key in ("BACKEND_API_SECRET", "COPILOT_API_SECRET"):
        val = os.getenv(env_key)
        if val and val.strip():
            return val.strip()

    return None


class AnalyticsAPIError(Exception):
    """User-friendly, sanitized exception for frontend API client interactions."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class AnalyticsClient:
    """
    Synchronous HTTP client for interacting with the Analytics Copilot backend.
    
    Provides query submission, health checking, deduplication, and error sanitization.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_secret: Optional[str] = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        transport: Optional[httpx.BaseTransport] = None,
    ):
        self.base_url = (base_url or get_api_base_url()).rstrip("/")
        self.api_secret = api_secret if api_secret is not None else get_backend_api_secret()
        self.timeout_seconds = timeout_seconds
        self.transport = transport
        self._last_question: Optional[str] = None
        self._last_response: Optional[Dict[str, Any]] = None

    def _build_headers(self) -> Dict[str, str]:
        """Construct request headers including X-Copilot-Secret if configured."""
        headers: Dict[str, str] = {}
        if self.api_secret:
            headers["X-Copilot-Secret"] = self.api_secret
        return headers

    def check_health(self) -> Dict[str, Any]:
        """
        Verify backend connectivity via GET /health.
        
        Returns:
            Dict containing backend status and version, or unhealthy indicator.
        """
        url = f"{self.base_url}/health"
        headers = self._build_headers()
        try:
            with httpx.Client(transport=self.transport, timeout=5.0) as client:
                resp = client.get(url, headers=headers)
                resp.raise_for_status()
                return resp.json()
        except Exception as err:
            logger.warning("Health check failed for %s: %s", url, type(err).__name__)
            return {"status": "unhealthy", "error": "Backend service unreachable."}

    def query(self, question: str, force: bool = False) -> Dict[str, Any]:
        """
        Send a natural language business question to POST /api/v1/analytics/query.
        
        Args:
            question: Business query text in English.
            force: If False, skips sending if the identical question was just sent and cached.
            
        Returns:
            Dict containing the parsed AnalyticsQueryResponse JSON structure.
            
        Raises:
            AnalyticsAPIError: If a connection, timeout, HTTP, or validation error occurs.
        """
        cleaned_question = question.strip() if question else ""
        if not cleaned_question:
            raise AnalyticsAPIError("Question cannot be empty.")

        # Avoid redundant duplicate requests from identical re-invocations on reruns
        if not force and self._last_question == cleaned_question and self._last_response is not None:
            return self._last_response

        url = f"{self.base_url}/api/v1/analytics/query"
        payload = {"question": cleaned_question}
        headers = self._build_headers()

        try:
            with httpx.Client(transport=self.transport, timeout=self.timeout_seconds) as client:
                response = client.post(url, json=payload, headers=headers)
        except httpx.ConnectError:
            raise AnalyticsAPIError(
                f"Could not connect to the analytics backend at {self.base_url}. "
                "Please verify that the FastAPI server is running."
            )
        except httpx.TimeoutException:
            raise AnalyticsAPIError(
                f"The analytics backend did not respond within {int(self.timeout_seconds)} seconds. "
                "The query or LLM provider timed out."
            )
        except httpx.RequestError as req_err:
            raise AnalyticsAPIError(
                f"Network communication error contacting backend: {type(req_err).__name__}"
            )

        # Handle specific HTTP error status codes gracefully
        if response.status_code == 401:
            raise AnalyticsAPIError(
                "Access unauthorized: The backend rejected the authentication credential. "
                "Please verify BACKEND_API_SECRET in Streamlit secrets.",
                status_code=401,
            )
        elif response.status_code == 422:
            raise AnalyticsAPIError(
                "Invalid question format. Please provide a question between 3 and 500 characters.",
                status_code=422,
            )
        elif response.status_code == 503:
            raise AnalyticsAPIError(
                "The AI query generation service is not configured properly on the server.",
                status_code=503,
            )
        elif response.status_code == 504:
            raise AnalyticsAPIError(
                "The upstream AI service timed out while generating SQL. Please try again.",
                status_code=504,
            )
        elif response.status_code == 502:
            raise AnalyticsAPIError(
                "Failed to generate SQL from AI provider. Please try again or rephrase.",
                status_code=502,
            )
        elif response.status_code >= 400:
            raise AnalyticsAPIError(
                f"Backend request failed with status code {response.status_code}.",
                status_code=response.status_code,
            )

        try:
            data = response.json()
        except Exception:
            raise AnalyticsAPIError("Invalid JSON response received from backend.")

        # Validate that the response conforms to AnalyticsQueryResponse structure
        if not isinstance(data, dict) or "status" not in data or "explanation" not in data:
            raise AnalyticsAPIError("Unexpected response structure received from analytics backend.")

        # Cache last successful query to prevent accidental re-queries
        self._last_question = cleaned_question
        self._last_response = data
        return data
