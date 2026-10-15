"""
Unit and offline tests for scripts/init_remote_neon.py.

Verifies:
1. Parameter-safe SQL composition using psycopg.sql (no raw f-string password interpolation).
2. Comprehensive secret, password, and URL redaction.
3. Clean-state validation and partial-data protection.
4. Dynamic sequence synchronization calculation.
5. PostgreSQL 16 schema ownership transfer with guaranteed ephemeral membership cleanup.
6. Configuration validation and safe dry-run execution.
"""

from pathlib import Path
from unittest.mock import MagicMock, call, patch
import pytest
import psycopg
from psycopg import sql

from scripts.init_remote_neon import (
    EXPECTED_SEED_COUNTS,
    SecretRedactor,
    apply_schema_and_column_security,
    build_alter_role_password_sql,
    build_create_role_sql,
    build_grant_schema_sql,
    calculate_next_sequence_val,
    evaluate_table_counts,
    run_dry_run,
    transfer_schema_ownership,
    validate_configuration,
)
from src.core.config import Settings


# ------------------------------------------------------------------------------
# 1. Parameter-Safe SQL Composition Tests (Requirement 2 & 3)
# ------------------------------------------------------------------------------
def test_build_create_role_sql_structure():
    """Verify build_create_role_sql constructs composed query with Identifier and Literal."""
    query = build_create_role_sql("app_user", "super_secret_pwd_123")
    assert isinstance(query, sql.Composed)

    # Convert to string without database connection using a Null connection / string inspection
    # In psycopg 3, sql.Composed can be inspected via its internal sequence
    parts = list(query)
    # Verify the pieces: 'CREATE ROLE ', Identifier('app_user'), ' WITH LOGIN PASSWORD ', Literal('super_secret_pwd_123')
    assert any(isinstance(p, sql.Identifier) for p in parts)
    assert any(isinstance(p, sql.Literal) for p in parts)


def test_build_alter_role_password_sql_structure():
    """Verify build_alter_role_password_sql constructs composed query with Identifier and Literal."""
    query = build_alter_role_password_sql("analytics_user", "another_secret_456")
    assert isinstance(query, sql.Composed)
    parts = list(query)
    assert any(isinstance(p, sql.Identifier) for p in parts)
    assert any(isinstance(p, sql.Literal) for p in parts)


def test_sql_composition_injection_safety():
    """Verify SQL composition prevents quote breakout when special characters exist in names."""
    # Even if an adversarial role name or password is used, Identifier and Literal quote properly
    query = build_create_role_sql('evil_user"; DROP TABLE test;--', "pass' OR '1'='1")
    parts = list(query)
    identifier = next(p for p in parts if isinstance(p, sql.Identifier))
    literal = next(p for p in parts if isinstance(p, sql.Literal))
    assert identifier._obj == ('evil_user"; DROP TABLE test;--',)
    assert literal._obj == "pass' OR '1'='1"
    rendered = query.as_string(None)
    assert '""' in rendered
    assert "''" in rendered


# ------------------------------------------------------------------------------
# 2. Secret Redaction Tests (Requirement 14)
# ------------------------------------------------------------------------------
def test_secret_redactor_exact_tokens():
    """Verify exact secret tokens are scrubbed from arbitrary text."""
    redactor = SecretRedactor(["secret_pass_1", "super_token_999"])
    raw_message = "Connecting with password secret_pass_1 and token super_token_999."
    scrubbed = redactor.redact(raw_message)
    assert "secret_pass_1" not in scrubbed
    assert "super_token_999" not in scrubbed
    assert "[REDACTED_SECRET]" in scrubbed


def test_secret_redactor_connection_urls():
    """Verify database connection URLs have passwords masked."""
    redactor = SecretRedactor()
    url = "postgresql+psycopg://neondb_owner:npg_SecretPass999@ep-xyz.aws.neon.tech:5432/neondb"
    scrubbed = redactor.redact(url)
    assert "npg_SecretPass999" not in scrubbed
    assert "neondb_owner:[REDACTED_PASSWORD]@ep-xyz" in scrubbed


