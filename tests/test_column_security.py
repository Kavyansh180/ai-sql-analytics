"""Comprehensive Adversarial and Column-Level Security Tests (Phase 3.3).

Verifies deterministic AST enforcement, wildcard protections, CTE/subquery isolation,
database boundary permissions, schema drift detection, and execution prevention.
"""
from unittest.mock import patch
import pytest
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from src.db.models.business import Customer, Order, OrderItem, Product
from src.llm.models import GeneratedSQLResponse, QueryStatus
from src.llm.provider import MockLLMProvider
from src.llm.service import NaturalLanguageSQLService
from src.security.policy import (
    APPROVED_TABLE_COLUMNS,
    PROHIBITED_TABLE_COLUMNS,
    PROHIBITED_CONTACT_COLUMNS,
    is_approved_column,
    is_prohibited_column,
    is_wildcard_restricted,
)
from src.security.validator import SQLSecurityValidator


@pytest.fixture
def validator() -> SQLSecurityValidator:
    """Fixture providing a configured SQLSecurityValidator."""
    return SQLSecurityValidator()


# ------------------------------------------------------------------------------
# 1. Direct Column Access Rejection
# ------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "sql",
    [
        "SELECT email FROM analytics.customers;",
        "SELECT c.email FROM analytics.customers AS c;",
        "SELECT c.email FROM analytics.customers c;",
        "SELECT customers.email FROM analytics.customers;",
        'SELECT "email" FROM analytics.customers;',
        'SELECT c."email" FROM analytics.customers AS c;',
        "SELECT c.email_address FROM analytics.customers AS c;",
        "SELECT email, first_name FROM analytics.customers;",
        "SELECT first_name, email FROM analytics.customers;",
    ],
)
def test_direct_selection_prohibited_email(validator: SQLSecurityValidator, sql: str):
    """Direct selection of prohibited email column in any syntax is rejected."""
    result = validator.validate_and_sanitize(sql)
    assert result.is_valid is False
    assert "Column-level security violation" in result.error
    assert "email" in result.error.lower()


# ------------------------------------------------------------------------------
# 2. Aliases, Functions, and Expression Evasion
# ------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "sql",
    [
        "SELECT c.email AS contact FROM analytics.customers AS c;",
        "SELECT c.email AS user_identifier FROM analytics.customers AS c;",
        "SELECT LOWER(c.email) FROM analytics.customers AS c;",
        "SELECT UPPER(c.email) AS up_email FROM analytics.customers AS c;",
        "SELECT SUBSTRING(c.email FROM 1 FOR 5) FROM analytics.customers AS c;",
        "SELECT CONCAT(c.first_name, ' <', c.email, '>') FROM analytics.customers AS c;",
        "SELECT COALESCE(c.email, 'none') FROM analytics.customers AS c;",
        "SELECT 1 FROM analytics.customers WHERE email LIKE '%@gmail.com';",
        "SELECT 1 FROM analytics.customers WHERE LOWER(email) = 'test@example.com';",
        "SELECT first_name FROM analytics.customers ORDER BY email ASC;",
        "SELECT country, COUNT(*) FROM analytics.customers GROUP BY country, email;",
        "SELECT COUNT(email) FROM analytics.customers;",
    ],
)
def test_aliased_and_expression_email_rejected(validator: SQLSecurityValidator, sql: str):
    """Prohibited column wrapped in aliases, functions, WHERE, ORDER BY, or GROUP BY is rejected."""
    result = validator.validate_and_sanitize(sql)
    assert result.is_valid is False
    assert "Column-level security violation" in result.error
    assert "email" in result.error.lower()


# ------------------------------------------------------------------------------
# 3. Wildcard Selection (* and c.*) Protection
# ------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM analytics.customers;",
        "SELECT c.* FROM analytics.customers AS c;",
        "SELECT c.* FROM analytics.customers c;",
        "SELECT customers.* FROM analytics.customers;",
        "SELECT c.*, o.id FROM analytics.customers c JOIN analytics.orders o ON c.id = o.customer_id;",
        "SELECT o.id, c.* FROM analytics.customers c JOIN analytics.orders o ON c.id = o.customer_id;",
    ],
)
def test_wildcard_on_sensitive_table_rejected(validator: SQLSecurityValidator, sql: str):
    """Wildcard selection on tables containing prohibited columns is strictly blocked."""
    result = validator.validate_and_sanitize(sql)
    assert result.is_valid is False
    assert "Column-level security violation" in result.error
    assert "Wildcard selection" in result.error
    assert "customers" in result.error


