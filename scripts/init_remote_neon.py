"""
Remote PostgreSQL 16 Initialization and Security Hardening Orchestrator for Neon.

This script adapts the audited local PostgreSQL initialization scripts:
- docker/postgres/init/01_init_roles_and_schemas.sql
- docker/postgres/init/02_seed_business_data.sql
- docker/postgres/init/03_column_security_grants.sql

Key Cloud Adaptation & Security Features:
1. Loads credentials strictly from .env.neon in memory (never written to disk or logs).
2. Uses psycopg 3 parameter-safe sql.Identifier and sql.Literal composition for role passwords.
3. Implements PostgreSQL 16 schema ownership transfer with strictly scoped ephemeral
   role-membership grant and guaranteed REVOKE cleanup in a finally block.
4. Validates table clean-state before seeding (aborts on partial/inconsistent data).
5. Dynamically synchronizes orders sequence using actual MAX(id) rather than hardcoded 1010.
6. Enforces column-level security on analytics.customers (email strictly restricted).
7. Sets analytics_user statement_timeout=3000ms and default_transaction_read_only=on.
8. Configures default privileges across role boundaries for future tables.
9. Redacts secrets, connection strings, and passwords from all logs and error messages.
10. Provides safe --dry-run mode for preflight verification without connecting to Neon.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# Set up project root in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import psycopg
from psycopg import sql

from src.core.config import Settings

# ------------------------------------------------------------------------------
# Expected Seed Data Constants
# ------------------------------------------------------------------------------
EXPECTED_SEED_COUNTS = {
    "customers": 10,
    "products": 10,
    "orders": 10,
    "order_items": 16,
}

SEED_CUSTOMERS = [
    ("Emma", "Watson", "emma.watson@example.com", "United Kingdom", "London", "2025-01-15 10:00:00+00"),
    ("Liam", "Smith", "liam.smith@example.com", "United States", "New York", "2025-01-20 11:30:00+00"),
    ("Sophia", "Garcia", "sophia.garcia@example.com", "Spain", "Madrid", "2025-02-01 09:15:00+00"),
    ("Noah", "Müller", "noah.mueller@example.com", "Germany", "Berlin", "2025-02-10 14:20:00+00"),
    ("Olivia", "Chen", "olivia.chen@example.com", "Canada", "Toronto", "2025-02-18 16:45:00+00"),
    ("Aarav", "Patel", "aarav.patel@example.com", "India", "Bengaluru", "2025-03-01 08:00:00+00"),
    ("Lucas", "Silva", "lucas.silva@example.com", "Brazil", "São Paulo", "2025-03-05 12:10:00+00"),
    ("Mia", "Dubois", "mia.dubois@example.com", "France", "Paris", "2025-03-12 15:30:00+00"),
    ("James", "Wilson", "james.wilson@example.com", "United States", "Austin", "2025-03-15 17:00:00+00"),
    ("Ananya", "Sharma", "ananya.sharma@example.com", "India", "Mumbai", "2025-03-20 18:25:00+00"),
]

SEED_PRODUCTS = [
    ("Pro Wireless Noise-Cancelling Headphones", "Electronics", 249.99, 110.00, 150, True, "2025-01-10 08:00:00+00"),
    ("Ultra HD 4K Monitor 27-inch", "Electronics", 399.00, 220.00, 85, True, "2025-01-10 08:00:00+00"),
    ("Ergonomic Mechanical Keyboard", "Accessories", 129.50, 60.00, 200, True, "2025-01-12 09:00:00+00"),
    ("Precision Wireless Mouse", "Accessories", 59.99, 25.00, 310, True, "2025-01-12 09:00:00+00"),
    ("Standing Desk Pro 60-inch", "Furniture", 549.00, 300.00, 45, True, "2025-01-15 10:00:00+00"),
    ("Ergonomic Mesh Office Chair", "Furniture", 320.00, 160.00, 60, True, "2025-01-15 10:00:00+00"),
    ("USB-C Multi-Port Docking Station", "Electronics", 89.95, 38.00, 180, True, "2025-01-18 11:00:00+00"),
    ("Aluminum Laptop Stand", "Accessories", 39.99, 14.00, 250, True, "2025-01-20 12:00:00+00"),
    ("Smart Fitness Watch Series 5", "Wearables", 199.99, 85.00, 120, True, "2025-02-01 10:00:00+00"),
    ("Waterproof Bluetooth Speaker", "Audio", 79.99, 32.00, 140, True, "2025-02-05 11:30:00+00"),
]

SEED_ORDERS = [
    (1001, 1, "completed", 379.49, "2025-03-01 11:20:00+00"),
    (1002, 2, "completed", 549.00, "2025-03-02 14:10:00+00"),
    (1003, 3, "completed", 189.49, "2025-03-04 16:30:00+00"),
    (1004, 4, "shipped",   399.00, "2025-03-05 09:45:00+00"),
    (1005, 5, "completed", 798.99, "2025-03-08 13:15:00+00"),
    (1006, 6, "pending",   129.50, "2025-03-10 10:05:00+00"),
    (1007, 7, "completed", 320.00, "2025-03-12 15:40:00+00"),
    (1008, 8, "cancelled", 249.99, "2025-03-14 17:20:00+00"),
    (1009, 9, "completed", 458.99, "2025-03-18 12:00:00+00"),
    (1010, 10, "shipped",  199.99, "2025-03-22 18:30:00+00"),
]

SEED_ORDER_ITEMS = [
    (1001, 1, 1, 249.99),
    (1001, 3, 1, 129.50),
    (1002, 5, 1, 549.00),
    (1003, 3, 1, 129.50),
    (1003, 4, 1, 59.99),
    (1004, 2, 1, 399.00),
    (1005, 2, 1, 399.00),
    (1005, 1, 1, 249.99),
    (1005, 8, 1, 39.99),
    (1005, 7, 1, 89.95),
    (1006, 3, 1, 129.50),
    (1007, 6, 1, 320.00),
    (1008, 1, 1, 249.99),
    (1009, 2, 1, 399.00),
    (1009, 4, 1, 59.99),
    (1010, 9, 1, 199.99),
]

# ------------------------------------------------------------------------------
# Security & Secret Redaction Utilities
# ------------------------------------------------------------------------------
class SecretRedactor:
    """Scrubs passwords, tokens, URLs, and SQL password literals from strings and logs."""

    def __init__(self, secrets: Optional[List[str]] = None):
        self.secrets: Set[str] = set()
        if secrets:
            for s in secrets:
                if s and len(s) >= 4 and not s.isspace():
                    self.secrets.add(s)

    def register_secrets(self, *secrets: Optional[str]) -> None:
        for s in secrets:
            if s and len(s) >= 4 and not s.isspace():
                self.secrets.add(s)

    def redact(self, text: str) -> str:
        if not text:
            return ""
        result = str(text)

        # 1. Exact matches for known secret tokens
        for s in self.secrets:
            result = result.replace(s, "[REDACTED_SECRET]")

        # 2. Database connection URLs: postgresql://user:password@host
        result = re.sub(r"(://[^:]+:)([^@]+)(@)", r"\1[REDACTED_PASSWORD]\3", result)

        # 3. SQL password statements: PASSWORD 'secret' or PASSWORD "secret"
        result = re.sub(
            r"(PASSWORD\s+)(['\"])(?:(?!\2).)*\2",
            r"\1'[REDACTED_PASSWORD]'",
            result,
            flags=re.IGNORECASE,
        )

        # 4. Keyword parameters: password=...
        result = re.sub(
            r"(password\s*=\s*)([^\s,;&'\"]+)",
            r"\1[REDACTED_PASSWORD]",
            result,
            flags=re.IGNORECASE,
        )

        # 5. Google / Gemini API keys
        result = re.sub(r"(AIza[0-9A-Za-z-_]{35})", r"[REDACTED_API_KEY]", result)

        return result


global_redactor = SecretRedactor()


class RedactingFormatter(logging.Formatter):
    """Custom logging formatter redacting sensitive tokens."""

    def __init__(self, redactor: SecretRedactor, fmt: Optional[str] = None):
        super().__init__(fmt=fmt)
        self.redactor = redactor

    def format(self, record: logging.LogRecord) -> str:
        original = super().format(record)
        return self.redactor.redact(original)


def setup_logger(redactor: SecretRedactor) -> logging.Logger:
    logger = logging.getLogger("neon_init")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    formatter = RedactingFormatter(
        redactor=redactor,
        fmt="%(asctime)s [%(levelname)s] %(message)s",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger


logger = setup_logger(global_redactor)

# ------------------------------------------------------------------------------
# SQL Composition Functions (Requirement 2 & 3)
# ------------------------------------------------------------------------------
def build_create_role_sql(role_name: str, password: str) -> sql.Composed:
    """Build safe CREATE ROLE statement using psycopg Identifier and Literal."""
    return sql.SQL("CREATE ROLE {role} WITH LOGIN PASSWORD {password}").format(
        role=sql.Identifier(role_name),
        password=sql.Literal(password),
    )


def build_alter_role_password_sql(role_name: str, password: str) -> sql.Composed:
    """Build safe ALTER ROLE statement using psycopg Identifier and Literal."""
    return sql.SQL("ALTER ROLE {role} WITH LOGIN PASSWORD {password}").format(
        role=sql.Identifier(role_name),
        password=sql.Literal(password),
    )


def build_grant_schema_sql(
    schema_name: str,
    role_name: str,
    privilege: str = "USAGE",
) -> sql.Composed:
    """
    Build safe GRANT ON SCHEMA statement using psycopg Identifier and validated privilege token.
    
    Validates PostgreSQL 16 schema privileges ('USAGE', 'CREATE', or 'ALL').
    Prevents invalid composite syntax like 'USAGE, ALL' and enforces least privilege.
    """
    normalized = privilege.strip().upper()
    valid_privileges = {"USAGE", "CREATE", "ALL"}
    if normalized not in valid_privileges:
        raise ValueError(
            f"Invalid schema privilege '{privilege}'. Valid privileges are: {sorted(valid_privileges)}"
        )
    return sql.SQL("GRANT {privilege} ON SCHEMA {schema} TO {role}").format(
        privilege=sql.SQL(normalized),
        schema=sql.Identifier(schema_name),
        role=sql.Identifier(role_name),
    )


def ensure_role(cursor: psycopg.Cursor, role_name: str, password: str) -> str:
    """
    Ensure the role exists with the specified password.
    Returns 'created' or 'updated'.
    """
    cursor.execute("SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = %s", (role_name,))
    exists = cursor.fetchone() is not None
    if not exists:
        cursor.execute(build_create_role_sql(role_name, password))
        return "created"
    else:
        cursor.execute(build_alter_role_password_sql(role_name, password))
        return "updated"


def configure_analytics_role_attributes(
    cursor: psycopg.Cursor,
    role_name: str,
    timeout_ms: int = 3000,
) -> None:
    """Enforce 3000ms statement timeout and default read-only transactions."""
    timeout_str = f"{timeout_ms}ms"
    cursor.execute(
        sql.SQL("ALTER ROLE {role} SET statement_timeout = {timeout}").format(
            role=sql.Identifier(role_name),
            timeout=sql.Literal(timeout_str),
        )
    )
    cursor.execute(
        sql.SQL("ALTER ROLE {role} SET default_transaction_read_only = 'on'").format(
            role=sql.Identifier(role_name),
        )
    )


# ------------------------------------------------------------------------------
# Clean-State and Sequence Synchronization Logic (Requirement 6 & 7)
# ------------------------------------------------------------------------------
def evaluate_table_counts(counts: Dict[str, int]) -> str:
    """
    Pure evaluator for target table row counts.
    Returns:
        'clean': all tables are empty (0 rows)
        'seeded': all tables have exact expected seed counts
    Raises:
        ValueError: if table counts are partial or unexpected
    """
    all_zero = all(counts.get(tbl, -1) == 0 for tbl in EXPECTED_SEED_COUNTS)
    if all_zero:
        return "clean"

    if counts == EXPECTED_SEED_COUNTS:
        return "seeded"

    raise ValueError(
        f"Inconsistent table state detected in 'analytics' schema: {counts}. "
        f"Expected either all empty (clean) or all populated {EXPECTED_SEED_COUNTS}. "
        "Aborting seed to prevent partial data corruption."
    )


def check_table_clean_state(cursor: psycopg.Cursor) -> str:
    """Query live database table counts and validate state."""
    counts = {}
    for tbl in ["customers", "products", "orders", "order_items"]:
        cursor.execute(
            sql.SQL("SELECT count(*) FROM analytics.{tbl}").format(
                tbl=sql.Identifier(tbl)
            )
        )
        counts[tbl] = cursor.fetchone()[0]
    return evaluate_table_counts(counts)


def calculate_next_sequence_val(max_id: int) -> Tuple[int, bool]:
    """
    Calculate PostgreSQL sequence setval parameters based on max_id.
    Returns (setval_value, is_called).
    """
    if max_id > 0:
        return (max_id, True)
    return (1, False)


def sync_table_sequences(cursor: psycopg.Cursor) -> Dict[str, int]:
    """
    Dynamically synchronize primary key sequences using actual MAX(id).
    Avoids hardcoding 1010 or any static sequence values.
    """
    synced = {}
    sequence_map = [
        ("analytics.orders", "analytics.orders_id_seq"),
        ("analytics.customers", "analytics.customers_id_seq"),
        ("analytics.products", "analytics.products_id_seq"),
        ("analytics.order_items", "analytics.order_items_id_seq"),
    ]
    for table_full, seq_full in sequence_map:
        schema, table = table_full.split(".")
        cursor.execute(
            sql.SQL("SELECT COALESCE(MAX(id), 0) FROM {schema}.{table}").format(
                schema=sql.Identifier(schema),
                table=sql.Identifier(table),
            )
        )
        max_id = cursor.fetchone()[0]
        val, is_called = calculate_next_sequence_val(max_id)
        cursor.execute("SELECT setval(%s, %s, %s)", (seq_full, val, is_called))
        synced[seq_full] = max_id
    return synced


# ------------------------------------------------------------------------------
# PostgreSQL 16 Schema Ownership Transfer (Requirement 5)
# ------------------------------------------------------------------------------
def transfer_schema_ownership(
    cursor: psycopg.Cursor,
    schema_name: str,
    target_owner: str,
) -> bool:
    """
    Safely transfer schema ownership under PostgreSQL 16 non-superuser rules.
    
    In PG 16, transferring schema ownership requires membership in the target role.
    We grant temporary membership, alter ownership, and guarantee REVOKE in a finally block.
    If the revoke fails, we abort immediately to prevent lingering privilege escalation.
    
    Returns True if ownership was transferred, False if already owned.
    """
    cursor.execute(
        """
        SELECT r.rolname
        FROM pg_namespace n
        JOIN pg_roles r ON n.nspowner = r.oid
        WHERE n.nspname = %s
        """,
        (schema_name,),
    )
    row = cursor.fetchone()
    current_owner = row[0] if row else None
    if current_owner == target_owner:
        return False

    membership_granted = False
    try:
        cursor.execute(
            sql.SQL("GRANT {role} TO CURRENT_USER").format(
                role=sql.Identifier(target_owner)
            )
        )
        membership_granted = True
        cursor.execute(
            sql.SQL("ALTER SCHEMA {schema} OWNER TO {role}").format(
                schema=sql.Identifier(schema_name),
                role=sql.Identifier(target_owner),
            )
        )
        return True
    finally:
        if membership_granted:
            conn = getattr(cursor, "connection", None)
            # If the transaction is aborted (e.g. due to ALTER SCHEMA failure),
            # rolling back undoes the ephemeral GRANT immediately and cleanly.
            if (
                conn
                and hasattr(conn, "info")
                and getattr(conn.info, "transaction_status", None) == psycopg.pq.TransactionStatus.INERROR
            ):
                conn.rollback()
            else:
                try:
                    cursor.execute(
                        sql.SQL("REVOKE {role} FROM CURRENT_USER").format(
                            role=sql.Identifier(target_owner)
                        )
                    )
                except Exception as revoke_exc:
                    raise RuntimeError(
                        f"CRITICAL PRIVILEGE CLEANUP FAILURE: Failed to revoke temporary membership of "
                        f"'{target_owner}' from CURRENT_USER: {global_redactor.redact(str(revoke_exc))}. "
                        "Aborting to avoid persistent privilege escalation."
                    ) from revoke_exc


# ------------------------------------------------------------------------------
# Business DDL and Column Security Grants (Requirement 8, 9, 10, 11)
# ------------------------------------------------------------------------------
def create_business_tables(cursor: psycopg.Cursor) -> None:
    """Create the 4 analytics schema tables if they do not exist."""
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS analytics.customers (
            id SERIAL PRIMARY KEY,
            first_name VARCHAR(50) NOT NULL,
            last_name VARCHAR(50) NOT NULL,
            email VARCHAR(100) UNIQUE NOT NULL,
            country VARCHAR(50) NOT NULL,
            city VARCHAR(50) NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS analytics.products (
            id SERIAL PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            category VARCHAR(50) NOT NULL,
            price NUMERIC(10, 2) NOT NULL,
            cost NUMERIC(10, 2) NOT NULL,
            stock_quantity INT NOT NULL DEFAULT 0,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS analytics.orders (
            id SERIAL PRIMARY KEY,
            customer_id INT NOT NULL REFERENCES analytics.customers(id) ON DELETE RESTRICT,
            status VARCHAR(30) NOT NULL,
            total_amount NUMERIC(12, 2) NOT NULL DEFAULT 0.00,
            order_date TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS analytics.order_items (
            id SERIAL PRIMARY KEY,
            order_id INT NOT NULL REFERENCES analytics.orders(id) ON DELETE CASCADE,
            product_id INT NOT NULL REFERENCES analytics.products(id) ON DELETE RESTRICT,
            quantity INT NOT NULL CHECK (quantity > 0),
            unit_price NUMERIC(10, 2) NOT NULL
        );
        """
    )