def test_secret_redactor_sql_password_literals():
    """Verify SQL PASSWORD literals are masked."""
    redactor = SecretRedactor()
    stmt = "CREATE ROLE app_user WITH LOGIN PASSWORD 'unregistered_secret_token_123';"
    scrubbed = redactor.redact(stmt)
    assert "unregistered_secret_token_123" not in scrubbed
    assert "PASSWORD '[REDACTED_PASSWORD]'" in scrubbed


def test_secret_redactor_keyword_parameters():
    """Verify password= parameter syntax is masked."""
    redactor = SecretRedactor()
    log_line = "Error connecting host=localhost port=5432 password=leaked_param dbname=neondb"
    scrubbed = redactor.redact(log_line)
    assert "leaked_param" not in scrubbed
    assert "password=[REDACTED_PASSWORD]" in scrubbed


def test_secret_redactor_gemini_api_key():
    """Verify Gemini API keys are masked."""
    redactor = SecretRedactor()
    raw = "Client created with key AIzaSyA1B2C3D4E5F6G7H8I9J0K1L2M3N4O5P6Q"
    scrubbed = redactor.redact(raw)
    assert "AIzaSyA1B2C3D4E5F6G7H8I9J0K1L2M3N4O5P6Q" not in scrubbed
    assert "[REDACTED_API_KEY]" in scrubbed


# ------------------------------------------------------------------------------
# 3. Clean-State Validation Tests (Requirement 6)
# ------------------------------------------------------------------------------
def test_evaluate_table_counts_clean():
    """Verify empty tables evaluate to 'clean' state."""
    counts = {"customers": 0, "products": 0, "orders": 0, "order_items": 0}
    assert evaluate_table_counts(counts) == "clean"


def test_evaluate_table_counts_seeded():
    """Verify exact expected counts evaluate to 'seeded' state."""
    counts = {"customers": 10, "products": 10, "orders": 10, "order_items": 16}
    assert evaluate_table_counts(counts) == "seeded"


def test_evaluate_table_counts_partial_aborts():
    """Verify partial or inconsistent data raises ValueError to prevent corruption."""
    # Some tables empty, others populated
    counts1 = {"customers": 5, "products": 0, "orders": 0, "order_items": 0}
    with pytest.raises(ValueError, match="Inconsistent table state"):
        evaluate_table_counts(counts1)

    # All populated, but unexpected row count
    counts2 = {"customers": 10, "products": 10, "orders": 9, "order_items": 16}
    with pytest.raises(ValueError, match="Inconsistent table state"):
        evaluate_table_counts(counts2)


# ------------------------------------------------------------------------------
# 4. Sequence Synchronization Calculation Tests (Requirement 7)
# ------------------------------------------------------------------------------
def test_calculate_next_sequence_val_positive():
    """Verify positive max_id yields (max_id, True)."""
    val, is_called = calculate_next_sequence_val(1010)
    assert val == 1010
    assert is_called is True

    val, is_called = calculate_next_sequence_val(42)
    assert val == 42
    assert is_called is True


def test_calculate_next_sequence_val_zero():
    """Verify zero max_id yields (1, False) so the next insert receives 1."""
    val, is_called = calculate_next_sequence_val(0)
    assert val == 1
    assert is_called is False


# ------------------------------------------------------------------------------
# 5. PostgreSQL 16 Schema Ownership & Ephemeral Cleanup Tests (Requirement 5)
# ------------------------------------------------------------------------------
def test_transfer_schema_ownership_already_owned():
    """Verify no ownership statements are executed if schema is already owned."""
    mock_cursor = MagicMock()
    # Mock return value for current owner query
    mock_cursor.fetchone.return_value = ("app_user",)

    result = transfer_schema_ownership(mock_cursor, "app_metadata", "app_user")
    assert result is False
    # Only the SELECT query should have been executed
    assert mock_cursor.execute.call_count == 1