def test_wildcard_aggregates_and_safe_tables_permitted(validator: SQLSecurityValidator):
    """COUNT(*) and wildcard on non-sensitive tables (products, orders, order_items) are allowed."""
    # COUNT(*) does not project or return raw column data
    res_count = validator.validate_and_sanitize("SELECT COUNT(*) FROM analytics.customers;")
    assert res_count.is_valid is True

    res_count_c = validator.validate_and_sanitize("SELECT COUNT(c.*) FROM analytics.customers c;")
    assert res_count_c.is_valid is True

    # Products has no prohibited columns
    res_prod = validator.validate_and_sanitize("SELECT * FROM analytics.products;")
    assert res_prod.is_valid is True

    res_prod_p = validator.validate_and_sanitize("SELECT p.* FROM analytics.products p;")
    assert res_prod_p.is_valid is True

    # Order items has no prohibited columns
    res_oi = validator.validate_and_sanitize("SELECT oi.* FROM analytics.order_items oi;")
    assert res_oi.is_valid is True


# ------------------------------------------------------------------------------
# 4. CTEs and Subqueries Evasion Attempts
# ------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "sql",
    [
        "WITH cte AS (SELECT email FROM analytics.customers) SELECT * FROM cte;",
        "WITH cte AS (SELECT email AS contact FROM analytics.customers) SELECT contact FROM cte;",
        "WITH cte AS (SELECT c.id, c.email FROM analytics.customers c) SELECT id FROM cte;",
        "WITH cte AS (SELECT * FROM analytics.customers) SELECT id FROM cte;",
        "SELECT * FROM (SELECT email FROM analytics.customers) AS sub;",
        "SELECT sub.contact FROM (SELECT email AS contact FROM analytics.customers) AS sub;",
        "SELECT sub.id FROM (SELECT * FROM analytics.customers) AS sub;",
        "SELECT * FROM (SELECT c.email, o.id FROM analytics.customers c JOIN analytics.orders o ON c.id = o.customer_id) AS sub;",
    ],
)
def test_cte_and_subquery_prohibited_column_rejected(validator: SQLSecurityValidator, sql: str):
    """Prohibited columns or sensitive wildcards inside CTEs and subqueries cannot be smuggled out."""
    result = validator.validate_and_sanitize(sql)
    assert result.is_valid is False
    assert "Column-level security violation" in result.error


def test_safe_cte_and_subqueries_permitted(validator: SQLSecurityValidator):
    """CTEs and subqueries selecting approved columns are permitted."""
    sql_cte = """
    WITH top_customers AS (
        SELECT c.id, c.first_name, COUNT(o.id) AS total_orders
        FROM analytics.customers c
        JOIN analytics.orders o ON c.id = o.customer_id
        GROUP BY c.id, c.first_name
    )
    SELECT id, first_name, total_orders
    FROM top_customers
    ORDER BY total_orders DESC;
    """
    res = validator.validate_and_sanitize(sql_cte)
    assert res.is_valid is True


# ------------------------------------------------------------------------------
# 5. Joins and Identifier Resolution
# ------------------------------------------------------------------------------

def test_join_prohibited_column_rejected(validator: SQLSecurityValidator):
    """Prohibited column access across table joins is blocked."""
    sql = """
    SELECT c.email, p.name, oi.quantity
    FROM analytics.customers c
    JOIN analytics.orders o ON c.id = o.customer_id
    JOIN analytics.order_items oi ON o.id = oi.order_id
    JOIN analytics.products p ON oi.product_id = p.id;
    """
    result = validator.validate_and_sanitize(sql)
    assert result.is_valid is False
    assert "Column-level security violation" in result.error
    assert "email" in result.error.lower()


def test_join_using_prohibited_column_rejected(validator: SQLSecurityValidator):
    """JOIN ... USING (email) is rejected."""
    sql = "SELECT c.id FROM analytics.customers c JOIN analytics.orders o USING (email);"
    result = validator.validate_and_sanitize(sql)
    assert result.is_valid is False
    assert "Column-level security violation" in result.error