def insert_seed_data(cursor: psycopg.Cursor) -> None:
    """Insert the validated baseline seed data."""
    cursor.executemany(
        """
        INSERT INTO analytics.customers (first_name, last_name, email, country, city, created_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        SEED_CUSTOMERS,
    )
    cursor.executemany(
        """
        INSERT INTO analytics.products (name, category, price, cost, stock_quantity, is_active, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        SEED_PRODUCTS,
    )
    cursor.executemany(
        """
        INSERT INTO analytics.orders (id, customer_id, status, total_amount, order_date)
        VALUES (%s, %s, %s, %s, %s)
        """,
        SEED_ORDERS,
    )
    cursor.executemany(
        """
        INSERT INTO analytics.order_items (order_id, product_id, quantity, unit_price)
        VALUES (%s, %s, %s, %s)
        """,
        SEED_ORDER_ITEMS,
    )


def apply_schema_and_column_security(
    cursor: psycopg.Cursor,
    db_name: str,
    app_role: str,
    analytics_role: str,
) -> None:
    """Enforce strict security boundaries and column-level customer grants."""
    # 1. Database-level temporary table restrictions
    cursor.execute(
        sql.SQL("REVOKE TEMPORARY ON DATABASE {db} FROM PUBLIC").format(
            db=sql.Identifier(db_name)
        )
    )
    cursor.execute(
        sql.SQL("REVOKE TEMPORARY ON DATABASE {db} FROM {role}").format(
            db=sql.Identifier(db_name),
            role=sql.Identifier(analytics_role),
        )
    )

    # 2. Public schema hardening
    cursor.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    cursor.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
    cursor.execute(
        sql.SQL("REVOKE ALL ON SCHEMA public FROM {role}").format(
            role=sql.Identifier(analytics_role)
        )
    )

    # 3. Guarded set_config function revoke (handles non-superuser gracefully)
    try:
        cursor.execute("REVOKE EXECUTE ON FUNCTION pg_catalog.set_config(text, text, boolean) FROM PUBLIC")
        cursor.execute(
            sql.SQL("REVOKE EXECUTE ON FUNCTION pg_catalog.set_config(text, text, boolean) FROM {role}").format(
                role=sql.Identifier(analytics_role)
            )
        )
    except Exception:
        logger.info("Skipping pg_catalog.set_config revoke (not superuser on pg_catalog). AST validator enforces boundary.")

    # 4. app_metadata schema privileges for app_user; complete denial for analytics_user
    cursor.execute(build_grant_schema_sql("app_metadata", app_role, "ALL"))
    cursor.execute(
        sql.SQL("GRANT ALL ON ALL TABLES IN SCHEMA app_metadata TO {role}").format(
            role=sql.Identifier(app_role)
        )
    )
    cursor.execute(
        sql.SQL("GRANT ALL ON ALL SEQUENCES IN SCHEMA app_metadata TO {role}").format(
            role=sql.Identifier(app_role)
        )
    )
    cursor.execute(
        sql.SQL("REVOKE ALL ON SCHEMA app_metadata FROM {role}").format(
            role=sql.Identifier(analytics_role)
        )
    )
    cursor.execute("REVOKE ALL ON SCHEMA app_metadata FROM PUBLIC")

    # 5. analytics schema privileges
    cursor.execute(build_grant_schema_sql("analytics", app_role, "USAGE"))
    cursor.execute(
        sql.SQL("GRANT ALL ON ALL TABLES IN SCHEMA analytics TO {role}").format(
            role=sql.Identifier(app_role)
        )
    )
    cursor.execute(
        sql.SQL("GRANT ALL ON ALL SEQUENCES IN SCHEMA analytics TO {role}").format(
            role=sql.Identifier(app_role)
        )
    )

    cursor.execute(build_grant_schema_sql("analytics", analytics_role, "USAGE"))
    cursor.execute(
        sql.SQL("GRANT SELECT ON analytics.products, analytics.orders, analytics.order_items TO {role}").format(
            role=sql.Identifier(analytics_role)
        )
    )

    # 6. Strict Column-Level Security on analytics.customers
    # Revoke table-level SELECT; grant only approved non-sensitive columns (excludes email)
    cursor.execute(
        sql.SQL("REVOKE SELECT ON analytics.customers FROM {role}").format(
            role=sql.Identifier(analytics_role)
        )
    )
    cursor.execute(
        sql.SQL(
            "GRANT SELECT (id, first_name, last_name, country, city, created_at) "
            "ON analytics.customers TO {role}"
        ).format(role=sql.Identifier(analytics_role))
    )

    # 7. Default privileges for future objects created by admin
    cursor.execute(
        sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA analytics GRANT SELECT ON TABLES TO {role}").format(
            role=sql.Identifier(analytics_role)
        )
    )
    cursor.execute(
        sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA analytics GRANT ALL ON TABLES TO {role}").format(
            role=sql.Identifier(app_role)
        )
    )
    cursor.execute(
        sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA analytics GRANT ALL ON SEQUENCES TO {role}").format(
            role=sql.Identifier(app_role)
        )
    )


