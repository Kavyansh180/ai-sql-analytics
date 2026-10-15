# AI-Powered SQL Analytics Copilot

An enterprise-grade, portfolio-ready Natural Language to SQL Analytics Copilot built with Python 3.13, PostgreSQL 16, SQLAlchemy 2.x (synchronous + psycopg 3), Alembic, SQLGlot, Google Gemini API (`google-genai`), and FastAPI.

---

## Architecture Overview (Modular Monolith)

```
d:/AI SQL ANALYTICS/
├── docker-compose.yml              # PostgreSQL 16 Alpine container (development)
├── docker-compose.prod.yml         # Production Compose with network isolation & containerized backend
├── Dockerfile                      # Multi-stage production Dockerfile (non-root appuser)
├── Dockerfile.lambda               # AWS Lambda Python 3.13 serverless container image
├── AWS_DEPLOYMENT_RUNBOOK.md       # Step-by-step production AWS deployment & security runbook
├── pyproject.toml                  # Python package & pytest configuration
├── requirements.txt                # Dependencies (FastAPI, SQLAlchemy, google-genai, etc.)
├── alembic.ini                     # Migration configuration
├── docker/
│   └── postgres/init/
│       ├── 01_init_roles_and_schemas.sql   # Idempotent role creation, privileges, and schema hardening
│       ├── 02_seed_business_data.sql       # Realistic e-commerce data (customers, products, orders, items)
│       └── 03_column_security_grants.sql   # Column-level SELECT grants on analytics.customers (Phase 3.3)
├── alembic/
│   ├── env.py                      # Configured for app_metadata schema and app_user role
│   └── versions/                   # Versioned DDL migrations
├── src/
│   ├── core/
│   │   ├── config.py               # Pydantic Settings & typed database URLs (psycopg 3)
│   │   └── logging.py              # Structured JSON logging, request correlation, & PII/secret redactor (Phase 4.1)
│   ├── db/
│   │   ├── session.py              # Role-specific SQLAlchemy engines & session managers
│   │   └── models/
│   │       ├── base.py             # DeclarativeBase
│   │       ├── business.py         # Business entities in 'analytics' schema
│   │       └── metadata.py         # Audit logging entity in 'app_metadata' schema
│   ├── security/
│   │   ├── policy.py               # Centralized column allowlists, blocklists, and drift guard (Phase 3.3)
│   │   └── validator.py            # SQLGlot AST guardrails, column security, allowlists, and limit clamping
│   ├── llm/
│   │   ├── exceptions.py           # Domain exceptions (LLMConfigurationError, etc.)
│   │   ├── models.py               # Pydantic structured output models (GeneratedSQLResponse)
│   │   ├── schema_context.py       # Grounded schema definitions & approved business rules
│   │   ├── prompts.py              # Versioned system prompt with safety constraints
│   │   ├── provider.py             # LLM provider with bounded backoff retries & jitter (Phase 4.1)
│   │   └── service.py              # Orchestration service (validation -> LLM -> guardrail -> DB)
│   ├── api/
│   │   ├── middleware.py           # Pure ASGI body limit (64KB), request ID, & structured access logging (Phase 4.1)
│   │   ├── rate_limiter.py         # In-memory sliding window rate limiter with TTL pruning (Phase 4.1)
│   │   ├── schemas.py              # API request & response DTOs with question validation
│   │   ├── routes.py               # Versioned REST endpoint: POST /api/v1/analytics/query
│   │   └── main.py                 # FastAPI application factory, liveness, & readiness probe
│   └── frontend/
│       ├── __init__.py             # Frontend package initialization
│       ├── client.py               # HTTP API client, deduplication, & error mapping
│       ├── charts.py               # Automatic Plotly chart selection & rendering (Phase 3.2)
│       └── app.py                  # Streamlit business analyst UI
└── tests/
    ├── conftest.py                 # Shared database test fixtures
    ├── test_api_endpoints.py       # FastAPI HTTP endpoint tests & CLS rejection
    ├── test_column_security.py     # 70 adversarial AST & DB column-level security tests (Phase 3.3.1)
    ├── test_db_connectivity.py     # Connection & seed verification
    ├── test_frontend_charts.py     # Automatic chart selection unit tests (Phase 3.2)
    ├── test_frontend_client.py     # Frontend API client & deduplication tests
    ├── test_llm_models.py          # Structured response model invariants
    ├── test_llm_provider.py        # Provider abstraction & configuration tests
    ├── test_nl_sql_service.py      # End-to-end service orchestration tests
    ├── test_production_readiness.py# 27 production readiness & hardening tests (API limits, rate limiting, logs, retries)
    ├── test_schema_context.py      # Schema drift detection against ORM models
    ├── test_security_roles.py      # PostgreSQL privilege boundary tests (DML/DDL & column grants)
    ├── test_session_helpers.py     # Session helper utility & row bounding tests
    ├── test_sql_validator.py       # SQLGlot AST policy tests (CTEs, functions, shapes)
    └── test_statement_timeout.py   # PostgreSQL statement_timeout cancellation tests
```