def test_transfer_schema_ownership_ephemeral_grant_and_cleanup():
    """Verify PG16 ephemeral role membership grant and cleanup in finally block."""
    mock_cursor = MagicMock()
    # Current owner is neondb_owner
    mock_cursor.fetchone.return_value = ("neondb_owner",)

    result = transfer_schema_ownership(mock_cursor, "app_metadata", "app_user")
    assert result is True

    # Check execution sequence:
    # 1. SELECT current owner
    # 2. GRANT app_user TO CURRENT_USER
    # 3. ALTER SCHEMA app_metadata OWNER TO app_user
    # 4. REVOKE app_user FROM CURRENT_USER
    calls = mock_cursor.execute.call_args_list
    assert len(calls) == 4

    # Verify call 2 was GRANT
    grant_composed = calls[1][0][0]
    assert isinstance(grant_composed, sql.Composed)
    assert "GRANT" in str(grant_composed)

    # Verify call 3 was ALTER SCHEMA
    alter_composed = calls[2][0][0]
    assert isinstance(alter_composed, sql.Composed)
    assert "ALTER SCHEMA" in str(alter_composed)

    # Verify call 4 was REVOKE
    revoke_composed = calls[3][0][0]
    assert isinstance(revoke_composed, sql.Composed)
    assert "REVOKE" in str(revoke_composed)


def test_transfer_schema_ownership_cleanup_failure_aborts():
    """Verify critical RuntimeError is raised if membership cleanup fails."""
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = ("neondb_owner",)

    def side_effect(query, *args, **kwargs):
        # Fail when REVOKE is called
        if isinstance(query, sql.Composed) and "REVOKE" in str(query):
            raise psycopg.Error("Simulated network drop on revoke")
        return None

    mock_cursor.execute.side_effect = side_effect

    with pytest.raises(RuntimeError, match="CRITICAL PRIVILEGE CLEANUP FAILURE"):
        transfer_schema_ownership(mock_cursor, "app_metadata", "app_user")


# ------------------------------------------------------------------------------
# 6. Configuration Validation & Dry-Run Tests (Requirement 13)
# ------------------------------------------------------------------------------
def test_validate_configuration_valid():
    """Verify a valid remote Neon configuration yields no validation errors."""
    valid_settings = Settings(
        POSTGRES_HOST="ep-cold-glitter-ao63f4sx.c-2.ap-southeast-1.aws.neon.tech",
        POSTGRES_PORT=5432,
        POSTGRES_DB="neondb",
        POSTGRES_ADMIN_USER="neondb_owner",
        POSTGRES_ADMIN_PASSWORD="super_secure_admin_password_123",
        APP_DB_USER="app_user",
        APP_DB_PASSWORD="secure_app_user_token_abc123456789",
        ANALYTICS_DB_USER="analytics_user",
        ANALYTICS_DB_PASSWORD="secure_analytics_token_xyz987654321",
        PGSSLMODE="require",
        DB_SERVERLESS_MODE=True,
    )
    errors = validate_configuration(valid_settings)
    assert errors == []


def test_validate_configuration_detects_flaws():
    """Verify validation detects invalid hosts, db name, sslmode, serverless mode, and password collisions."""
    invalid_settings = Settings(
        POSTGRES_HOST="localhost",  # invalid for Neon
        POSTGRES_PORT=5432,
        POSTGRES_DB="local_db",     # must be neondb
        POSTGRES_ADMIN_USER="neondb_owner",
        POSTGRES_ADMIN_PASSWORD="changeme",  # placeholder
        APP_DB_USER="app_user",
        APP_DB_PASSWORD="same_password_for_both",
        ANALYTICS_DB_USER="analytics_user",
        ANALYTICS_DB_PASSWORD="same_password_for_both",  # duplicate passwords
        PGSSLMODE="disable",        # must be require
        DB_SERVERLESS_MODE=False,   # must be True
    )
    errors = validate_configuration(invalid_settings)
    assert any("POSTGRES_HOST" in e for e in errors)
    assert any("POSTGRES_DB" in e for e in errors)
    assert any("SSL mode" in e for e in errors)
    assert any("DB_SERVERLESS_MODE" in e for e in errors)
    assert any("distinct" in e for e in errors)
    assert any("placeholder" in e for e in errors)