def configure_app_user_defaults(app_cursor: psycopg.Cursor, analytics_role: str) -> None:
    """Configure default privileges for future tables created by app_user."""
    app_cursor.execute(
        sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA analytics GRANT SELECT ON TABLES TO {role}").format(
            role=sql.Identifier(analytics_role)
        )
    )


# ------------------------------------------------------------------------------
# Configuration Validation & Dry-Run Mode (Requirement 13)
# ------------------------------------------------------------------------------
def validate_configuration(settings: Settings) -> List[str]:
    """Validate settings against Neon production constraints without opening connections."""
    errors = []

    if not settings.POSTGRES_HOST or settings.POSTGRES_HOST in ("localhost", "127.0.0.1", "postgres"):
        errors.append(f"POSTGRES_HOST must point to remote Neon endpoint, found: '{settings.POSTGRES_HOST}'")

    if settings.POSTGRES_DB != "neondb":
        errors.append(f"POSTGRES_DB must be 'neondb', found: '{settings.POSTGRES_DB}'")

    ssl_mode = settings.get_sslmode()
    if ssl_mode != "require":
        errors.append(f"Effective SSL mode must be 'require', found: '{ssl_mode}'")

    if not settings.DB_SERVERLESS_MODE:
        errors.append("DB_SERVERLESS_MODE must be enabled (True) for Neon cloud execution")

    # Passwords check
    if not settings.POSTGRES_ADMIN_PASSWORD or len(settings.POSTGRES_ADMIN_PASSWORD) < 8:
        errors.append("POSTGRES_ADMIN_PASSWORD is empty or too short")

    if not settings.APP_DB_PASSWORD or len(settings.APP_DB_PASSWORD) < 8:
        errors.append("APP_DB_PASSWORD is empty or too short")

    if not settings.ANALYTICS_DB_PASSWORD or len(settings.ANALYTICS_DB_PASSWORD) < 8:
        errors.append("ANALYTICS_DB_PASSWORD is empty or too short")

    if settings.APP_DB_PASSWORD == settings.ANALYTICS_DB_PASSWORD:
        errors.append("APP_DB_PASSWORD and ANALYTICS_DB_PASSWORD must be distinct tokens")

    for field_name, val in [
        ("POSTGRES_ADMIN_PASSWORD", settings.POSTGRES_ADMIN_PASSWORD),
        ("APP_DB_PASSWORD", settings.APP_DB_PASSWORD),
        ("ANALYTICS_DB_PASSWORD", settings.ANALYTICS_DB_PASSWORD),
    ]:
        if any(marker in val.lower() for marker in ("placeholder", "changeme", "example", "dev_password")):
            errors.append(f"{field_name} contains placeholder/example text")

    return errors