---

## Defense-in-Depth Security Model

The system enforces security at **both** the AST validation layer and the PostgreSQL database engine layer:

### 1. Database Engine-Level Hardening
- **Role-Based Privilege Separation**:
  - `app_user`: Service role that owns and manages the `app_metadata` schema (query audit logs and Alembic migrations).
  - `analytics_user`: Restricted read-only role used for all copilot SQL executions against the `analytics` schema.
- **Engine-Enforced Controls**:
  - `analytics_user` has `default_transaction_read_only = 'on'`.
  - `statement_timeout = '3000ms'` enforced at the role configuration level.
  - `REVOKE TEMPORARY ON DATABASE` from `PUBLIC` and `analytics_user` to block temp table creation.
  - `REVOKE EXECUTE ON FUNCTION pg_catalog.set_config` from `PUBLIC` and `analytics_user` to prevent session-level timeout or config tampering.
  - `public` schema has `REVOKE CREATE` and `REVOKE ALL` from `PUBLIC` and `analytics_user`.
  - `app_metadata` schema is strictly isolated: `analytics_user` has all privileges revoked (PostgreSQL returns `permission denied`).
  - Even if `SET transaction_read_only = off;` is attempted in a session, PostgreSQL table-level grants reject any `INSERT`, `UPDATE`, `DELETE`, `DROP`, or `CREATE`.

### 2. AST Query Guardrails (SQLGlot)
- **Statement Integrity**: Exactly one statement allowed (statement chaining with `;` is rejected).
- **Read-Only Shapes**: Only `SELECT`, `UNION`, `INTERSECT`, `EXCEPT`, and CTE (`WITH ... SELECT`) expressions permitted.
- **Table Allowlist**: Referenced tables must match the approved business catalog (`customers`, `products`, `orders`, `order_items`). CTE aliases defined in the query are recognized and validated.
- **Dangerous Functions**: Functions like `set_config`, `pg_sleep`, `pg_read_file`, `dblink`, `query_to_xml`, and `pg_terminate_backend` are blocked at the AST level.
- **Table Creation Bypass**: `SELECT ... INTO` is blocked.
- **Automated Row Limits**: Queries without `LIMIT` or with excessive `LIMIT` clauses are automatically clamped to `max_limit` (default 1,000 rows).

---

## Environment Configuration

Copy `.env.example` to `.env` and configure your settings:

