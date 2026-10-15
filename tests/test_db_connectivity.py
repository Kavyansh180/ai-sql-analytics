"""Tests for PostgreSQL 16 connectivity and role configuration."""
import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine


def test_app_user_connection(app_db_engine: Engine):
    """Verify that app_user can connect and read PostgreSQL version."""
    with app_db_engine.connect() as conn:
        result = conn.execute(text("SELECT version();")).scalar()
        assert result is not None
        assert "PostgreSQL 16" in result

        current_user = conn.execute(text("SELECT current_user;")).scalar()
        assert current_user == "app_user"


def test_analytics_user_connection(analytics_db_engine: Engine):
    """Verify that analytics_user connects with read-only transaction mode and timeout."""
    with analytics_db_engine.connect() as conn:
        current_user = conn.execute(text("SELECT current_user;")).scalar()
        assert current_user == "analytics_user"

        # Verify transaction read only default setting
        is_read_only = conn.execute(text("SHOW transaction_read_only;")).scalar()
        assert is_read_only == "on"

        # Verify statement timeout configured on connection / role
        timeout = conn.execute(text("SHOW statement_timeout;")).scalar()
        assert timeout in ("3s", "3000ms")


def test_seed_data_presence(analytics_db_engine: Engine):
    """Verify that initial business seed tables are populated in analytics schema."""
    with analytics_db_engine.connect() as conn:
        customer_count = conn.execute(text("SELECT count(*) FROM analytics.customers;")).scalar()
        product_count = conn.execute(text("SELECT count(*) FROM analytics.products;")).scalar()
        order_count = conn.execute(text("SELECT count(*) FROM analytics.orders;")).scalar()
        item_count = conn.execute(text("SELECT count(*) FROM analytics.order_items;")).scalar()

        assert customer_count == 10
        assert product_count == 10
        assert order_count == 10
        assert item_count >= 16