def test_run_dry_run_success():
    """Verify run_dry_run returns 0 and does not open network connections."""
    valid_settings = Settings(
        POSTGRES_HOST="ep-cold-glitter-ao63f4sx.c-2.ap-southeast-1.aws.neon.tech",
        POSTGRES_PORT=5432,
        POSTGRES_DB="neondb",
        POSTGRES_ADMIN_USER="neondb_owner",
        POSTGRES_ADMIN_PASSWORD="super_secure_admin_password_123",
        APP_DB_USER="app_user",
        APP_DB_PASSWORD="secure_app_user_token_abc123456789",
        ANALYTICS_DB_USER="analytics_user",
        ANALYTICS_DB_PASSWORD="secure_analytics_token_xyz987654321",
        PGSSLMODE="require",
        DB_SERVERLESS_MODE=True,
    )
    # Ensure psycopg.connect is never called during dry run
    with patch("psycopg.connect") as mock_connect:
        exit_code = run_dry_run(valid_settings, Path(".env.neon"))
        assert exit_code == 0
        mock_connect.assert_not_called()


def test_transfer_schema_ownership_handles_aborted_transaction():
    """Verify that when a transaction is in INERROR state, connection.rollback() is invoked."""
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = ("neondb_owner",)
    mock_conn = MagicMock()
    mock_conn.info.transaction_status = psycopg.pq.TransactionStatus.INERROR
    mock_cursor.connection = mock_conn

    # Simulate error on ALTER SCHEMA
    def side_effect(query, *args, **kwargs):
        if isinstance(query, sql.Composed) and "ALTER SCHEMA" in str(query):
            raise psycopg.Error("Simulated alter schema failure")
        return None

    mock_cursor.execute.side_effect = side_effect

    with pytest.raises(psycopg.Error, match="Simulated alter schema failure"):
        transfer_schema_ownership(mock_cursor, "app_metadata", "app_user")

    # Verify rollback was called to clear the aborted transaction
    mock_conn.rollback.assert_called_once()


def test_run_post_init_verification_ownership_mismatch():
    """Verify run_post_init_verification fails if app_metadata is not owned by app_user."""
    from scripts.init_remote_neon import run_post_init_verification

    valid_settings = Settings(
        POSTGRES_HOST="ep-cold-glitter-ao63f4sx.c-2.ap-southeast-1.aws.neon.tech",
        POSTGRES_PORT=5432,
        POSTGRES_DB="neondb",
        POSTGRES_ADMIN_USER="neondb_owner",
        POSTGRES_ADMIN_PASSWORD="super_secure_admin_password_123",
        APP_DB_USER="app_user",
        APP_DB_PASSWORD="secure_app_user_token_abc123456789",
        ANALYTICS_DB_USER="analytics_user",
        ANALYTICS_DB_PASSWORD="secure_analytics_token_xyz987654321",
        PGSSLMODE="require",
        DB_SERVERLESS_MODE=True,
    )

    mock_analytics_conn = MagicMock()
    mock_analytics_cur = MagicMock()
    mock_analytics_conn.__enter__.return_value = mock_analytics_conn
    mock_analytics_conn.cursor.return_value.__enter__.return_value = mock_analytics_cur
    # Analytics user checks pass
    mock_analytics_cur.fetchone.side_effect = [
        (10,), # products count
        (1, "Emma", "UK"), # customers allowed cols
    ]
    # Email query raises InsufficientPrivilege
    def analytics_side_effect(query, *args, **kwargs):
        if "email" in str(query):
            raise psycopg.errors.InsufficientPrivilege("Permission denied")
        if "query_audit_logs" in str(query):
            raise psycopg.errors.InsufficientPrivilege("Permission denied")
        if "INSERT INTO" in str(query):
            raise psycopg.errors.ReadOnlySqlTransaction("Read-only")
        return None
    mock_analytics_cur.execute.side_effect = analytics_side_effect

    mock_app_conn = MagicMock()
    mock_app_cur = MagicMock()
    mock_app_conn.__enter__.return_value = mock_app_conn
    mock_app_conn.cursor.return_value.__enter__.return_value = mock_app_cur
    # Ownership mismatch: owner is neondb_owner, not app_user!
    mock_app_cur.fetchone.return_value = ("neondb_owner",)

    with patch("psycopg.connect", side_effect=[mock_analytics_conn, mock_app_conn]):
        with pytest.raises(RuntimeError, match="OWNERSHIP VIOLATION"):
            run_post_init_verification(valid_settings)


