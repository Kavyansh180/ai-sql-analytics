-- ==============================================================================
-- 01_init_roles_and_schemas.sql
-- PostgreSQL 16 Initialization Script
-- Configures roles, security boundaries, schemas, and privilege isolation.
-- ==============================================================================

-- 1. Create Dedicated Application and Analytics Roles Idempotently
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'app_user') THEN
        CREATE ROLE app_user WITH LOGIN PASSWORD 'app_user_dev_password';
    END IF;

    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'analytics_user') THEN
        CREATE ROLE analytics_user WITH LOGIN PASSWORD 'analytics_user_dev_password';
    END IF;
END
$$;

-- 2. Configure Hardened Defaults for Analytics Role
-- Enforce a 3-second statement timeout and default read-only transactions
ALTER ROLE analytics_user SET statement_timeout = '3000ms';
ALTER ROLE analytics_user SET default_transaction_read_only = 'on';

-- 3. Create Isolated Schemas
CREATE SCHEMA IF NOT EXISTS analytics;
CREATE SCHEMA IF NOT EXISTS app_metadata;

-- 4. Harden Database & Public Schema (Defense-in-depth against privilege leaks)
-- Revoke temporary table creation at database level (uses current database dynamically)
DO $$
BEGIN
    EXECUTE format('REVOKE TEMPORARY ON DATABASE %I FROM PUBLIC', current_database());
    EXECUTE format('REVOKE TEMPORARY ON DATABASE %I FROM analytics_user', current_database());
END
$$;

-- Revoke set_config to prevent session statement_timeout tampering
-- If the current executing role lacks permission to revoke privileges on pg_catalog functions
-- (e.g. in managed cloud environments such as Neon where the owner is not a superuser),
-- skip with a clear NOTICE; do not fail or falsely claim the revoke was applied.
-- The AST validator (src/security/validator.py) provides deterministic defense-in-depth
-- by rejecting set_config before execution.
DO $$
BEGIN
    BEGIN
        REVOKE EXECUTE ON FUNCTION pg_catalog.set_config(text, text, boolean) FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION pg_catalog.set_config(text, text, boolean) FROM analytics_user;
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE NOTICE 'Skipping pg_catalog.set_config revoke: executing role lacks superuser privileges on pg_catalog. AST validator enforces this boundary.';
    END;
END
$$;

-- Revoke all public schema access
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON SCHEMA public FROM analytics_user;

-- 5. Configure app_metadata Schema (Application and Migration Data Only)
ALTER SCHEMA app_metadata OWNER TO app_user;
GRANT ALL ON SCHEMA app_metadata TO app_user;
GRANT ALL ON ALL TABLES IN SCHEMA app_metadata TO app_user;
GRANT ALL ON ALL SEQUENCES IN SCHEMA app_metadata TO app_user;

-- Explicitly deny analytics_user from touching application metadata
REVOKE ALL ON SCHEMA app_metadata FROM analytics_user;
REVOKE ALL ON SCHEMA app_metadata FROM PUBLIC;

-- 6. Configure analytics Schema (Business Data for Copilot Queries)
GRANT USAGE ON SCHEMA analytics TO app_user;
GRANT USAGE ON SCHEMA analytics TO analytics_user;

GRANT ALL ON SCHEMA analytics TO app_user;

-- Default privileges for future tables created in analytics schema:
-- 1. Configure defaults for objects created by the current executing admin role.
--    Omitting 'FOR ROLE' targets CURRENT_USER without requiring cross-role membership.
ALTER DEFAULT PRIVILEGES IN SCHEMA analytics
    GRANT SELECT ON TABLES TO analytics_user;

ALTER DEFAULT PRIVILEGES IN SCHEMA analytics
    GRANT ALL ON TABLES TO app_user;

ALTER DEFAULT PRIVILEGES IN SCHEMA analytics
    GRANT ALL ON SEQUENCES TO app_user;

-- 2. Configure defaults for objects created by app_user (if authorized).
--    Only roles with superuser privileges or membership in app_user can alter defaults for app_user.
--    In local Docker (running as postgres superuser), this succeeds.
--    On Neon (running as non-superuser project owner), this is skipped with a clear NOTICE,
--    and the exact separate grant step is documented below.
DO $$
DECLARE
    v_is_authorized BOOLEAN := FALSE;
BEGIN
    SELECT (r.rolsuper OR pg_has_role(CURRENT_USER, 'app_user', 'MEMBER'))
    INTO v_is_authorized
    FROM pg_roles r
    WHERE r.rolname = CURRENT_USER;

    IF v_is_authorized THEN
        EXECUTE 'ALTER DEFAULT PRIVILEGES FOR ROLE app_user IN SCHEMA analytics GRANT SELECT ON TABLES TO analytics_user';
    ELSE
        RAISE NOTICE 'Executing role % is not authorized to alter defaults for app_user. When connected as app_user, execute: ALTER DEFAULT PRIVILEGES IN SCHEMA analytics GRANT SELECT ON TABLES TO analytics_user;', CURRENT_USER;
    END IF;
END
$$;