def test_join_on_approved_columns_permitted(validator: SQLSecurityValidator):
    """Legitimate analytical join across customers, orders, and order_items succeeds."""
    sql = """
    SELECT c.first_name, c.last_name, SUM(oi.quantity * oi.unit_price) AS total_spent
    FROM analytics.customers c
    JOIN analytics.orders o ON c.id = o.customer_id
    JOIN analytics.order_items oi ON o.id = oi.order_id
    WHERE o.status IN ('completed', 'shipped')
    GROUP BY c.id, c.first_name, c.last_name
    ORDER BY total_spent DESC;
    """
    result = validator.validate_and_sanitize(sql)
    assert result.is_valid is True


# ------------------------------------------------------------------------------
# 6. Case Insensitivity & Quoting Variations
# ------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "sql",
    [
        "SELECT C.EMAIL FROM analytics.customers C;",
        "SELECT c.Email FROM analytics.customers c;",
        "SELECT c.EmAiL AS Contact FROM analytics.customers c;",
        'SELECT c."email" FROM analytics.customers c;',
        'SELECT c."EMAIL" FROM analytics.customers c;',
        "SELECT LOWER(C.EMAIL) FROM analytics.customers C;",
    ],
)
def test_case_variation_prohibited_column_rejected(validator: SQLSecurityValidator, sql: str):
    """Mixed-case and quoted identifiers for prohibited columns are safely caught."""
    result = validator.validate_and_sanitize(sql)
    assert result.is_valid is False
    assert "Column-level security violation" in result.error


# ------------------------------------------------------------------------------
# 7. Unapproved Column & Schema Drift Protection
# ------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "sql",
    [
        "SELECT c.unapproved_secret_token FROM analytics.customers c;",
        "SELECT p.internal_cost_margin FROM analytics.products p;",
        "SELECT o.unapproved_payment_hash FROM analytics.orders o;",
        "SELECT c.social_security_number FROM analytics.customers c;",
    ],
)
def test_unapproved_column_rejected(validator: SQLSecurityValidator, sql: str):
    """Columns not in the approved analytical allowlist are rejected fail-closed."""
    result = validator.validate_and_sanitize(sql)
    assert result.is_valid is False
    assert "Column-level security violation" in result.error


def test_schema_drift_detection_orm_models_classified():
    """Verify that every column in all SQLAlchemy models is explicitly classified in the security policy."""
    model_map = {
        "customers": Customer,
        "products": Product,
        "orders": Order,
        "order_items": OrderItem,
    }

    for table_name, model_cls in model_map.items():
        mapper = inspect(model_cls)
        orm_columns = {col.key for col in mapper.columns}
        approved = APPROVED_TABLE_COLUMNS.get(table_name, set())
        prohibited = PROHIBITED_TABLE_COLUMNS.get(table_name, set())

        # Every ORM column must be either approved or prohibited
        unclassified = orm_columns - (approved | prohibited)
        assert not unclassified, (
            f"Table '{table_name}' has unclassified columns in ORM model {model_cls.__name__}: {unclassified}. "
            "All columns must be classified in src.security.policy!"
        )


# ------------------------------------------------------------------------------
# 8. Execution Function Never Called for Blocked Queries
# ------------------------------------------------------------------------------

def test_mock_llm_prohibited_email_blocked_before_db_execution():
    """Verify that if an LLM returns a query accessing email, execute_readonly_query is NEVER called."""
    mock_provider = MockLLMProvider(
        custom_responses={
            "show all customer emails": GeneratedSQLResponse(
                status=QueryStatus.READY,
                sql="SELECT c.first_name, c.email FROM analytics.customers c;",
                explanation="Extract customer emails.",
            )
        }
    )
    service = NaturalLanguageSQLService(provider=mock_provider)

    with patch("src.llm.service.execute_readonly_query") as mock_exec:
        result = service.execute_question("show all customer emails")

        # The execution function must NOT have been called
        mock_exec.assert_not_called()

        # The response must be unsupported with security error
        assert result.status == QueryStatus.UNSUPPORTED
        assert result.rows == []
        assert "violates database security policies" in result.explanation
        assert "Column-level security violation" in result.error