# ------------------------------------------------------------------------------
# 7. PostgreSQL 16 Schema Grant Syntax & Least-Privilege Regression Tests
# ------------------------------------------------------------------------------
def test_build_grant_schema_sql_valid_syntax_and_least_privilege():
    """Verify build_grant_schema_sql generates valid, least-privilege PostgreSQL 16 statements."""
    # 1. Analytics schema to app_user: least privilege is USAGE (not ALL)
    query_app = build_grant_schema_sql("analytics", "app_user", "USAGE")
    assert isinstance(query_app, sql.Composed)
    rendered_app = query_app.as_string(None)
    assert rendered_app == 'GRANT USAGE ON SCHEMA "analytics" TO "app_user"'
    assert "ALL" not in rendered_app
    assert "," not in rendered_app

    # 2. Analytics schema to analytics_user: least privilege is USAGE
    query_analytics = build_grant_schema_sql("analytics", "analytics_user", "USAGE")
    assert isinstance(query_analytics, sql.Composed)
    rendered_analytics = query_analytics.as_string(None)
    assert rendered_analytics == 'GRANT USAGE ON SCHEMA "analytics" TO "analytics_user"'

    # 3. Default parameter should be USAGE for least privilege
    query_default = build_grant_schema_sql("analytics", "app_user")
    assert query_default.as_string(None) == 'GRANT USAGE ON SCHEMA "analytics" TO "app_user"'

    # 4. App metadata schema to app_user: owner privilege is ALL
    query_metadata = build_grant_schema_sql("app_metadata", "app_user", "ALL")
    assert query_metadata.as_string(None) == 'GRANT ALL ON SCHEMA "app_metadata" TO "app_user"'


def test_build_grant_schema_sql_rejects_invalid_syntax_combinations():
    """Verify build_grant_schema_sql rejects invalid syntax combinations like 'USAGE, ALL'."""
    invalid_privileges = [
        "USAGE, ALL",
        "ALL, USAGE",
        "SELECT",
        "INSERT",
        "UPDATE",
        "DELETE",
        "USAGE, CREATE, ALL",
        "",
        "DROP",
    ]
    for inv in invalid_privileges:
        with pytest.raises(ValueError, match="Invalid schema privilege"):
            build_grant_schema_sql("analytics", "app_user", inv)


def test_phase5_schema_grants_syntax_and_least_privilege():
    """
    Regression test asserting that Phase 5 apply_schema_and_column_security
    generates valid PostgreSQL syntax and strictly preserves least privilege.
    """
    mock_cursor = MagicMock()
    apply_schema_and_column_security(
        cursor=mock_cursor,
        db_name="neondb",
        app_role="app_user",
        analytics_role="analytics_user",
    )

    executed_queries = []
    for call_item in mock_cursor.execute.call_args_list:
        raw_query = call_item[0][0]
        if isinstance(raw_query, sql.Composed):
            rendered = raw_query.as_string(None)
        else:
            rendered = str(raw_query)
        executed_queries.append(rendered)

    # 1. Assert NO query contains the invalid 'USAGE, ALL' syntax that broke Neon init
    for q in executed_queries:
        assert "USAGE, ALL" not in q, f"Detected invalid syntax in query: {q}"
        assert "ALL, USAGE" not in q, f"Detected invalid syntax in query: {q}"

    # 2. Check schema grants on 'analytics'
    analytics_schema_grants = [
        q for q in executed_queries if "ON SCHEMA \"analytics\"" in q or "ON SCHEMA analytics" in q
    ]
    # Expect USAGE granted to app_user and analytics_user
    assert any(
        'GRANT USAGE ON SCHEMA "analytics" TO "app_user"' in q for q in analytics_schema_grants
    ), "Expected app_user to receive GRANT USAGE ON SCHEMA analytics"
    assert any(
        'GRANT USAGE ON SCHEMA "analytics" TO "analytics_user"' in q for q in analytics_schema_grants
    ), "Expected analytics_user to receive GRANT USAGE ON SCHEMA analytics"

    # Preserves least privilege: neither role should have ALL on schema analytics
    for q in analytics_schema_grants:
        assert "GRANT ALL ON SCHEMA" not in q, (
            f"Least-privilege violation: found ALL granted on schema analytics: {q}"
        )

    # 3. Check schema grants on 'app_metadata'
    metadata_schema_grants = [
        q for q in executed_queries if "ON SCHEMA \"app_metadata\"" in q or "ON SCHEMA app_metadata" in q
    ]
    assert any(
        'GRANT ALL ON SCHEMA "app_metadata" TO "app_user"' in q for q in metadata_schema_grants
    ), "Expected app_user to receive GRANT ALL ON SCHEMA app_metadata"
    assert any(
        'REVOKE ALL ON SCHEMA app_metadata FROM "analytics_user"' in q
        or 'REVOKE ALL ON SCHEMA "app_metadata" FROM "analytics_user"' in q
        for q in metadata_schema_grants
    ), "Expected analytics_user to be denied app_metadata schema"

    # 4. Check column-level security on analytics.customers
    customer_grants = [q for q in executed_queries if "customers" in q]
    assert any(
        'REVOKE SELECT ON analytics.customers FROM "analytics_user"' in q for q in customer_grants
    )
    col_grant = next(
        (q for q in customer_grants if "GRANT SELECT (" in q and "analytics_user" in q), None
    )
    assert col_grant is not None, "Expected column-level GRANT SELECT on analytics.customers"
    assert "email" not in col_grant, "Column-level grant must not include 'email'"


