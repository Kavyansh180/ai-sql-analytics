"""Unit tests for SQLGlot AST security validation and guardrails."""
import pytest
from src.security.validator import SQLSecurityValidator


@pytest.fixture
def validator() -> SQLSecurityValidator:
    """Fixture providing a configured SQLSecurityValidator."""
    return SQLSecurityValidator(max_limit=1000)


# ------------------------------------------------------------------------------
# 1. Permitted Read Queries
# ------------------------------------------------------------------------------

def test_valid_simple_select(validator: SQLSecurityValidator):
    """Simple SELECT on allowed analytics table and approved columns is valid."""
    query = "SELECT id, first_name, city FROM analytics.customers WHERE country = 'United States';"
    result = validator.validate_and_sanitize(query)
    assert result.is_valid is True
    assert result.error is None
    assert "LIMIT 1000" in result.sanitized_sql.upper()


def test_valid_join_query(validator: SQLSecurityValidator):
    """JOIN across multiple allowed analytics tables is valid."""
    query = """
    SELECT c.first_name, c.last_name, o.id AS order_id, o.total_amount
    FROM analytics.customers c
    JOIN analytics.orders o ON c.id = o.customer_id
    WHERE o.status = 'completed';
    """
    result = validator.validate_and_sanitize(query)
    assert result.is_valid is True
    assert result.error is None
    assert len(result.tables_referenced) == 2


def test_valid_aggregate_query(validator: SQLSecurityValidator):
    """Aggregations and GROUP BY are valid."""
    query = """
    SELECT category, count(*) AS total_products, avg(price) AS avg_price
    FROM analytics.products
    GROUP BY category
    HAVING count(*) > 1;
    """
    result = validator.validate_and_sanitize(query)
    assert result.is_valid is True
    assert result.error is None


def test_valid_table_without_schema_prefix(validator: SQLSecurityValidator):
    """Unprefixed table name that matches an allowed table is valid."""
    query = "SELECT name, price FROM products WHERE is_active = TRUE;"
    result = validator.validate_and_sanitize(query)
    assert result.is_valid is True
    assert result.error is None


def test_valid_cte_query(validator: SQLSecurityValidator):
    """Common Table Expressions (CTEs) referencing allowed tables are valid."""
    query = """
    WITH customer_orders AS (
        SELECT customer_id, count(*) AS order_count, sum(total_amount) AS lifetime_value
        FROM analytics.orders
        GROUP BY customer_id
    )
    SELECT c.first_name, c.last_name, co.order_count, co.lifetime_value
    FROM analytics.customers c
    JOIN customer_orders co ON c.id = co.customer_id
    ORDER BY co.lifetime_value DESC;
    """
    result = validator.validate_and_sanitize(query)
    assert result.is_valid is True
    assert result.error is None
    assert "LIMIT 1000" in result.sanitized_sql.upper()


def test_valid_multiple_ctes(validator: SQLSecurityValidator):
    """Multiple chained CTEs are valid as long as underlying tables are permitted."""
    query = """
    WITH active_customers AS (
        SELECT id, first_name, last_name FROM analytics.customers
    ),
    high_value_orders AS (
        SELECT id, customer_id, total_amount FROM analytics.orders WHERE total_amount > 200
    )
    SELECT ac.first_name, hvo.total_amount
    FROM active_customers ac
    JOIN high_value_orders hvo ON ac.id = hvo.customer_id;
    """
    result = validator.validate_and_sanitize(query)
    assert result.is_valid is True
    assert result.error is None


def test_valid_nested_subquery(validator: SQLSecurityValidator):
    """Nested subqueries in FROM and WHERE clauses are valid."""
    query = """
    SELECT c.first_name, c.city
    FROM analytics.customers c
    WHERE c.id IN (
        SELECT o.customer_id
        FROM analytics.orders o
        WHERE o.status = 'completed'
    );
    """
    result = validator.validate_and_sanitize(query)
    assert result.is_valid is True
    assert result.error is None


def test_valid_set_operations(validator: SQLSecurityValidator):
    """Read-only set operations (UNION, INTERSECT, EXCEPT) are valid."""
    query_union = "SELECT id, name FROM analytics.products WHERE price > 100 UNION SELECT id, name FROM analytics.products WHERE cost > 50;"
    result_union = validator.validate_and_sanitize(query_union)
    assert result_union.is_valid is True
    assert "LIMIT 1000" in result_union.sanitized_sql.upper()

    query_intersect = "SELECT id FROM analytics.customers INTERSECT SELECT customer_id FROM analytics.orders;"
    result_intersect = validator.validate_and_sanitize(query_intersect)
    assert result_intersect.is_valid is True


# ------------------------------------------------------------------------------
# 2. Rejection of Write, DDL, and Table Creation Operations
# ------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "forbidden_sql",
    [
        "INSERT INTO analytics.customers (first_name, last_name, email, country, city) VALUES ('Eve', 'Hacker', 'eve@evil.com', 'US', 'NYC');",
        "UPDATE analytics.products SET price = 0.01 WHERE id = 1;",
        "DELETE FROM analytics.orders WHERE id = 1001;",
        "DROP TABLE analytics.customers;",
        "ALTER TABLE analytics.products ADD COLUMN stolen_data text;",
        "CREATE TABLE analytics.injected (id int);",
        "TRUNCATE TABLE analytics.orders;",
        "GRANT ALL ON analytics.customers TO public;",
        "SELECT * INTO new_table FROM analytics.products;",  # SELECT INTO table creation attempt
    ],
)
def test_reject_forbidden_operations(validator: SQLSecurityValidator, forbidden_sql: str):
    """Any DML, DDL, DCL, or table creation operation must be rejected by AST validation."""
    result = validator.validate_and_sanitize(forbidden_sql)
    assert result.is_valid is False
    assert result.error is not None


