"""Tests for Pydantic structured output models and status invariants."""
import pytest
from src.llm.models import GeneratedSQLResponse, QueryStatus


def test_valid_ready_response():
    """Valid ready response must preserve SQL and explanation."""
    resp = GeneratedSQLResponse(
        status=QueryStatus.READY,
        sql="SELECT * FROM analytics.customers LIMIT 10;",
        explanation="Retrieves 10 customer records.",
    )
    assert resp.status == QueryStatus.READY
    assert resp.sql == "SELECT * FROM analytics.customers LIMIT 10;"
    assert resp.explanation == "Retrieves 10 customer records."


def test_ready_response_rejects_empty_sql():
    """A response with status 'ready' cannot have an empty or None sql string."""
    with pytest.raises(ValueError, match="When status is 'ready', 'sql' must be a non-empty"):
        GeneratedSQLResponse(
            status=QueryStatus.READY,
            sql="",
            explanation="Missing SQL",
        )

    with pytest.raises(ValueError, match="When status is 'ready', 'sql' must be a non-empty"):
        GeneratedSQLResponse(
            status=QueryStatus.READY,
            sql=None,
            explanation="Missing SQL",
        )


def test_clarification_needed_clears_sql():
    """A response requiring clarification must force sql to None."""
    resp = GeneratedSQLResponse(
        status=QueryStatus.CLARIFICATION_NEEDED,
        sql="SELECT * FROM analytics.customers;",  # Accidentally included SQL
        explanation="Please clarify which metric you need.",
        clarification_question="Do you want revenue or order counts?",
    )
    assert resp.status == QueryStatus.CLARIFICATION_NEEDED
    assert resp.sql is None
    assert resp.clarification_question == "Do you want revenue or order counts?"


def test_unsupported_clears_sql():
    """An unsupported response must force sql to None."""
    resp = GeneratedSQLResponse(
        status=QueryStatus.UNSUPPORTED,
        sql="SELECT * FROM employees;",  # Accidentally included SQL
        explanation="Employee data is not available.",
    )
    assert resp.status == QueryStatus.UNSUPPORTED
    assert resp.sql is None