# ------------------------------------------------------------------------------
# 8. Alembic Remote Migration Target & Verification Regression Tests
# ------------------------------------------------------------------------------
def test_get_settings_respects_runtime_env_file(tmp_path):
    """Verify get_settings() reloads settings dynamically when ENV_FILE changes."""
    import os
    from src.core.config import get_settings

    custom_env = tmp_path / ".env.custom_neon"
    custom_env.write_text(
        "POSTGRES_HOST=ep-custom-remote.neon.tech\n"
        "POSTGRES_PORT=5432\n"
        "POSTGRES_DB=neondb\n"
        "POSTGRES_ADMIN_USER=custom_admin\n"
        "POSTGRES_ADMIN_PASSWORD=custom_admin_password_123\n"
        "APP_DB_USER=custom_app\n"
        "APP_DB_PASSWORD=custom_app_password_123\n"
        "ANALYTICS_DB_USER=custom_analytics\n"
        "ANALYTICS_DB_PASSWORD=custom_analytics_password_123\n"
        "DB_SERVERLESS_MODE=true\n"
        "PGSSLMODE=require\n"
    )

    old_env = os.environ.get("ENV_FILE")
    try:
        os.environ["ENV_FILE"] = str(custom_env)
        get_settings.cache_clear()
        s = get_settings()
        assert s.POSTGRES_HOST == "ep-custom-remote.neon.tech"
        assert s.POSTGRES_DB == "neondb"
        assert "ep-custom-remote.neon.tech" in str(s.get_app_database_url())
    finally:
        if old_env is not None:
            os.environ["ENV_FILE"] = old_env
        else:
            os.environ.pop("ENV_FILE", None)
        get_settings.cache_clear()


def test_alembic_fileconfig_preserves_application_loggers():
    """Verify fileConfig in alembic/env.py does not disable existing application loggers."""
    import logging
    from logging.config import fileConfig

    test_logger = logging.getLogger("test_preservation_logger")
    test_logger.setLevel(logging.INFO)
    test_logger.handlers.clear()
    stream = MagicMock()
    handler = logging.StreamHandler(stream)
    test_logger.addHandler(handler)

    # Calling fileConfig with disable_existing_loggers=False
    fileConfig("alembic.ini", disable_existing_loggers=False)

    assert test_logger.disabled is False
    test_logger.info("Log message should not be disabled")
    assert stream.write.called