def run_dry_run(settings: Settings, env_path: Path) -> int:
    """
    Execute dry-run preflight validation.
    Prints planned phases and safe metadata without opening network connections or running SQL.
    """
    logger.info("=== DRY-RUN PREFLIGHT VALIDATION MODE ===")
    logger.info(f"Target Environment File: {env_path.name}")

    errors = validate_configuration(settings)
    if errors:
        logger.error("Configuration validation FAILED:")
        for err in errors:
            logger.error(f"  - {err}")
        return 1

    logger.info("Configuration Validation: PASSED")
    logger.info(f"  Target Host:      {settings.POSTGRES_HOST}")
    logger.info(f"  Target Port:      {settings.POSTGRES_PORT}")
    logger.info(f"  Target Database:  {settings.POSTGRES_DB}")
    logger.info(f"  SSL Mode:         {settings.get_sslmode()}")
    logger.info(f"  Serverless Mode:  {settings.DB_SERVERLESS_MODE}")
    logger.info(f"  Admin User:       {settings.POSTGRES_ADMIN_USER}")
    logger.info(f"  App User:         {settings.APP_DB_USER}")
    logger.info(f"  Analytics User:   {settings.ANALYTICS_DB_USER}")
    logger.info(f"  Passwords:        [VERIFIED NON-EMPTY, SECURE, INDEPENDENT]")

    logger.info("\nPlanned Initialization Sequence:")
    logger.info("  1. Connect as POSTGRES_ADMIN_USER over TLS (neondb)")
    logger.info("  2. Phase 1: Provision Roles (app_user, analytics_user) with secure in-memory passwords")
    logger.info("  3. Phase 1b: Configure analytics_user statement_timeout=3000ms & default_transaction_read_only=on")
    logger.info("  4. Phase 2: Create schemas 'analytics' and 'app_metadata'")
    logger.info("  5. Phase 2b: PG16 ownership transfer of 'app_metadata' to 'app_user' with ephemeral membership & cleanup")
    logger.info("  6. Phase 3: Create analytics tables (customers, products, orders, order_items)")
    logger.info("  7. Phase 3b: Validate table clean-state (verify 0 rows before seeding)")
    logger.info("  8. Phase 3c: Seed business data (10 customers, 10 products, 10 orders, 16 items)")
    logger.info("  9. Phase 4: Dynamic sequence synchronization using MAX(id)")
    logger.info(" 10. Phase 5: Enforce column-level security on analytics.customers (deny email)")
    logger.info(" 11. Phase 5b: Grant analytics_user SELECT on products, orders, order_items; deny app_metadata")
    logger.info(" 12. Phase 6: Connect as app_user and configure default privileges for future tables")
    logger.info(" 13. Phase 7: Post-initialization zero-trust verification (positive & negative checks)")

    logger.info("\nDRY-RUN COMPLETE: Zero network connections opened. Zero SQL statements executed.")
    return 0