```env
# PostgreSQL Configuration
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=sql_analytics_db

# Role Credentials
POSTGRES_ADMIN_USER=postgres
POSTGRES_ADMIN_PASSWORD=postgres_dev_password
APP_DB_USER=app_user
APP_DB_PASSWORD=app_user_dev_password
ANALYTICS_DB_USER=analytics_user
ANALYTICS_DB_PASSWORD=analytics_user_dev_password

# Guardrail Defaults
ANALYTICS_STATEMENT_TIMEOUT_MS=3000
MAX_QUERY_ROW_LIMIT=1000
ENVIRONMENT=development

# LLM Provider Configuration (Phase 2)
# Obtain an API key from Google AI Studio (https://aistudio.google.com/)
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-3.1-flash-lite
LLM_REQUEST_TIMEOUT_SECONDS=30

# Frontend Configuration (Phase 3)
API_BASE_URL=http://127.0.0.1:8000
```

> [!NOTE]
> When `GEMINI_API_KEY` is not provided, unit and integration tests run entirely against `MockLLMProvider` and do not require internet access or API credits. The Streamlit frontend calls the backend API and never accesses the database or Gemini API directly.

---

## Running Locally

To run the complete application, open three separate terminal windows:

### Terminal 1: Start PostgreSQL 16
```powershell
docker compose up -d
```
*(Optionally run migrations on first setup: `.\.venv\Scripts\python.exe -m alembic upgrade head`)*

### Terminal 2: Start the FastAPI Backend Server
```powershell
.\.venv\Scripts\python.exe -m uvicorn src.api.main:app --host 127.0.0.1 --port 8000 --reload
```