def test_direct_execution_helper_blocks_prohibited_email():
    """execute_readonly_query itself raises PermissionError before touching database."""
    from src.db.session import execute_readonly_query

    with pytest.raises(PermissionError) as exc_info:
        execute_readonly_query("SELECT email FROM analytics.customers;")

    assert "Query security policy violation" in str(exc_info.value)
    assert "Column-level security violation" in str(exc_info.value)


# ------------------------------------------------------------------------------
# 9. Database-Level Enforcement (PostgreSQL Engine Boundary)
# ------------------------------------------------------------------------------

def test_database_level_email_denied_for_analytics_user(analytics_db_engine: Engine):
    """Direct PostgreSQL query as analytics_user fails with permission denied on email."""
    with analytics_db_engine.connect() as conn:
        with pytest.raises(DBAPIError) as exc_info:
            conn.execute(text("SELECT email FROM analytics.customers;")).fetchall()
        err = str(exc_info.value).lower()
        assert "permission denied for table customers" in err or "permission denied" in err


def test_database_level_star_denied_for_analytics_user(analytics_db_engine: Engine):
    """Direct PostgreSQL query as analytics_user fails with permission denied on SELECT *."""
    with analytics_db_engine.connect() as conn:
        with pytest.raises(DBAPIError) as exc_info:
            conn.execute(text("SELECT * FROM analytics.customers;")).fetchall()
        err = str(exc_info.value).lower()
        assert "permission denied for table customers" in err or "permission denied" in err


def test_database_level_approved_columns_succeed_for_analytics_user(analytics_db_engine: Engine):
    """Direct PostgreSQL query as analytics_user succeeds on approved columns."""
    with analytics_db_engine.connect() as conn:
        rows = conn.execute(text("SELECT id, first_name, last_name, country, city FROM analytics.customers LIMIT 5;")).fetchall()
        assert len(rows) > 0


# ------------------------------------------------------------------------------
# 10. Phase 3.3.1 — Scoped Ambiguity & Fail-Closed Resolution
# ------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "ambiguous_query",
    [
        "SELECT id FROM analytics.customers c JOIN analytics.orders o ON c.id = o.customer_id;",
        "SELECT created_at FROM analytics.customers c JOIN analytics.products p ON c.id = p.id;",
        "SELECT c.first_name, id FROM analytics.customers c JOIN analytics.orders o ON c.id = o.customer_id;",
        "SELECT id, total_amount FROM analytics.customers c JOIN analytics.orders o ON c.id = o.customer_id;",
        "SELECT oi.quantity, id FROM analytics.products p JOIN analytics.order_items oi ON p.id = oi.product_id;",
    ],
)
def test_unqualified_ambiguous_column_rejected(validator: SQLSecurityValidator, ambiguous_query: str):
    """Unqualified column present in multiple joined tables fails closed as ambiguous."""
    result = validator.validate_and_sanitize(ambiguous_query)
    assert result.is_valid is False
    assert "is ambiguous across multiple referenced tables" in result.error
    assert "Must be table-qualified" in result.error


def test_unqualified_unique_columns_permitted(validator: SQLSecurityValidator):
    """Unqualified columns that exist in exactly one referenced table are permitted."""
    query = "SELECT first_name, total_amount FROM analytics.customers c JOIN analytics.orders o ON c.id = o.customer_id;"
    result = validator.validate_and_sanitize(query)
    assert result.is_valid is True
    assert result.error is None


def test_subquery_scoped_unqualified_column_permitted(validator: SQLSecurityValidator):
    """Unqualified column in subquery whose local FROM has only one table is permitted."""
    query = "SELECT c.id FROM analytics.customers c WHERE c.id IN (SELECT id FROM analytics.orders);"
    result = validator.validate_and_sanitize(query)
    assert result.is_valid is True
    assert result.error is None


# ------------------------------------------------------------------------------
# 11. Phase 3.3.1 — Resolver Discretion (Aliases, Literals, Comments)
# ------------------------------------------------------------------------------

def test_resolver_distinguishes_column_from_aliases(validator: SQLSecurityValidator):
    """Aliases with prohibited words are not confused with actual column references."""
    query = "SELECT c.id AS email_count, c.first_name AS user_email_owner FROM analytics.customers c;"
    result = validator.validate_and_sanitize(query)
    assert result.is_valid is True
    assert result.error is None