def test_alembic_honors_injected_connection():
    """Verify Alembic uses config.attributes['connection'] when present and ignores app_engine."""
    from alembic.config import Config
    from alembic import command

    cfg = Config("alembic.ini")
    mock_connection = MagicMock()
    mock_connection.dialect.name = "postgresql"
    mock_connection.dialect.server_version_info = (16, 0)
    cfg.attributes["connection"] = mock_connection

    with patch("src.db.session.app_engine") as mock_app_engine:
        command.upgrade(cfg, "head")

        # Injected connection was used to run Alembic migration statements
        assert len(mock_connection.method_calls) > 0
        # The module-level app_engine.connect() was NOT invoked
        mock_app_engine.connect.assert_not_called()



def test_remote_migration_path_constructs_engine_with_neon_url():
    """Verify Phase 6b constructs migration engine using settings.get_app_database_url()."""
    from scripts.init_remote_neon import execute_remote_initialization
    from sqlalchemy.pool import NullPool

    neon_settings = Settings(
        POSTGRES_HOST="ep-remote-glitter.aws.neon.tech",
        POSTGRES_PORT=5432,
        POSTGRES_DB="neondb",
        POSTGRES_ADMIN_USER="neondb_owner",
        POSTGRES_ADMIN_PASSWORD="super_secure_admin_password_123",
        APP_DB_USER="app_user",
        APP_DB_PASSWORD="secure_app_user_token_abc123456789",
        ANALYTICS_DB_USER="analytics_user",
        ANALYTICS_DB_PASSWORD="secure_analytics_token_xyz987654321",
        PGSSLMODE="require",
        DB_SERVERLESS_MODE=True,
    )

    mock_admin_conn = MagicMock()
    mock_app_conn = MagicMock()
    mock_migration_engine = MagicMock()
    mock_migration_conn = MagicMock()
    mock_migration_engine.connect.return_value.__enter__.return_value = mock_migration_conn

    with patch("psycopg.connect", side_effect=[mock_admin_conn, mock_app_conn]), \
         patch("scripts.init_remote_neon.ensure_role", return_value="updated"), \
         patch("scripts.init_remote_neon.configure_analytics_role_attributes"), \
         patch("scripts.init_remote_neon.transfer_schema_ownership", return_value=False), \
         patch("scripts.init_remote_neon.create_business_tables"), \
         patch("scripts.init_remote_neon.check_table_clean_state", return_value="seeded"), \
         patch("scripts.init_remote_neon.sync_table_sequences", return_value={}), \
         patch("scripts.init_remote_neon.apply_schema_and_column_security"), \
         patch("scripts.init_remote_neon.configure_app_user_defaults"), \
         patch("sqlalchemy.create_engine", return_value=mock_migration_engine) as mock_create_engine, \
         patch("alembic.command.upgrade") as mock_upgrade, \
         patch("scripts.init_remote_neon.run_post_init_verification") as mock_verify:

        execute_remote_initialization(neon_settings, run_migrations=True)

        # 1. create_engine was called with the target Neon URL (not localhost!)
        mock_create_engine.assert_called_once()
        target_url = mock_create_engine.call_args[0][0]
        assert "ep-remote-glitter.aws.neon.tech" in str(target_url)
        assert "localhost" not in str(target_url)
        assert mock_create_engine.call_args[1].get("poolclass") == NullPool

        # 2. Connection was injected into Alembic config attributes
        assert mock_upgrade.called
        passed_cfg = mock_upgrade.call_args[0][0]
        assert passed_cfg.attributes["connection"] == mock_migration_conn

        # 3. Engine was properly disposed
        mock_migration_engine.dispose.assert_called_once()

        # 4. Verification was invoked with require_migrations=True
        mock_verify.assert_called_once_with(neon_settings, require_migrations=True)


