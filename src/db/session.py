"""Database engine and session management for app and analytics roles."""
import time
from contextlib import contextmanager
from typing import Any, Dict, Generator, List, Optional, Tuple
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from src.core.config import get_settings

import os
from sqlalchemy.pool import NullPool

_settings = get_settings()


def is_serverless_environment(serverless_override: Optional[bool] = None) -> bool:
    """
    Check if the current runtime is an ephemeral serverless environment (e.g., AWS Lambda).
    """
    if serverless_override is not None:
        return serverless_override
    settings = get_settings()
    if settings.DB_SERVERLESS_MODE:
        return True
    return bool(os.getenv("AWS_LAMBDA_FUNCTION_NAME") or os.getenv("LAMBDA_TASK_ROOT"))


def get_engine_pool_kwargs(serverless_override: Optional[bool] = None) -> Dict[str, Any]:
    """
    Generate connection pooling configuration.
    
    In serverless environments (AWS Lambda), NullPool is enforced to prevent connection pool
    multiplication across autoscaling concurrent container instances.
    In standard persistent environments (Docker Compose, dev servers), QueuePool is used.
    """
    if is_serverless_environment(serverless_override):
        return {"poolclass": NullPool}
    return {
        "pool_pre_ping": True,
        "pool_size": 5,
        "max_overflow": 10,
    }


def create_app_engine(serverless_override: Optional[bool] = None) -> Engine:
    """Factory creating app_user engine with appropriate connection pooling."""
    settings = get_settings()
    kwargs = get_engine_pool_kwargs(serverless_override)
    return create_engine(
        settings.get_app_database_url(),
        **kwargs,
    )


def create_analytics_engine(serverless_override: Optional[bool] = None) -> Engine:
    """Factory creating analytics_user engine with appropriate connection pooling and statement timeout."""
    settings = get_settings()
    kwargs = get_engine_pool_kwargs(serverless_override)
    return create_engine(
        settings.get_analytics_database_url(),
        connect_args={
            "options": f"-c statement_timeout={settings.ANALYTICS_STATEMENT_TIMEOUT_MS}ms"
        },
        execution_options={
            "postgresql_readonly": True,
        },
        **kwargs,
    )


# ------------------------------------------------------------------------------
# App Engine (app_user: manages app_metadata schema and migrations)
# ------------------------------------------------------------------------------
app_engine: Engine = create_app_engine()

AppSessionLocal = sessionmaker(
    bind=app_engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


@contextmanager
def get_app_db() -> Generator[Session, None, None]:
    """Context manager for application metadata database session."""
    session = AppSessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ------------------------------------------------------------------------------
# Analytics Engine (analytics_user: strictly read-only execution on analytics schema)
# ------------------------------------------------------------------------------
analytics_engine: Engine = create_analytics_engine()

AnalyticsSessionLocal = sessionmaker(
    bind=analytics_engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


@contextmanager
def get_analytics_db() -> Generator[Session, None, None]:
    """Context manager for analytics read-only database session."""
    session = AnalyticsSessionLocal()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def execute_readonly_query(
    sql_query: str,
    params: Optional[Dict[str, Any]] = None,
    max_rows: Optional[int] = None,
    enforce_validation: bool = True,
) -> Tuple[List[str], List[Dict[str, Any]], float]:
    """
    Execute a read-only query using the restricted analytics_user role.
    
    If enforce_validation is True, the query is pre-validated and sanitized
    by SQLSecurityValidator before execution.
    
    Returns:
        (columns, list_of_row_dicts, execution_time_ms)
    """
    from src.security.validator import SQLSecurityValidator

    effective_max_rows = max_rows or _settings.MAX_QUERY_ROW_LIMIT

    if enforce_validation:
        validator = SQLSecurityValidator(max_limit=effective_max_rows)
        validation_result = validator.validate_and_sanitize(sql_query)
        if not validation_result.is_valid:
            raise PermissionError(f"Query security policy violation: {validation_result.error}")
        sql_to_execute = validation_result.sanitized_sql
    else:
        sql_to_execute = sql_query

    start_time = time.perf_counter()
    connection = None
    try:
        connection = analytics_engine.connect()
        result = connection.execute(text(sql_to_execute), params or {})
        columns = list(result.keys())
        rows = [dict(zip(columns, row)) for row in result.fetchmany(effective_max_rows)]
    except Exception:
        if connection is not None:
            try:
                connection.rollback()
            except Exception:
                pass
        raise
    finally:
        if connection is not None:
            connection.close()

    execution_time_ms = (time.perf_counter() - start_time) * 1000.0
    return columns, rows, execution_time_ms