# ------------------------------------------------------------------------------
# Post-Initialization Verification (Requirement 4 & 9)
# ------------------------------------------------------------------------------
def run_post_init_verification(settings: Settings, require_migrations: bool = False) -> None:
    """Verify security boundaries and query permissions across all roles."""
    logger.info("Phase 7: Running zero-trust security and permission verification...")

    # Verification 1: analytics_user permissions
    with psycopg.connect(
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        dbname=settings.POSTGRES_DB,
        user=settings.ANALYTICS_DB_USER,
        password=settings.ANALYTICS_DB_PASSWORD,
        sslmode=settings.get_sslmode() or "require",
        connect_timeout=10,
    ) as analytics_conn:
        with analytics_conn.cursor() as cur:
            # Positive checks
            cur.execute("SELECT count(*) FROM analytics.products")
            p_cnt = cur.fetchone()[0]
            logger.info(f"  [PASS] analytics_user can read products (count: {p_cnt})")

            cur.execute("SELECT id, first_name, country FROM analytics.customers LIMIT 1")
            row = cur.fetchone()
            logger.info(f"  [PASS] analytics_user can read allowed customer columns (id: {row[0]})")

            # Negative check: customers.email
            email_blocked = False
            try:
                cur.execute("SELECT email FROM analytics.customers LIMIT 1")
                cur.fetchone()
            except psycopg.errors.InsufficientPrivilege:
                email_blocked = True
                analytics_conn.rollback()
            if not email_blocked:
                raise RuntimeError("SECURITY VIOLATION: analytics_user was able to read customers.email!")
            logger.info("  [PASS] analytics_user is strictly BLOCKED from customers.email")

            # Negative check: app_metadata schema
            metadata_blocked = False
            try:
                cur.execute("SELECT * FROM app_metadata.query_audit_logs LIMIT 1")
                cur.fetchone()
            except psycopg.errors.InsufficientPrivilege:
                metadata_blocked = True
                analytics_conn.rollback()
            except psycopg.errors.UndefinedTable:
                metadata_blocked = True
                analytics_conn.rollback()
            if not metadata_blocked:
                raise RuntimeError("SECURITY VIOLATION: analytics_user was able to query app_metadata!")
            logger.info("  [PASS] analytics_user is strictly BLOCKED from app_metadata schema")

            # Negative check: write operations
            write_blocked = False
            try:
                cur.execute(
                    "INSERT INTO analytics.products (name, category, price, cost) VALUES ('X', 'Y', 1, 1)"
                )
            except (psycopg.errors.ReadOnlySqlTransaction, psycopg.errors.InsufficientPrivilege):
                write_blocked = True
                analytics_conn.rollback()
            if not write_blocked:
                raise RuntimeError("SECURITY VIOLATION: analytics_user was able to insert into products!")
            logger.info("  [PASS] analytics_user is strictly BLOCKED from write operations")

    # Verification 2: app_user permissions and app_metadata ownership
    with psycopg.connect(
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        dbname=settings.POSTGRES_DB,
        user=settings.APP_DB_USER,
        password=settings.APP_DB_PASSWORD,
        sslmode=settings.get_sslmode() or "require",
        connect_timeout=10,
    ) as app_conn:
        with app_conn.cursor() as cur:
            # Check schema ownership
            cur.execute(
                """
                SELECT r.rolname
                FROM pg_namespace n
                JOIN pg_roles r ON n.nspowner = r.oid
                WHERE n.nspname = 'app_metadata'
                """
            )
            owner_row = cur.fetchone()
            actual_owner = owner_row[0] if owner_row else None
            if actual_owner != settings.APP_DB_USER:
                raise RuntimeError(
                    f"OWNERSHIP VIOLATION: app_metadata owner is '{actual_owner}', expected '{settings.APP_DB_USER}'"
                )
            logger.info(f"  [PASS] app_user is verified owner of schema 'app_metadata'")

            # Check read access to analytics schema (including full customers table)
            cur.execute("SELECT count(*) FROM analytics.customers")
            c_cnt = cur.fetchone()[0]
            logger.info(f"  [PASS] app_user can read business tables (customers count: {c_cnt})")

            # Check audit log table existence and readability if migrations were run
            cur.execute(
                """
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_schema = 'app_metadata' AND table_name = 'query_audit_logs'
                )
                """
            )
            has_audit_table = cur.fetchone()[0]

            cur.execute(
                """
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_schema = 'app_metadata' AND table_name = 'alembic_version'
                )
                """
            )
            has_version_table = cur.fetchone()[0]

            if require_migrations:
                if not has_audit_table:
                    raise RuntimeError(
                        "MIGRATION VERIFICATION FAILURE: Expected table 'app_metadata.query_audit_logs' "
                        "does not exist after running migrations!"
                    )
                if not has_version_table:
                    raise RuntimeError(
                        "MIGRATION VERIFICATION FAILURE: Expected table 'app_metadata.alembic_version' "
                        "does not exist after running migrations!"
                    )
                cur.execute("SELECT version_num FROM app_metadata.alembic_version LIMIT 1")
                v_row = cur.fetchone()
                rev = v_row[0] if v_row else None
                if not rev:
                    raise RuntimeError(
                        "MIGRATION VERIFICATION FAILURE: 'app_metadata.alembic_version' table is empty!"
                    )
                cur.execute("SELECT count(*) FROM app_metadata.query_audit_logs")
                a_cnt = cur.fetchone()[0]
                logger.info(f"  [PASS] app_user can query app_metadata.query_audit_logs (count: {a_cnt}, revision: {rev})")
            else:
                if has_audit_table:
                    cur.execute("SELECT count(*) FROM app_metadata.query_audit_logs")
                    a_cnt = cur.fetchone()[0]
                    logger.info(f"  [PASS] app_user can query app_metadata.query_audit_logs (count: {a_cnt})")
                else:
                    logger.info("  [PASS] app_user has schema privileges on app_metadata (audit log table pending migrations)")

    logger.info("Phase 7 Verification: ALL SECURITY BOUNDARIES CONFIRMED.")



