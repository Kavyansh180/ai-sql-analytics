"""Natural Language to SQL Orchestration Service."""
from datetime import date, datetime
from decimal import Decimal
import logging
import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from src.db.models.metadata import QueryAuditLog
from src.db.session import execute_readonly_query, get_app_db
from src.llm.exceptions import (
    LLMBaseException,
    LLMConfigurationError,
    LLMProviderError,
    LLMResponseValidationError,
    LLMTimeoutError,
)
from src.llm.models import GeneratedSQLResponse, QueryStatus
from src.llm.provider import GeminiLLMProvider, LLMProvider, MockLLMProvider
from src.security.validator import SQLSecurityValidator

logger = logging.getLogger(__name__)


def serialize_database_value(val: Any) -> Any:
    """Convert database types (Decimal, datetime, date) safely into JSON-compatible values."""
    if isinstance(val, Decimal):
        # Convert Decimal to float for JSON compatibility
        return float(val)
    if isinstance(val, (datetime, date)):
        return val.isoformat()
    return val


class QueryExecutionResult(BaseModel):
    """Unified application response model for natural language analytics queries."""
    status: QueryStatus
    question: str
    sql: Optional[str] = None
    explanation: str
    columns: List[str] = Field(default_factory=list)
    rows: List[Dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    execution_time_ms: Optional[float] = None
    clarification_question: Optional[str] = None
    error: Optional[str] = None


class NaturalLanguageSQLService:
    """
    Orchestrates natural language question processing, LLM generation,
    security policy validation, and read-only database execution.
    """

    def __init__(self, provider: Optional[LLMProvider] = None):
        self.provider = provider
        self.validator = SQLSecurityValidator()

    def _get_provider(self) -> LLMProvider:
        """Resolve configured LLM provider, defaulting to GeminiLLMProvider."""
        if self.provider is not None:
            return self.provider
        return GeminiLLMProvider()

    def _log_audit(
        self,
        prompt: str,
        sql: Optional[str],
        status: str,
        row_count: Optional[int] = None,
        execution_time_ms: Optional[float] = None,
        error_message: Optional[str] = None,
    ) -> None:
        """Persist query audit record to app_metadata.query_audit_logs using app_user."""
        try:
            sanitized_error = None
            if error_message:
                # Mask credentials, passwords, or connection URLs if present
                sanitized_error = re.sub(r"://([^:]+):([^@]+)@", r"://\1:***@", str(error_message))
                sanitized_error = re.sub(r"password=([^\s]+)", "password=***", sanitized_error, flags=re.IGNORECASE)
                # Keep error message bounded to prevent log bloating or raw trace exposure
                sanitized_error = sanitized_error[:500]

            with get_app_db() as session:
                audit_entry = QueryAuditLog(
                    natural_language_prompt=prompt[:500],
                    generated_sql=sql or "[NO_SQL_GENERATED]",
                    execution_status=status,
                    row_count=row_count,
                    execution_time_ms=execution_time_ms,
                    error_message=sanitized_error,
                    executed_by_role="analytics_user",
                )
                session.add(audit_entry)
        except Exception as audit_err:
            # Audit log failure should be recorded in server logs without disrupting caller
            logger.warning("Failed to record query audit log: %s", str(audit_err))

    def execute_question(self, question: str) -> QueryExecutionResult:
        """
        Process a user's natural language question end-to-end.
        
        Workflow:
        1. Input validation (length, emptiness).
        2. LLM structured generation via configured provider.
        3. Clarification/Unsupported handling (no SQL executed).
        4. Validation via SQLSecurityValidator.
        5. Read-only execution via execute_readonly_query.
        6. JSON-safe serialization and audit logging.
        """
        if not question or not question.strip():
            return QueryExecutionResult(
                status=QueryStatus.UNSUPPORTED,
                question=question or "",
                explanation="Question cannot be empty.",
                error="Empty question submitted.",
            )

        cleaned_question = question.strip()
        if len(cleaned_question) > 500:
            return QueryExecutionResult(
                status=QueryStatus.UNSUPPORTED,
                question=cleaned_question,
                explanation="Question exceeds the maximum allowed length of 500 characters.",
                error="Question too long.",
            )

        # 1. Invoke LLM Provider
        provider = self._get_provider()
        llm_response: GeneratedSQLResponse = provider.generate_response(cleaned_question)

        # 2. Non-Ready decisions: Return immediately without touching the database
        if llm_response.status != QueryStatus.READY or not llm_response.sql:
            self._log_audit(
                prompt=cleaned_question,
                sql=None,
                status=llm_response.status.value.upper(),
            )
            return QueryExecutionResult(
                status=llm_response.status,
                question=cleaned_question,
                sql=None,
                explanation=llm_response.explanation,
                clarification_question=llm_response.clarification_question,
            )

        # 3. Security Policy Validation (SQLGlot AST)
        validation = self.validator.validate_and_sanitize(llm_response.sql)
        if not validation.is_valid:
            error_msg = f"Security guardrail rejected query: {validation.error}"
            logger.warning("LLM generated invalid query for prompt '%s': %s", cleaned_question, validation.error)
            self._log_audit(
                prompt=cleaned_question,
                sql=llm_response.sql,
                status="BLOCKED_BY_POLICY",
                error_message=error_msg,
            )
            return QueryExecutionResult(
                status=QueryStatus.UNSUPPORTED,
                question=cleaned_question,
                sql=llm_response.sql,
                explanation="The generated query violates database security policies and cannot be executed.",
                error=error_msg,
            )

        sanitized_sql = validation.sanitized_sql

        # 4. Database Execution via read-only analytics role
        try:
            columns, raw_rows, exec_time = execute_readonly_query(
                sanitized_sql,
                enforce_validation=True,
            )
        except Exception as db_err:
            err_str = str(db_err).lower()
            is_timeout = "statement timeout" in err_str or "canceling statement" in err_str
            status_code_name = "TIMED_OUT" if is_timeout else "FAILED"

            logger.error(
                "Error executing query against PostgreSQL (%s): %s",
                status_code_name,
                type(db_err).__name__,
                extra={"error_type": type(db_err).__name__, "is_timeout": is_timeout},
            )
            audit_msg = f"Database execution error: {type(db_err).__name__}"
            self._log_audit(
                prompt=cleaned_question,
                sql=sanitized_sql,
                status=status_code_name,
                error_message=audit_msg,
            )

            if is_timeout:
                explanation = "The analytical query exceeded the maximum execution time limit and was canceled."
                user_error = "Query execution timed out."
            else:
                explanation = "An error occurred while executing the query on the database."
                user_error = "Database query execution failed. Details have been safely logged."

            return QueryExecutionResult(
                status=QueryStatus.UNSUPPORTED,
                question=cleaned_question,
                sql=sanitized_sql,
                explanation=explanation,
                error=user_error,
            )

        # 5. Serialize rows safely for JSON consumption
        serialized_rows: List[Dict[str, Any]] = [
            {k: serialize_database_value(v) for k, v in row.items()}
            for row in raw_rows
        ]

        # 6. Audit Success Log
        self._log_audit(
            prompt=cleaned_question,
            sql=sanitized_sql,
            status="SUCCESS",
            row_count=len(serialized_rows),
            execution_time_ms=exec_time,
        )

        return QueryExecutionResult(
            status=QueryStatus.READY,
            question=cleaned_question,
            sql=sanitized_sql,
            explanation=llm_response.explanation,
            columns=columns,
            rows=serialized_rows,
            row_count=len(serialized_rows),
            execution_time_ms=round(exec_time, 2),
        )
