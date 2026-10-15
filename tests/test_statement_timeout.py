"""Tests for statement timeout enforcement on the analytics role."""
import time
import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, OperationalError


def test_fast_query_succeeds_within_timeout(analytics_db_engine: Engine):
    """Fast queries must complete successfully without being interrupted."""
    start = time.perf_counter()
    with analytics_db_engine.connect() as conn:
        res = conn.execute(text("SELECT 1;")).scalar()
        assert res == 1
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0


def test_long_running_query_canceled_by_timeout(analytics_db_engine: Engine):
    """Queries running longer than statement_timeout (3s) must be killed by PostgreSQL."""
    # Attempting to sleep for 5 seconds when timeout is 3000ms
    sleep_query = text("SELECT pg_sleep(5);")

    start = time.perf_counter()
    with pytest.raises((OperationalError, DBAPIError)) as exc_info:
        with analytics_db_engine.connect() as conn:
            conn.execute(sleep_query)
    elapsed = time.perf_counter() - start

    # Should be canceled at around 3 seconds (allow small margin: 2.8s to 4.5s)
    assert 2.8 <= elapsed <= 4.8, f"Query took {elapsed}s, expected cancellation near 3s"

    err = str(exc_info.value).lower()
    assert "statement timeout" in err or "canceling statement" in err