### Terminal 3: Start the Streamlit Analytics Frontend
```powershell
.\.venv\Scripts\python.exe -m streamlit run src/frontend/app.py
```
Open [http://localhost:8501](http://localhost:8501) in your browser to access the Streamlit UI.

---

## Running Tests

Run the complete automated test suite (221 automated tests across 15 test modules):

```powershell
.\.venv\Scripts\python.exe -m pytest -v
```

---

## Automatic Data Visualization (Plotly) — Phase 3.2

The Streamlit frontend automatically inspects returned tabular columns and metrics to render interactive Plotly charts:
- **📈 Line Chart**: Automatically chosen when there is a recognized date or time column (`order_month`, `order_date`, etc.) and a numeric metric with $\ge 2$ data points. Values are sorted chronologically.
- **📊 Bar Chart**: Chosen when there is a categorical entity (`product_name`, `customer_name`, `country`) and a numeric metric. Automatically switches to a horizontal bar chart when labels are long (> 15 characters) or when there are $> 8$ items for readable presentation.
- **🍩 Pie / Donut Chart**: Selected for parts-of-a-whole composition comparisons when there are 2 to 6 non-negative categories with a positive sum (e.g., category or status distribution).
- **📋 Table Only (No Chart)**: The application intentionally displays the data table with an explanatory note when a chart is not appropriate:
  - Empty result sets ($0$ rows).
  - Datasets containing only text or categorical columns without any numeric metrics.
  - Single scalar values ($1$ cell) lacking a comparative or temporal dimension.
  - Non-numeric or invalid metric values.
- **Dual Tab Control**: When a visualization is available, users can toggle between the **📊 Visualization** tab and the **📋 Data Table** tab to inspect raw rows. Underlying table data is never mutated for plotting.

---

## Frontend & Backend Communication Architecture

The Streamlit frontend (`src/frontend/app.py` & `src/frontend/client.py`) is decoupled from the database and LLM provider:
1. **No Direct Database Access**: The frontend does not establish database connections or generate SQL queries independently.
2. **REST API Communication**: The frontend interacts exclusively via HTTP with the FastAPI backend:
   - `GET /health`: Used by the sidebar status widget to verify backend connectivity.
   - `POST /api/v1/analytics/query`: Submits natural language queries and receives validated, serialized results.
3. **Configuration**: Configured via `API_BASE_URL` (default: `http://127.0.0.1:8000`), loaded from Streamlit secrets or environment variables.
4. **Secret Isolation**: `GEMINI_API_KEY` is exclusively managed on the backend and never exposed to the frontend browser or client bundle.
5. **Deduplication**: Identical questions submitted repeatedly without modification are served from the client session cache to prevent duplicate network and LLM calls.

---

## Example Business Questions

You can ask analytical questions such as:
- *"Which product generated the highest revenue?"*
- *"Show monthly revenue trends."*
- *"Which customers placed the most orders?"*
- *"Compare revenue across products."*

---

## Invoking the Analytics API Directly (CLI / HTTP)

### Interactive Documentation (Swagger UI)
Open [http://localhost:8000/docs](http://localhost:8000/docs) in your browser.

### Sample HTTP Request (PowerShell)
```powershell
$body = @{
    question = "Which product generated the highest revenue?"
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://localhost:8000/api/v1/analytics/query" `
    -Method Post `
    -Headers @{"Content-Type" = "application/json"} `
    -Body $body
```

### Sample HTTP Request (cURL)
```bash
curl -X POST "http://localhost:8000/api/v1/analytics/query" \
  -H "Content-Type: application/json" \
  -d '{"question": "Which product generated the highest revenue?"}'
```

### Representative Successful Response
```json
{
  "status": "ready",
  "question": "Which product generated the highest revenue?",
  "sql": "SELECT p.id, p.name, p.category, SUM(oi.quantity * oi.unit_price) AS total_revenue FROM analytics.products AS p JOIN analytics.order_items AS oi ON p.id = oi.product_id JOIN analytics.orders AS o ON oi.order_id = o.id WHERE o.status IN ('completed', 'shipped') GROUP BY p.id, p.name, p.category ORDER BY total_revenue DESC LIMIT 1",
  "explanation": "Calculates the total realized revenue per product from completed/shipped orders and returns the top product.",
  "columns": ["id", "name", "category", "total_revenue"],
  "rows": [
    {
      "id": 1,
      "name": "Pro Wireless Noise-Cancelling Headphones",
      "category": "Electronics",
      "total_revenue": 749.97
    }
  ],
  "row_count": 1,
  "execution_time_ms": 14.25,
  "clarification_question": null,
  "error": null
}
```

---

## Deterministic Column-Level Security (CLS) & SQL Security Hardening — Phase 3.3 & 3.3.1

Phase 3.3 and 3.3.1 address the security vulnerability where allowlisted tables (such as `analytics.customers`) contain sensitive personal data columns (`email`, phone, SSN). In Phase 3.3.1, security enforcement is **deterministic, automated, and decoupled from LLM prompt rules**: no query can access prohibited customer contact columns through any supported syntax.

### 1. The Prohibited Column Policy (`src/security/policy.py`)
- **Prohibited Table Columns**: `analytics.customers.email` is strictly prohibited for general analytical query access.
- **Global Prohibited Keywords**: Any column identifier matching `email`, `phone`, `ssn`, `social_security_number`, `password`, `secret`, or `credit_card` is blocked across all tables.
- **Wildcard Restricted Tables**: Tables containing prohibited fields (`customers`) cannot be queried with bare `SELECT *` or qualified wildcards (`SELECT c.*`).
- **Approved Columns**:
  - `customers`: `id`, `first_name`, `last_name`, `country`, `city`, `created_at` (preserves joins, customer rankings by name, and country aggregations).
  - `products`: `id`, `name`, `category`, `price`, `cost`, `stock_quantity`, `is_active`, `created_at`.
  - `orders`: `id`, `customer_id`, `order_date`, `status`, `total_amount`.
  - `order_items`: `id`, `order_id`, `product_id`, `quantity`, `unit_price`.

### 2. Deterministic AST Security Boundary (`src/security/validator.py`)
Enforced inside `SQLSecurityValidator.validate_and_sanitize()` before any SQL statement can reach database execution:
- **Direct Column Projection**: `SELECT email FROM analytics.customers` -> Rejected.
- **Table-Qualified & Case-Insensitive**: `SELECT c.email`, `SELECT c.EmAiL`, `SELECT c."email"`, `SELECT c."EMAIL"` -> Rejected.
- **Aliases & Computed Expressions**: `SELECT c.email AS contact`, `SELECT LOWER(c.email)`, `SELECT CONCAT(c.first_name, c.email)` -> Rejected.
- **Filter Clauses & Sorting**: `WHERE email LIKE '%@gmail.com'`, `ORDER BY email`, `GROUP BY email` -> Rejected.
- **Wildcard Interception**: `SELECT * FROM analytics.customers` and `SELECT c.* FROM analytics.customers c` are intercepted and rejected with instructions to select approved columns explicitly. Non-sensitive tables and aggregate wildcards (`COUNT(*)`, `COUNT(c.*)`) remain fully supported.
- **CTEs and Subqueries**: Prohibited columns inside CTE definitions (`WITH cte AS (SELECT email FROM ...) SELECT * FROM cte`) and nested derived tables (`SELECT sub.email FROM (SELECT ...) sub`) are inspected at the AST level and blocked immediately.
- **Joins**: Joins between tables (e.g. `customers c JOIN orders o ON c.id = o.customer_id`) are checked; selecting prohibited columns or using them in `USING(email)` clauses is rejected.
- **Scoped Ambiguity Guard (Phase 3.3.1)**: If an unqualified column (e.g. `id`) is present in multiple joined tables within a `Select` scope, the validator fails closed (`Unqualified column 'id' is ambiguous across multiple referenced tables (customers, orders). Must be table-qualified.`). Unambiguous unique columns (e.g. `first_name`, `total_amount`) remain permitted.
- **Resolver Discretion**: Proves that the AST resolver correctly distinguishes actual column references from harmless aliases (e.g. `AS email_count`), string literals (`'support@example.com'`), and SQL comments (`/* check email */`), avoiding false-positive rejections.
- **Fail-Closed Drift Guard**: If a qualified table query references an unclassified or newly added column not in `APPROVED_TABLE_COLUMNS`, the validator fails closed (`Column '<name>' is not in the approved column allowlist`).

### 3. Database-Level Defense-in-Depth (`03_column_security_grants.sql`)
Even if the application layer or AST parser were entirely bypassed, PostgreSQL itself enforces column-level privilege boundaries for `analytics_user`:
- The previous broad table grant `GRANT SELECT ON analytics.customers TO analytics_user;` has been revoked.
- Narrow column-level privileges are granted:
  ```sql
  GRANT SELECT (id, first_name, last_name, country, city, created_at)
  ON analytics.customers TO analytics_user;
  ```
- Any direct query by `analytics_user` attempting `SELECT email FROM analytics.customers` or `SELECT * FROM analytics.customers` triggers an immediate PostgreSQL kernel error: `psycopg.errors.InsufficientPrivilege: permission denied for table customers`.

### 4. Fresh Database vs. Existing Database Upgrade Procedure
- **Fresh Database Initialization**: During initial container setup with an empty volume, the PostgreSQL entrypoint executes `/docker-entrypoint-initdb.d/` in lexical order:
  1. `01_init_roles_and_schemas.sql`: Roles, timeouts, and schemas are created.
  2. `02_seed_business_data.sql`: Business tables are created and seeded.
  3. `03_column_security_grants.sql`: Table-level `SELECT` on `customers` is revoked and column grants are applied.
- **Existing Database Upgrade**: Docker init scripts do **not** re-run on existing Docker volumes. To apply column security grants on an existing database without data loss:
  ```powershell
  Get-Content docker/postgres/init/03_column_security_grants.sql | docker exec -i sql_analytics_postgres psql -U postgres -d sql_analytics_db
  ```

### 5. Automated Security Test Suite
- **`tests/test_column_security.py`**: 70 dedicated adversarial regression tests covering direct selection, aliases, SQL functions, wildcards, CTEs, subqueries, joins, case variations, scoped ambiguity resolution, resolver discretion (aliases, literals, comments), ORM schema drift detection, execution function bypass prevention, and PostgreSQL engine permissions.
- **`tests/test_security_roles.py`**: Verified that `analytics_user` is denied `email` and `*` at the PostgreSQL engine level while allowed to read approved customer columns.
- **`tests/test_api_endpoints.py`**: Verified API-level blocking of prohibited column access.

---

## Production Readiness & Deployment Hardening — Phase 4

Phase 4 hardens the copilot application for reliable, secure, observable deployment:

### 1. API Hardening & Input Defense
- **Payload Size Clamping (`RequestBodyLimitMiddleware`)**: Pure ASGI middleware enforcing a strict 64 KB (`MAX_REQUEST_BODY_BYTES`) maximum request body size before JSON parsing. Fast-path rejects declared `Content-Length` headers > 64 KB (413) or negative/invalid values (400). Also tracks received bytes on the fly for chunked/streamed uploads without buffering in memory.
- **Whitespace & Question Boundaries (`AnalyticsQueryRequest`)**: Enforces input validation via Pydantic field validators. Empty questions, whitespace-only strings, and queries under 3 characters after trimming receive HTTP 422 (`Unprocessable Entity`). Questions over 500 characters are rejected before LLM invocation.
- **In-Memory Rate Limiting (`InMemoryRateLimiter`)**:
  - Sliding-window rate limiter per client IP (default 60 requests/minute via `RATE_LIMIT_REQUESTS_PER_MINUTE`).
  - Automatic periodic and on-demand TTL pruning (`cleanup()`) of expired client entries to eliminate memory leaks under high volumes of unique clients.
  - Returns HTTP 429 (`Too Many Requests`) with an RFC-compliant `Retry-After` header when exceeded.
  - Safe client IP extraction: inspects direct socket host by default. Forwarding headers (`X-Forwarded-For`) are only inspected if `TRUST_PROXY_HEADERS=true` to prevent header spoofing.
- **Safe Sanitized Error Responses**:
  - Upstream LLM timeouts return HTTP 504 (`Gateway Timeout`).
  - Upstream provider or structured parsing failures return HTTP 502 (`Bad Gateway`).
  - Missing server configuration returns HTTP 503 (`Service Unavailable`).
  - Unhandled internal exceptions return HTTP 500 (`Internal Server Error`) with generic, sanitized error messages. Raw stack traces, database URLs, credentials, and internal filesystem paths are never disclosed to HTTP clients.
- **Liveness & Readiness Health Probes**:
  - `GET /health`: Liveness probe verifying the FastAPI server process is responsive.
  - `GET /health/ready`: Readiness probe verifying downstream database connectivity (`SELECT 1` on PostgreSQL via read-only role) and LLM configuration. Returns HTTP 200 when ready or HTTP 503 (`Service Unavailable`) when database connectivity is degraded.

### 2. Structured Logging & Observability (`src/core/logging.py`)
- **Correlation ID Tracking (`RequestIdMiddleware`)**:
  - Validates client `X-Request-ID` headers against `^[a-zA-Z0-9_-]{1,64}$` to reject injection attempts.
  - Generates cryptographically secure UUIDv4 if missing or invalid.
  - Stores correlation ID in asynchronous `contextvars` and attaches it to response headers and log output.
- **Sensitive Data & PII Redactor (`SensitiveDataFilter`)**:
  - Automatically scrubs database passwords (`password=***`, `postgresql://user:***@host`).
  - Automatically scrubs Google Gemini API keys (`AIzaSy...`).
  - Automatically scrubs Bearer tokens (`Bearer [REDACTED_TOKEN]`).
  - Automatically scrubs customer email addresses (`[REDACTED_EMAIL]`).
  - Extended to scrub exception tracebacks (`record.exc_text` and `payload["exception"]`), preventing credential exposure during database or service failures.
  - Result row records are never dumped to application logs at INFO or ERROR level; only column metadata and row counts are recorded.
- **Machine-Parseable Format**: Supports `LOG_FORMAT=json` for JSON output in production log collectors or `LOG_FORMAT=text` for local development.

### 3. Reliability & Failure Handling Matrix (`src/llm/provider.py` & `src/db/session.py`)
- **Bounded Backoff Retries with Jitter**:
  - **Retryable Errors**: Google GenAI HTTP 429 (`ResourceExhausted`), 500 (`Internal`), 502, 503 (`Unavailable`), 504, and transient network disconnects (`httpx.ConnectError`). Retried up to `LLM_MAX_RETRIES` (default 2) with exponential backoff and randomized jitter (`delay = min(base * 2^attempt + jitter, max_delay)`). Respects `Retry-After` metadata from provider headers when present.
  - **No Stacked Retries**: GenAI SDK initialized with `retry_options=HttpRetryOptions(attempts=1)` to disable internal library retries, guaranteeing single-layer bounded retry execution.
  - **Non-Retryable Errors**: HTTP 400 (`InvalidArgument`), 401/403 (`InvalidApiKey` / `PermissionDenied`), 404 (`NotFound`), schema response validation failures, and SQL security guardrail violations are aborted immediately with zero retries.
- **Database Connection Lifecycle**:
  - Explicit rollback and connection closure in `execute_readonly_query()` and `get_analytics_db()` ensures aborted transactions do not leave pooled connections in dirty states.
  - PostgreSQL statement timeouts (3,000 ms) are trapped, mapped to `TIMED_OUT` in `query_audit_logs`, and returned with user-friendly explanations.

### 4. Container & Production Deployment (`Dockerfile` & `docker-compose.prod.yml`)
- **Multi-Stage Dockerfile**:
  - Build stage installs minimal production backend dependencies (`docker/requirements-api.txt`), excluding heavy frontend and development packages (Streamlit, Plotly, PyArrow, Pandas).
  - Runtime stage (`python:3.13-slim`) copies only the virtualenv and app source.
  - Build context optimized via `.dockerignore`.
  - Enforces non-root execution via system user `appuser` (UID 10001, GID 10001).
  - Built-in container health check verifies `GET /health` every 15 seconds.
- **Network Isolation (`docker-compose.prod.yml`)**:
  - PostgreSQL is restricted to the internal Docker bridge network (`backend_net`). Port 5432 is **not** exposed to the host machine in production.
  - Backend API container connects internally via hostname `postgres` and exposes port 8000.
  - API container starts only after PostgreSQL passes health checks (`condition: service_healthy`).
  - Data persisted across container recreations via named volume `sql_analytics_postgres_data`.

---

## Prototype Limitations & Considerations

1. **Schema Scope:** Only queries answerable via the `analytics` schema (`customers`, `products`, `orders`, `order_items`) are supported. Questions requiring unstructured data or other corporate domains return `status: "unsupported"`.
2. **In-Memory Rate Limiting Scope:** The built-in rate limiter operates in-memory using sliding-window timestamp counters per client IP. In multi-worker or multi-replica deployments (e.g. Kubernetes, AWS ECS), rate limiting should be delegated to an edge gateway (Nginx, Kong, Cloudflare) or a distributed Redis store.
3. **Synchronous Execution:** All database operations and API calls are synchronous; heavy long-running analytics jobs under concurrent traffic should be queued in later enterprise phases.
4. **Database Volume Lifecycle:** Docker scripts in `/docker-entrypoint-initdb.d/` only execute on initial volume creation. Never run `docker compose down -v` if business seed data must be retained.
5. **Single-Month Dataset Scope:** The seeded dataset contains order records strictly within March 2025; multi-month trend queries display the single-period result with an explanatory note rather than a multi-month trend chart.