def test_resolver_distinguishes_column_from_string_literals(validator: SQLSecurityValidator):
    """String literals containing emails are not treated as prohibited column access."""
    query = "SELECT c.id, 'support@example.com' AS contact_support FROM analytics.customers c;"
    result = validator.validate_and_sanitize(query)
    assert result.is_valid is True
    assert result.error is None


def test_resolver_distinguishes_column_from_comments(validator: SQLSecurityValidator):
    """SQL comments mentioning prohibited column names do not trigger rejection."""
    query = "SELECT c.id, c.first_name FROM analytics.customers c; -- check email column later"
    # Note: semicolon splits query into two statements, so test comment without trailing semicolon
    query_clean = "SELECT c.id, c.first_name FROM analytics.customers c /* check email column */"
    result = validator.validate_and_sanitize(query_clean)
    assert result.is_valid is True
    assert result.error is None


# ------------------------------------------------------------------------------
# 12. Phase 3.3.1 — Service Execution Boundaries (Mock/Spy Verification)
# ------------------------------------------------------------------------------

def test_service_unsupported_decision_never_calls_database():
    """Unsupported LLM decisions must not trigger any database connection."""
    with patch("src.llm.service.execute_readonly_query") as mock_exec:
        mock_provider = MockLLMProvider(
            custom_handler=lambda q: GeneratedSQLResponse(
                status=QueryStatus.UNSUPPORTED,
                explanation="Out of domain query.",
            )
        )
        service = NaturalLanguageSQLService(provider=mock_provider)
        res = service.execute_question("What is employee salary?")

        mock_exec.assert_not_called()
        assert res.status == QueryStatus.UNSUPPORTED
        assert res.sql is None
        assert res.rows == []


def test_service_clarification_decision_never_calls_database():
    """Clarification LLM decisions must not trigger any database connection."""
    with patch("src.llm.service.execute_readonly_query") as mock_exec:
        mock_provider = MockLLMProvider(
            custom_handler=lambda q: GeneratedSQLResponse(
                status=QueryStatus.CLARIFICATION_NEEDED,
                explanation="Ambiguous query.",
                clarification_question="Which metric?",
            )
        )
        service = NaturalLanguageSQLService(provider=mock_provider)
        res = service.execute_question("Top")

        mock_exec.assert_not_called()
        assert res.status == QueryStatus.CLARIFICATION_NEEDED
        assert res.sql is None
        assert res.rows == []


def test_service_empty_and_oversized_questions_never_call_database():
    """Empty and oversized user questions are rejected before LLM or DB execution."""
    with patch("src.llm.service.execute_readonly_query") as mock_exec:
        service = NaturalLanguageSQLService()
        
        # Empty
        res_empty = service.execute_question("   ")
        mock_exec.assert_not_called()
        assert res_empty.status == QueryStatus.UNSUPPORTED
        
        # Oversized
        res_long = service.execute_question("x" * 600)
        mock_exec.assert_not_called()
        assert res_long.status == QueryStatus.UNSUPPORTED


# ------------------------------------------------------------------------------
# 13. Phase 3.3.1 — PostgreSQL Schema vs Policy Grant Consistency
# ------------------------------------------------------------------------------

def test_schema_policy_grant_consistency(analytics_db_engine: Engine):
    """PostgreSQL granted columns for analytics_user exactly match APPROVED_TABLE_COLUMNS."""
    from src.security.policy import APPROVED_TABLE_COLUMNS, PROHIBITED_TABLE_COLUMNS

    with analytics_db_engine.connect() as conn:
        res = conn.execute(text("""
            SELECT column_name
            FROM information_schema.column_privileges
            WHERE table_schema = 'analytics'
              AND table_name = 'customers'
              AND grantee = 'analytics_user'
              AND privilege_type = 'SELECT';
        """)).fetchall()
        db_granted_cols = {row[0] for row in res}

    # DB granted columns must match approved columns exactly
    assert db_granted_cols == APPROVED_TABLE_COLUMNS["customers"]

    # DB granted columns must have 0 overlap with prohibited columns
    assert len(db_granted_cols.intersection(PROHIBITED_TABLE_COLUMNS["customers"])) == 0

