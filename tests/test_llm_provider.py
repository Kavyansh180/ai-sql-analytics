"""Tests for LLM provider abstraction, Gemini provider error handling, and Mock provider."""
import pytest
from src.llm.exceptions import LLMConfigurationError
from src.llm.models import GeneratedSQLResponse, QueryStatus
from src.llm.provider import GeminiLLMProvider, MockLLMProvider


def test_gemini_provider_fails_safely_when_key_missing(monkeypatch):
    """GeminiLLMProvider must raise LLMConfigurationError when API key is missing or placeholder."""
    # Ensure environment has no key
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(LLMConfigurationError, match="Gemini API key is not configured"):
        GeminiLLMProvider(api_key="")

    with pytest.raises(LLMConfigurationError, match="Gemini API key is not configured"):
        GeminiLLMProvider(api_key="your_gemini_api_key_here")


def test_mock_provider_canonical_questions():
    """MockLLMProvider properly routes canonical portfolio questions to valid ready queries."""
    provider = MockLLMProvider()

    # 1. Highest revenue
    resp1 = provider.generate_response("Which product generated the highest revenue?")
    assert resp1.status == QueryStatus.READY
    assert "analytics.products" in resp1.sql
    assert "total_revenue" in resp1.sql

    # 2. Monthly revenue trend
    resp2 = provider.generate_response("What was our monthly revenue trend?")
    assert resp2.status == QueryStatus.READY
    assert "DATE_TRUNC" in resp2.sql

    # 3. Most orders
    resp3 = provider.generate_response("Which customers have placed the most orders?")
    assert resp3.status == QueryStatus.READY
    assert "total_orders" in resp3.sql

    # 4. Compare revenue by category
    resp4 = provider.generate_response("Compare revenue by product category.")
    assert resp4.status == QueryStatus.READY
    assert "category_revenue" in resp4.sql

    # 5. Customers by country
    resp5 = provider.generate_response("Which countries have the highest number of customers?")
    assert resp5.status == QueryStatus.READY
    assert "customer_count" in resp5.sql


def test_mock_provider_ambiguous_question():
    """MockLLMProvider returns clarification_needed on ambiguous questions."""
    provider = MockLLMProvider()
    resp = provider.generate_response("Show me the top")
    assert resp.status == QueryStatus.CLARIFICATION_NEEDED
    assert resp.sql is None
    assert resp.clarification_question is not None


def test_mock_provider_unsupported_question():
    """MockLLMProvider returns unsupported on questions outside the business domain."""
    provider = MockLLMProvider()
    resp = provider.generate_response("What is the average employee salary?")
    assert resp.status == QueryStatus.UNSUPPORTED
    assert resp.sql is None


def test_mock_provider_custom_handler():
    """MockLLMProvider respects custom response handlers for edge case testing."""
    custom_resp = GeneratedSQLResponse(
        status=QueryStatus.READY,
        sql="SELECT id FROM analytics.orders;",
        explanation="Custom test explanation",
    )
    provider = MockLLMProvider(custom_handler=lambda q: custom_resp)
    assert provider.generate_response("arbitrary question") == custom_resp