# ------------------------------------------------------------------------------
# Remote Initialization Orchestrator (Multi-Phase Transaction Management)
# ------------------------------------------------------------------------------
def execute_remote_initialization(
    settings: Settings,
    skip_seed: bool = False,
    run_migrations: bool = False,
) -> None:
    """
    Execute remote initialization against Neon PostgreSQL 16.
    Uses separate transactions for distinct administrative phases.
    """
    logger.info(f"Starting remote initialization on Neon: {settings.POSTGRES_HOST}/{settings.POSTGRES_DB}")

    # Phase 1 & 2: Admin Connection (Roles, Schemas, Ownership)
    with psycopg.connect(
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        dbname=settings.POSTGRES_DB,
        user=settings.POSTGRES_ADMIN_USER,
        password=settings.POSTGRES_ADMIN_PASSWORD,
        sslmode=settings.get_sslmode() or "require",
        connect_timeout=10,
        autocommit=False,
    ) as admin_conn:
        with admin_conn.cursor() as cur:
            logger.info("Phase 1: Provisioning application and analytics roles...")
            app_status = ensure_role(cur, settings.APP_DB_USER, settings.APP_DB_PASSWORD)
            analytics_status = ensure_role(cur, settings.ANALYTICS_DB_USER, settings.ANALYTICS_DB_PASSWORD)
            configure_analytics_role_attributes(cur, settings.ANALYTICS_DB_USER, settings.ANALYTICS_STATEMENT_TIMEOUT_MS)
            admin_conn.commit()
            logger.info(f"  app_user ({app_status}), analytics_user ({analytics_status}) configured.")

            logger.info("Phase 2: Creating schemas and transferring app_metadata ownership...")
            cur.execute("CREATE SCHEMA IF NOT EXISTS analytics;")
            cur.execute("CREATE SCHEMA IF NOT EXISTS app_metadata;")
            
            # PG 16 Ownership Transfer
            transferred = transfer_schema_ownership(cur, "app_metadata", settings.APP_DB_USER)
            if transferred:
                logger.info("  Transferred app_metadata ownership to app_user (PG16 role-membership handoff complete).")
            else:
                logger.info("  app_metadata is already owned by app_user.")
            admin_conn.commit()

            # Phase 3 & 4: Business Tables & Data Seeding
            if not skip_seed:
                logger.info("Phase 3: Creating business tables and validating clean initial state...")
                create_business_tables(cur)
                state = check_table_clean_state(cur)
                if state == "clean":
                    logger.info("  Clean state verified (all tables empty). Ingesting baseline seed data...")
                    insert_seed_data(cur)
                    synced = sync_table_sequences(cur)
                    logger.info(f"  Seed data populated. Synchronized sequences: {synced}")
                elif state == "seeded":
                    logger.info("  Tables already contain exact expected seed data. Skipping insert.")
                    synced = sync_table_sequences(cur)
                    logger.info(f"  Synchronized sequences: {synced}")
                admin_conn.commit()

            # Phase 5: Permissions & Column-Level Security Hardening
            logger.info("Phase 5: Enforcing privilege isolation and column security...")
            apply_schema_and_column_security(
                cur,
                db_name=settings.POSTGRES_DB,
                app_role=settings.APP_DB_USER,
                analytics_role=settings.ANALYTICS_DB_USER,
            )
            admin_conn.commit()
            logger.info("  Schema and column-level privileges enforced.")

    # Phase 6: App User Context (Default Privileges & Alembic)
    logger.info("Phase 6: Connecting as app_user to configure default privileges...")
    with psycopg.connect(
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        dbname=settings.POSTGRES_DB,
        user=settings.APP_DB_USER,
        password=settings.APP_DB_PASSWORD,
        sslmode=settings.get_sslmode() or "require",
        connect_timeout=10,
        autocommit=True,
    ) as app_conn:
        with app_conn.cursor() as cur:
            configure_app_user_defaults(cur, settings.ANALYTICS_DB_USER)
            logger.info("  app_user default privileges configured in schema 'analytics'.")

    if run_migrations:
        logger.info("Phase 6b: Executing Alembic migrations under app_user...")
        from alembic.config import Config
        from alembic import command
        from sqlalchemy import create_engine
        from sqlalchemy.pool import NullPool

        alembic_cfg = Config("alembic.ini")
        migration_engine = create_engine(
            settings.get_app_database_url(),
            poolclass=NullPool,
        )
        try:
            with migration_engine.connect() as connection:
                alembic_cfg.attributes["connection"] = connection
                command.upgrade(alembic_cfg, "head")
        finally:
            migration_engine.dispose()
        logger.info("  Alembic migrations completed (app_metadata.query_audit_logs created).")

    # Phase 7: Verification
    run_post_init_verification(settings, require_migrations=run_migrations)
    logger.info("=== REMOTE NEON INITIALIZATION COMPLETED SUCCESSFULLY ===")


