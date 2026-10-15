"""Integration tests verifying actual PostgreSQL 16 role permissions and isolation."""
import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError


# ------------------------------------------------------------------------------
# 1. Permitted Reads for analytics_user
# ------------------------------------------------------------------------------

def test_analytics_user_can_read_business_tables(analytics_db_engine: Engine):
    """analytics_user has explicit SELECT permission on all approved business tables/columns."""
    tables = ["customers", "products", "orders", "order_items"]
    with analytics_db_engine.connect() as conn:
        for tbl in tables:
            if tbl == "customers":
                # Customers has column-level permissions excluding email
                rows = conn.execute(text(f"SELECT id, first_name, last_name FROM analytics.{tbl} LIMIT 5;")).fetchall()
            else:
                rows = conn.execute(text(f"SELECT * FROM analytics.{tbl} LIMIT 5;")).fetchall()
            assert len(rows) > 0, f"Expected rows in analytics.{tbl}"


def test_analytics_user_cannot_select_prohibited_email_column(analytics_db_engine: Engine):
    """PostgreSQL rejects direct SELECT of prohibited email column at the database boundary."""
    with analytics_db_engine.connect() as conn:
        with pytest.raises(DBAPIError) as exc_info:
            conn.execute(text("SELECT email FROM analytics.customers LIMIT 5;")).fetchall()
        err = str(exc_info.value).lower()
        assert "permission denied for table customers" in err or "permission denied" in err


def test_analytics_user_cannot_select_star_from_customers(analytics_db_engine: Engine):
    """PostgreSQL rejects SELECT * on customers because it includes the prohibited email column."""
    with analytics_db_engine.connect() as conn:
        with pytest.raises(DBAPIError) as exc_info:
            conn.execute(text("SELECT * FROM analytics.customers LIMIT 5;")).fetchall()
        err = str(exc_info.value).lower()
        assert "permission denied for table customers" in err or "permission denied" in err


def test_analytics_user_can_join_customers_on_allowed_columns(analytics_db_engine: Engine):
    """analytics_user can join customers on allowed columns (id, names) and compute aggregates."""
    query = text(
        """
        SELECT c.first_name, c.last_name, COUNT(o.id) AS total_orders
        FROM analytics.customers c
        JOIN analytics.orders o ON c.id = o.customer_id
        GROUP BY c.id, c.first_name, c.last_name
        ORDER BY total_orders DESC
        LIMIT 5;
        """
    )
    with analytics_db_engine.connect() as conn:
        rows = conn.execute(query).fetchall()
        assert len(rows) > 0


# ------------------------------------------------------------------------------
# 2. Rejected DML Writes (INSERT, UPDATE, DELETE) for analytics_user
# ------------------------------------------------------------------------------

def test_analytics_user_cannot_insert(analytics_db_engine: Engine):
    """PostgreSQL rejects INSERT attempts by analytics_user."""
    query = text(
        """
        INSERT INTO analytics.customers (first_name, last_name, email, country, city)
        VALUES ('Unauthorized', 'User', 'unauthorized@example.com', 'US', 'Seattle');
        """
    )
    with pytest.raises(DBAPIError) as exc_info:
        with analytics_db_engine.connect() as conn:
            conn.execute(query)
            conn.commit()

    err = str(exc_info.value).lower()
    assert "read-only transaction" in err or "permission denied" in err


def test_analytics_user_cannot_update(analytics_db_engine: Engine):
    """PostgreSQL rejects UPDATE attempts by analytics_user."""
    query = text("UPDATE analytics.products SET price = 9999.00 WHERE id = 1;")
    with pytest.raises(DBAPIError) as exc_info:
        with analytics_db_engine.connect() as conn:
            conn.execute(query)
            conn.commit()

    err = str(exc_info.value).lower()
    assert "read-only transaction" in err or "permission denied" in err


def test_analytics_user_cannot_delete(analytics_db_engine: Engine):
    """PostgreSQL rejects DELETE attempts by analytics_user."""
    query = text("DELETE FROM analytics.orders WHERE id = 1001;")
    with pytest.raises(DBAPIError) as exc_info:
        with analytics_db_engine.connect() as conn:
            conn.execute(query)
            conn.commit()

    err = str(exc_info.value).lower()
    assert "read-only transaction" in err or "permission denied" in err


def test_cannot_bypass_via_set_transaction_read_only(analytics_db_engine: Engine):
    """Attempting to disable read-only transaction mode does not grant table write permissions."""
    with pytest.raises(DBAPIError) as exc_info:
        with analytics_db_engine.connect() as conn:
            conn.execute(text("SET transaction_read_only = off;"))
            conn.execute(
                text("INSERT INTO analytics.customers (first_name, last_name, email, country, city) VALUES ('X', 'Y', 'xy@ex.com', 'US', 'NYC');")
            )
            conn.commit()

    err = str(exc_info.value).lower()
    assert "permission denied for table customers" in err or "permission denied" in err


# ------------------------------------------------------------------------------
# 3. Rejected DDL & Administrative Actions (DROP, CREATE, TRUNCATE, GRANT, SET)
# ------------------------------------------------------------------------------