def test_post_init_verification_fails_loudly_when_migrations_missing():
    """Verify run_post_init_verification raises RuntimeError when required migrations are missing."""
    from scripts.init_remote_neon import run_post_init_verification

    test_settings = Settings(
        POSTGRES_HOST="ep-remote.neon.tech",
        POSTGRES_PORT=5432,
        POSTGRES_DB="neondb",
        POSTGRES_ADMIN_USER="neondb_owner",
        POSTGRES_ADMIN_PASSWORD="super_secure_admin_password_123",
        APP_DB_USER="app_user",
        APP_DB_PASSWORD="secure_app_user_token_abc123456789",
        ANALYTICS_DB_USER="analytics_user",
        ANALYTICS_DB_PASSWORD="secure_analytics_token_xyz987654321",
        PGSSLMODE="require",
        DB_SERVERLESS_MODE=True,
    )

    mock_analytics_conn = MagicMock()
    mock_analytics_cur = MagicMock()
    mock_analytics_conn.__enter__.return_value = mock_analytics_conn
    mock_analytics_conn.cursor.return_value.__enter__.return_value = mock_analytics_cur
    mock_analytics_cur.fetchone.side_effect = [
        (10,),              # products count
        (1, "A", "US"),     # customers allowed
    ]
    def analytics_side_effect(query, *args, **kwargs):
        if "email" in str(query):
            raise psycopg.errors.InsufficientPrivilege("Denied email")
        if "query_audit_logs" in str(query):
            raise psycopg.errors.InsufficientPrivilege("Denied metadata")
        if "INSERT INTO" in str(query):
            raise psycopg.errors.ReadOnlySqlTransaction("Read-only")
        return None
    mock_analytics_cur.execute.side_effect = analytics_side_effect

    # 1. Test case: require_migrations=True and query_audit_logs does NOT exist
    mock_app_conn1 = MagicMock()
    mock_app_cur1 = MagicMock()
    mock_app_conn1.__enter__.return_value = mock_app_conn1
    mock_app_conn1.cursor.return_value.__enter__.return_value = mock_app_cur1
    mock_app_cur1.fetchone.side_effect = [
        ("app_user",),  # ownership check: app_user owns app_metadata
        (10,),          # customers count
        (False,),       # EXISTS query_audit_logs -> FALSE!
        (False,),       # EXISTS alembic_version -> FALSE!
    ]

    with patch("psycopg.connect", side_effect=[mock_analytics_conn, mock_app_conn1]):
        with pytest.raises(RuntimeError, match="MIGRATION VERIFICATION FAILURE.*query_audit_logs"):
            run_post_init_verification(test_settings, require_migrations=True)

    # 2. Test case: require_migrations=False and query_audit_logs does not exist -> graceful PASS
    mock_analytics_cur.fetchone.side_effect = [
        (10,),              # products count
        (1, "A", "US"),     # customers allowed
    ]
    mock_app_conn2 = MagicMock()
    mock_app_cur2 = MagicMock()
    mock_app_conn2.__enter__.return_value = mock_app_conn2
    mock_app_conn2.cursor.return_value.__enter__.return_value = mock_app_cur2
    mock_app_cur2.fetchone.side_effect = [
        ("app_user",),  # ownership check
        (10,),          # customers count
        (False,),       # EXISTS query_audit_logs -> FALSE
        (False,),       # EXISTS alembic_version -> FALSE
    ]

    with patch("psycopg.connect", side_effect=[mock_analytics_conn, mock_app_conn2]):
        # Must not raise an error when require_migrations=False
        run_post_init_verification(test_settings, require_migrations=False)

    # 3. Test case: require_migrations=True, tables and revision exist -> PASS
    mock_analytics_cur.fetchone.side_effect = [
        (10,),              # products count
        (1, "A", "US"),     # customers allowed
    ]
    mock_app_conn3 = MagicMock()
    mock_app_cur3 = MagicMock()
    mock_app_conn3.__enter__.return_value = mock_app_conn3
    mock_app_conn3.cursor.return_value.__enter__.return_value = mock_app_cur3
    mock_app_cur3.fetchone.side_effect = [
        ("app_user",),          # ownership check
        (10,),                  # customers count
        (True,),                # EXISTS query_audit_logs -> True
        (True,),                # EXISTS alembic_version -> True
        ("140c3549174e",),      # version_num
        (0,),                   # audit logs count
    ]

    with patch("psycopg.connect", side_effect=[mock_analytics_conn, mock_app_conn3]):
        # Must pass cleanly when tables and revision are confirmed
        run_post_init_verification(test_settings, require_migrations=True)