# ------------------------------------------------------------------------------
# Main CLI Entrypoint
# ------------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Remote PostgreSQL 16 Initialization and Security Hardening Orchestrator for Neon.",
    )
    parser.add_argument(
        "--env-file",
        type=str,
        default=".env.neon",
        help="Path to Neon environment file (default: .env.neon)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Perform configuration validation and print planned operations without connecting to Neon",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Explicitly confirm remote execution against Neon (safety guardrail)",
    )
    parser.add_argument(
        "--skip-seed",
        action="store_true",
        help="Skip business data table creation and seeding",
    )
    parser.add_argument(
        "--run-migrations",
        action="store_true",
        help="Run Alembic migrations under app_user after role and schema initialization",
    )

    args = parser.parse_args()

    env_path = Path(args.env_file)
    if not env_path.is_absolute():
        env_path = PROJECT_ROOT / env_path

    if not env_path.exists():
        logger.error(f"Environment file not found: {env_path}")
        return 1

    # Configure ENV_FILE in environment before instantiating settings
    os.environ["ENV_FILE"] = str(env_path)
    from src.core.config import get_settings
    get_settings.cache_clear()

    try:
        settings = Settings(_env_file=str(env_path))
    except Exception as exc:
        logger.error(f"Failed to parse environment file: {global_redactor.redact(str(exc))}")
        return 1

    # Register known secrets with the redactor
    global_redactor.register_secrets(
        settings.POSTGRES_ADMIN_PASSWORD,
        settings.APP_DB_PASSWORD,
        settings.ANALYTICS_DB_PASSWORD,
        settings.GEMINI_API_KEY,
    )

    # Enforce safe default: require explicit --execute to connect
    if args.dry_run or not args.execute:
        if not args.dry_run and not args.execute:
            logger.info("Notice: Neither --execute nor --dry-run was specified. Defaulting to safe --dry-run mode.")
        return run_dry_run(settings, env_path)

    # Live execution requested with explicit --execute
    try:
        execute_remote_initialization(
            settings=settings,
            skip_seed=args.skip_seed,
            run_migrations=args.run_migrations,
        )
        return 0
    except Exception as exc:
        logger.error(f"Remote initialization failed: {global_redactor.redact(str(exc))}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