def test_analytics_user_cannot_drop_table(analytics_db_engine: Engine):
    """PostgreSQL rejects DROP TABLE attempts by analytics_user."""
    query = text("DROP TABLE analytics.customers;")
    with pytest.raises(DBAPIError) as exc_info:
        with analytics_db_engine.connect() as conn:
            conn.execute(query)
            conn.commit()

    err = str(exc_info.value).lower()
    assert "read-only transaction" in err or "permission denied" in err or "must be owner" in err


def test_analytics_user_cannot_create_table(analytics_db_engine: Engine):
    """PostgreSQL rejects CREATE TABLE attempts by analytics_user."""
    query = text("CREATE TABLE analytics.malicious_table (id int);")
    with pytest.raises(DBAPIError) as exc_info:
        with analytics_db_engine.connect() as conn:
            conn.execute(query)
            conn.commit()

    err = str(exc_info.value).lower()
    assert "read-only transaction" in err or "permission denied" in err


def test_analytics_user_cannot_create_temp_table(analytics_db_engine: Engine):
    """PostgreSQL rejects CREATE TEMP TABLE attempts by analytics_user (TEMPORARY revoked on DB)."""
    query = text("CREATE TEMP TABLE test_temp_table (id int);")
    with pytest.raises(DBAPIError) as exc_info:
        with analytics_db_engine.connect() as conn:
            conn.execute(query)
            conn.commit()

    err = str(exc_info.value).lower()
    assert "read-only transaction" in err or "permission denied" in err


def test_analytics_user_cannot_truncate(analytics_db_engine: Engine):
    """PostgreSQL rejects TRUNCATE TABLE attempts by analytics_user."""
    query = text("TRUNCATE TABLE analytics.orders;")
    with pytest.raises(DBAPIError) as exc_info:
        with analytics_db_engine.connect() as conn:
            conn.execute(query)
            conn.commit()

    err = str(exc_info.value).lower()
    assert "read-only transaction" in err or "permission denied" in err


def test_analytics_user_cannot_grant(analytics_db_engine: Engine):
    """PostgreSQL rejects GRANT attempts by analytics_user."""
    query = text("GRANT ALL ON analytics.customers TO public;")
    with pytest.raises(DBAPIError) as exc_info:
        with analytics_db_engine.connect() as conn:
            conn.execute(query)
            conn.commit()

    err = str(exc_info.value).lower()
    assert "read-only transaction" in err or "permission denied" in err or "must be owner" in err


def test_analytics_user_cannot_invoke_set_config(analytics_db_engine: Engine):
    """PostgreSQL rejects set_config execution by analytics_user (preventing timeout tampering)."""
    query = text("SELECT set_config('statement_timeout', '0', false);")
    with pytest.raises(DBAPIError) as exc_info:
        with analytics_db_engine.connect() as conn:
            conn.execute(query)

    err = str(exc_info.value).lower()
    assert "permission denied for function set_config" in err or "permission denied" in err


# ------------------------------------------------------------------------------
# 4. Metadata Isolation: analytics_user vs app_user
# ------------------------------------------------------------------------------

def test_analytics_user_blocked_from_metadata_schema(analytics_db_engine: Engine):
    """analytics_user has NO access to app_metadata tables (permission denied at DB level)."""
    query = text("SELECT * FROM app_metadata.query_audit_logs;")
    with pytest.raises(DBAPIError) as exc_info:
        with analytics_db_engine.connect() as conn:
            conn.execute(query)

    err = str(exc_info.value).lower()
    assert "permission denied for schema app_metadata" in err


def test_app_user_can_manage_metadata(app_db_engine: Engine):
    """app_user owns and can write to the app_metadata schema."""
    insert_sql = text(
        """
        INSERT INTO app_metadata.query_audit_logs (
            natural_language_prompt, generated_sql, execution_status, row_count, execution_time_ms, executed_by_role
        ) VALUES (
            'Show top 5 customers',
            'SELECT * FROM analytics.customers LIMIT 5;',
            'SUCCESS',
            5,
            12.5,
            'analytics_user'
        ) RETURNING id;
        """
    )
    with app_db_engine.connect() as conn:
        new_id = conn.execute(insert_sql).scalar()
        conn.commit()
        assert new_id is not None

        row = conn.execute(
            text(f"SELECT natural_language_prompt FROM app_metadata.query_audit_logs WHERE id = {new_id};")
        ).scalar()
        assert row == "Show top 5 customers"


# ------------------------------------------------------------------------------
# 5. Public Schema Hardening
# ------------------------------------------------------------------------------

def test_public_schema_access_revoked(analytics_db_engine: Engine):
    """Public schema creation privilege is revoked, preventing privilege escalation."""
    query = text("CREATE TABLE public.privilege_leak_test (id int);")
    with pytest.raises(DBAPIError) as exc_info:
        with analytics_db_engine.connect() as conn:
            conn.execute(query)
            conn.commit()

    err = str(exc_info.value).lower()
    assert "permission denied" in err or "read-only transaction" in err