def test_reject_malicious_cte(validator: SQLSecurityValidator):
    """CTEs attempting data mutation inside the CTE body must be rejected."""
    query = "WITH hacked AS (DELETE FROM analytics.orders RETURNING *) SELECT * FROM hacked;"
    result = validator.validate_and_sanitize(query)
    assert result.is_valid is False
    assert "Forbidden operation" in result.error


# ------------------------------------------------------------------------------
# 3. Dangerous and Administrative Functions Rejection
# ------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "dangerous_query, func_name",
    [
        ("SELECT set_config('statement_timeout', '0', false);", "set_config"),
        ("SELECT pg_sleep(5), * FROM analytics.customers;", "pg_sleep"),
        ("SELECT pg_read_file('/etc/passwd');", "pg_read_file"),
        ("SELECT query_to_xml('SELECT * FROM analytics.orders', true, false, '');", "query_to_xml"),
        ("SELECT dblink('host=evil.com', 'SELECT 1');", "dblink"),
        ("SELECT pg_terminate_backend(1234);", "pg_terminate_backend"),
    ],
)
def test_reject_dangerous_functions(validator: SQLSecurityValidator, dangerous_query: str, func_name: str):
    """Attempts to invoke administrative, sleep, or file I/O functions must be rejected."""
    result = validator.validate_and_sanitize(dangerous_query)
    assert result.is_valid is False
    assert f"'{func_name}' is forbidden" in result.error


# ------------------------------------------------------------------------------
# 4. Statement Chaining & SQL Comment Injection
# ------------------------------------------------------------------------------

def test_reject_statement_chaining(validator: SQLSecurityValidator):
    """Multiple statements separated by semicolon must be rejected."""
    query = "SELECT * FROM analytics.customers; DROP TABLE analytics.orders;"
    result = validator.validate_and_sanitize(query)
    assert result.is_valid is False
    assert "Multiple SQL statements" in result.error


def test_sql_comments_handled_safely(validator: SQLSecurityValidator):
    """SQL comments are parsed correctly without hiding chained commands."""
    query = "SELECT id, name FROM analytics.products -- comment here\nWHERE price > 50;"
    result = validator.validate_and_sanitize(query)
    assert result.is_valid is True


# ------------------------------------------------------------------------------
# 5. Schema Isolation & Allowlist Enforcement
# ------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "disallowed_query, expected_schema",
    [
        ("SELECT * FROM app_metadata.query_audit_logs;", "app_metadata"),
        ("SELECT * FROM pg_catalog.pg_user;", "pg_catalog"),
        ("SELECT * FROM information_schema.tables;", "information_schema"),
        ("SELECT * FROM public.some_table;", "public"),
    ],
)
def test_reject_disallowed_schemas(validator: SQLSecurityValidator, disallowed_query: str, expected_schema: str):
    """Accessing system catalogs, app metadata, or public schema must be rejected."""
    result = validator.validate_and_sanitize(disallowed_query)
    assert result.is_valid is False
    assert expected_schema in result.error


def test_reject_unapproved_table(validator: SQLSecurityValidator):
    """Tables outside the approved business allowlist must be rejected."""
    query = "SELECT * FROM analytics.internal_passwords;"
    result = validator.validate_and_sanitize(query)
    assert result.is_valid is False
    assert "not in the approved analytics allowlist" in result.error


# ------------------------------------------------------------------------------
# 6. Row Limit Enforcement & Clamping across Query Shapes
# ------------------------------------------------------------------------------

def test_limit_added_when_missing(validator: SQLSecurityValidator):
    """When a query has no LIMIT clause, default max_limit (1000) is added."""
    query = "SELECT id, name FROM analytics.products;"
    result = validator.validate_and_sanitize(query)
    assert result.is_valid is True
    assert "LIMIT 1000" in result.sanitized_sql.upper()


def test_excessive_limit_clamped(validator: SQLSecurityValidator):
    """When a query specifies LIMIT 50000, it is clamped to max_limit (1000)."""
    query = "SELECT id, name FROM analytics.products LIMIT 50000;"
    result = validator.validate_and_sanitize(query)
    assert result.is_valid is True
    assert "LIMIT 1000" in result.sanitized_sql.upper()


def test_union_limit_clamped(validator: SQLSecurityValidator):
    """UNION query with excessive LIMIT is clamped to max_limit (1000)."""
    query = "(SELECT id FROM analytics.products) UNION (SELECT id FROM analytics.products) LIMIT 50000;"
    result = validator.validate_and_sanitize(query)
    assert result.is_valid is True
    assert "LIMIT 1000" in result.sanitized_sql.upper()


def test_reasonable_limit_preserved(validator: SQLSecurityValidator):
    """When a query specifies a reasonable LIMIT (e.g. 50), it is preserved."""
    query = "SELECT id, name FROM analytics.products LIMIT 50;"
    result = validator.validate_and_sanitize(query)
    assert result.is_valid is True
    assert "LIMIT 50" in result.sanitized_sql.upper()
