"""Tests for database session helpers and execute_readonly_query utility."""
import pytest
from src.db.session import execute_readonly_query, get_analytics_db, get_app_db


def test_execute_readonly_query_helper():
    """Verify that execute_readonly_query executes successfully and returns expected structure."""
    sql = "SELECT id, first_name, country FROM analytics.customers ORDER BY id LIMIT 3;"
    columns, rows, execution_time_ms = execute_readonly_query(sql)

    assert columns == ["id", "first_name", "country"]
    assert len(rows) == 3
    assert rows[0]["first_name"] == "Emma"
    assert rows[0]["country"] == "United Kingdom"
    assert execution_time_ms > 0


def test_execute_readonly_query_blocks_malicious_query():
    """execute_readonly_query rejects DDL queries before database execution."""
    with pytest.raises(PermissionError) as exc_info:
        execute_readonly_query("DROP TABLE analytics.customers;")

    assert "Query security policy violation" in str(exc_info.value)


def test_execute_readonly_query_with_cte():
    """execute_readonly_query executes valid CTE queries cleanly."""
    sql = """
    WITH customer_spending AS (
        SELECT customer_id, sum(total_amount) AS total_spent
        FROM analytics.orders
        GROUP BY customer_id
    )
    SELECT c.first_name, cs.total_spent
    FROM analytics.customers c
    JOIN customer_spending cs ON c.id = cs.customer_id
    ORDER BY cs.total_spent DESC
    LIMIT 2;
    """
    columns, rows, execution_time_ms = execute_readonly_query(sql)
    assert "first_name" in columns
    assert "total_spent" in columns
    assert len(rows) == 2
    assert execution_time_ms > 0


def test_execute_readonly_query_bounds_rows():
    """execute_readonly_query respects max_rows bound."""
    sql = "SELECT id, name FROM analytics.products;"
    columns, rows, _ = execute_readonly_query(sql, max_rows=2)
    assert len(rows) == 2


def test_session_context_managers():
    """Verify both get_app_db and get_analytics_db context managers yield active sessions."""
    with get_app_db() as app_session:
        assert app_session.is_active

    with get_analytics_db() as analytics_session:
        assert analytics_session.is_active
