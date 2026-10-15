-- ==============================================================================
-- 03_column_security_grants.sql
-- Column-Level Security Hardening on analytics.customers for analytics_user
-- Phase 3.3 Defense-in-Depth Enforcement
-- ==============================================================================

-- 1. Revoke table-level SELECT on customers from analytics_user
REVOKE SELECT ON analytics.customers FROM analytics_user;

-- 2. Grant explicit column-level SELECT on approved non-sensitive columns only
-- Strictly excludes 'email' and any unapproved future contact columns
GRANT SELECT (id, first_name, last_name, country, city, created_at) 
ON analytics.customers 
TO analytics_user;
