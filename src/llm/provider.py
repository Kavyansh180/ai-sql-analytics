"""LLM provider abstraction and implementations for Natural Language SQL Generation."""
from abc import ABC, abstractmethod
import logging
import random
import re
import time
from typing import Callable, Dict, Optional, Tuple
import httpx
from google import genai
from google.genai import types
from google.genai.errors import APIError, ClientError, ServerError

from src.core.config import get_settings
from src.llm.exceptions import (
    LLMConfigurationError,
    LLMProviderError,
    LLMResponseValidationError,
    LLMTimeoutError,
)
from src.llm.models import GeneratedSQLResponse, QueryStatus
from src.llm.prompts import get_system_prompt

logger = logging.getLogger(__name__)


class LLMProvider(ABC):
    """Abstract interface for LLM natural-language-to-SQL generation providers."""

    @abstractmethod
    def generate_response(self, user_question: str) -> GeneratedSQLResponse:
        """
        Generate a structured decision and SQL response from a user query.
        
        Args:
            user_question: The natural language question from the business analyst.
            
        Returns:
            GeneratedSQLResponse: Validated structured decision contract.
        """
        pass


class GeminiLLMProvider(LLMProvider):
    """Google Gemini API provider implementation using the official google-genai SDK."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
    ):
        settings = get_settings()
        raw_key = api_key if api_key is not None else settings.GEMINI_API_KEY
        self.api_key = raw_key.strip() if raw_key else None
        self.model_name = (model_name or settings.GEMINI_MODEL or "gemini-3.1-flash-lite").strip()
        self.timeout_seconds = timeout_seconds or settings.LLM_REQUEST_TIMEOUT_SECONDS

        if not self.api_key or self.api_key == "your_gemini_api_key_here":
            raise LLMConfigurationError(
                "Gemini API key is not configured. Please set GEMINI_API_KEY in your environment or .env file."
            )

        # Initialize the official GenAI Client with timeout
        # google-genai types.HttpOptions timeout is in milliseconds
        # Disable SDK internal retries (attempts=1) to prevent stacked retry loops with application retry handler
        timeout_ms = int(self.timeout_seconds * 1000)
        http_options = types.HttpOptions(
            timeout=timeout_ms,
            retry_options=types.HttpRetryOptions(attempts=1),
        )
        self.client = genai.Client(api_key=self.api_key, http_options=http_options)

    def _extract_retry_after(self, exc: Exception) -> Optional[float]:
        """Extract Retry-After header or numeric delay from error metadata if present."""
        try:
            response = getattr(exc, "response", None)
            if response and hasattr(response, "headers"):
                retry_header = response.headers.get("retry-after")
                if retry_header:
                    return float(retry_header)
            msg = str(exc)
            match = re.search(r"retry after ([0-9]+(?:\.[0-9]+)?)\s*s", msg, re.IGNORECASE)
            if match:
                return float(match.group(1))
        except Exception:
            pass
        return None

    def _is_retryable_error(self, exc: Exception) -> Tuple[bool, Optional[float]]:
        """
        Determine if an exception from Gemini API is transient and retryable.
        Returns (is_retryable, delay_hint_seconds).
        """
        if isinstance(exc, (httpx.ConnectError, httpx.RemoteProtocolError)):
            return True, None

        if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
            return False, None

        if isinstance(exc, (ClientError, ServerError, APIError)):
            code = getattr(exc, "code", None)
            if code is None and hasattr(exc, "status_code"):
                code = getattr(exc, "status_code")

            if code == 429:
                return True, self._extract_retry_after(exc)

            if code in (500, 502, 503, 504):
                return True, self._extract_retry_after(exc)

            if code in (400, 401, 403, 404):
                return False, None

            err_text = str(exc).lower()
            if any(term in err_text for term in ("resource_exhausted", "429", "rate limit", "quota", "unavailable", "503")):
                return True, self._extract_retry_after(exc)

        return False, None

    def generate_response(self, user_question: str) -> GeneratedSQLResponse:
        """Generate structured SQL response using Gemini API with JSON Schema enforcement and retries."""
        system_prompt = get_system_prompt()

        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="application/json",
            response_schema=GeneratedSQLResponse,
            temperature=0.0,
        )

        settings = get_settings()
        max_retries = settings.LLM_MAX_RETRIES
        base_delay = settings.LLM_RETRY_BASE_DELAY_SECONDS
        max_delay = settings.LLM_RETRY_MAX_DELAY_SECONDS

        response = None
        for attempt in range(max_retries + 1):
            try:
                response = self.client.models.generate_content(
                    model=self.model_name,
                    contents=user_question,
                    config=config,
                )
                break
            except (httpx.TimeoutException, TimeoutError) as timeout_err:
                logger.error("Gemini API request timed out after %s seconds", self.timeout_seconds)
                raise LLMTimeoutError(f"LLM generation timed out after {self.timeout_seconds} seconds.") from timeout_err
            except Exception as exc:
                err_msg = getattr(exc, "message", None) or str(exc)
                if self.api_key and self.api_key in err_msg:
                    err_msg = err_msg.replace(self.api_key, "[REDACTED_API_KEY]")

                is_retryable, delay_hint = self._is_retryable_error(exc)
                if not is_retryable or attempt >= max_retries:
                    log_prefix = f"Gemini API error after {attempt} retries" if attempt > 0 else "Gemini API error"
                    logger.error("%s: %s", log_prefix, err_msg)
                    raise LLMProviderError(f"Gemini API error: {err_msg}") from exc

                if delay_hint is not None and delay_hint > 0:
                    delay = min(delay_hint, max_delay)
                else:
                    delay = min(base_delay * (2 ** attempt) + random.uniform(0.05, 0.15), max_delay)

                logger.warning(
                    "Retryable error from Gemini API (%s) on attempt %d/%d. Retrying in %.2fs: %s",
                    type(exc).__name__,
                    attempt + 1,
                    max_retries,
                    delay,
                    err_msg,
                )
                time.sleep(delay)

        # Extract and parse response
        if response and response.parsed and isinstance(response.parsed, GeneratedSQLResponse):
            return response.parsed

        if not response or not response.text or not response.text.strip():
            raise LLMResponseValidationError("Gemini API returned an empty response body.")

        try:
            return GeneratedSQLResponse.model_validate_json(response.text)
        except Exception as val_err:
            logger.error("Failed to parse Gemini response against GeneratedSQLResponse schema: %s", str(val_err))
            raise LLMResponseValidationError(f"Invalid structured output from LLM: {str(val_err)}") from val_err


class MockLLMProvider(LLMProvider):
    """
    Mock LLM Provider for unit testing, offline development, and deterministic testing.
    
    Can either use built-in canonical question mappings or accept a custom response/handler.
    """

    def __init__(
        self,
        custom_responses: Optional[Dict[str, GeneratedSQLResponse]] = None,
        custom_handler: Optional[Callable[[str], GeneratedSQLResponse]] = None,
    ):
        self.custom_responses = custom_responses or {}
        self.custom_handler = custom_handler

    def generate_response(self, user_question: str) -> GeneratedSQLResponse:
        """Return deterministic mock responses based on question patterns."""
        if self.custom_handler:
            return self.custom_handler(user_question)

        normalized = user_question.strip().lower()

        # Check explicit custom response dictionary
        if normalized in self.custom_responses:
            return self.custom_responses[normalized]

        for pattern, resp in self.custom_responses.items():
            if pattern.lower() in normalized:
                return resp

        # Built-in canonical portfolio queries
        if "highest revenue" in normalized or "most revenue" in normalized or "top product" in normalized:
            return GeneratedSQLResponse(
                status=QueryStatus.READY,
                sql="""
                SELECT p.id, p.name, p.category, SUM(oi.quantity * oi.unit_price) AS total_revenue
                FROM analytics.products p
                JOIN analytics.order_items oi ON p.id = oi.product_id
                JOIN analytics.orders o ON oi.order_id = o.id
                WHERE o.status IN ('completed', 'shipped')
                GROUP BY p.id, p.name, p.category
                ORDER BY total_revenue DESC
                LIMIT 1;
                """.strip(),
                explanation="Calculates the total realized revenue per product from completed/shipped orders and returns the top product.",
            )

        if "monthly revenue trend" in normalized or "monthly revenue" in normalized:
            return GeneratedSQLResponse(
                status=QueryStatus.READY,
                sql="""
                SELECT DATE_TRUNC('month', o.order_date) AS month, SUM(o.total_amount) AS monthly_revenue
                FROM analytics.orders o
                WHERE o.status IN ('completed', 'shipped')
                GROUP BY DATE_TRUNC('month', o.order_date)
                ORDER BY month ASC;
                """.strip(),
                explanation="Aggregates monthly realized revenue from orders grouped by month in chronological order.",
            )

        if "most orders" in normalized or "top customer" in normalized:
            return GeneratedSQLResponse(
                status=QueryStatus.READY,
                sql="""
                SELECT c.id, c.first_name, c.last_name, COUNT(o.id) AS total_orders
                FROM analytics.customers c
                JOIN analytics.orders o ON c.id = o.customer_id
                WHERE o.status IN ('completed', 'shipped')
                GROUP BY c.id, c.first_name, c.last_name
                ORDER BY total_orders DESC
                LIMIT 5;
                """.strip(),
                explanation="Ranks customers by total number of orders placed, returning the top 5.",
            )

        if "category" in normalized and "revenue" in normalized:
            return GeneratedSQLResponse(
                status=QueryStatus.READY,
                sql="""
                SELECT p.category, SUM(oi.quantity * oi.unit_price) AS category_revenue
                FROM analytics.products p
                JOIN analytics.order_items oi ON p.id = oi.product_id
                JOIN analytics.orders o ON oi.order_id = o.id
                WHERE o.status IN ('completed', 'shipped')
                GROUP BY p.category
                ORDER BY category_revenue DESC;
                """.strip(),
                explanation="Compares total realized revenue across all product categories.",
            )

        if "highest number of customers" in normalized or "customers by country" in normalized:
            return GeneratedSQLResponse(
                status=QueryStatus.READY,
                sql="""
                SELECT country, COUNT(id) AS customer_count
                FROM analytics.customers
                GROUP BY country
                ORDER BY customer_count DESC;
                """.strip(),
                explanation="Counts the number of registered customers in each country, sorted by highest count.",
            )

        # Ambiguous query triggers clarification
        if normalized in ("top", "show me the best", "details", "revenue"):
            return GeneratedSQLResponse(
                status=QueryStatus.CLARIFICATION_NEEDED,
                explanation="Your request is too ambiguous to generate a single query.",
                clarification_question="Would you like to see top products by revenue, top customers by order count, or revenue by category?",
            )

        # Out-of-domain query triggers unsupported
        if any(term in normalized for term in ["salary", "employee", "payroll", "weather", "server", "password"]):
            return GeneratedSQLResponse(
                status=QueryStatus.UNSUPPORTED,
                explanation="The requested information is outside the scope of the business analytics database (customers, products, orders, order_items).",
            )

        # Raw customer email / PII extraction requests trigger unsupported
        if any(term in normalized for term in ["email address", "customer email", "list emails", "customer emails", "emails"]):
            return GeneratedSQLResponse(
                status=QueryStatus.UNSUPPORTED,
                explanation="Extracting or dumping raw customer email addresses is restricted by data governance policy. Only aggregated customer analytics are supported.",
            )

        # Fallback default
        return GeneratedSQLResponse(
            status=QueryStatus.CLARIFICATION_NEEDED,
            explanation="Could you please provide more details on the specific metrics or entities you would like to analyze?",
            clarification_question="What specific metric would you like to see from customers, products, or orders?",
        )
