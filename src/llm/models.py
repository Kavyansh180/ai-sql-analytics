"""Pydantic models for structured LLM response contracts."""
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field, model_validator


class QueryStatus(str, Enum):
    """Execution readiness status for a user question."""
    READY = "ready"
    CLARIFICATION_NEEDED = "clarification_needed"
    UNSUPPORTED = "unsupported"


class GeneratedSQLResponse(BaseModel):
    """
    Structured output contract produced by the LLM provider.
    
    Guarantees that non-ready responses cannot produce executable SQL.
    """
    status: QueryStatus = Field(
        description="Decision status: 'ready' if query can be executed, 'clarification_needed' if ambiguous, or 'unsupported' if outside schema capabilities."
    )
    sql: Optional[str] = Field(
        default=None,
        description="The PostgreSQL SELECT statement if status is 'ready'; otherwise null.",
    )
    explanation: str = Field(
        description="Clear, business-oriented plain-English explanation of the analytical logic or why clarification/unsupported was chosen."
    )
    clarification_question: Optional[str] = Field(
        default=None,
        description="Clarifying question addressed to the user if status is 'clarification_needed'.",
    )

    @model_validator(mode="after")
    def validate_status_and_sql(self) -> "GeneratedSQLResponse":
        """Enforce strict invariant: non-ready states cannot carry executable SQL."""
        if self.status == QueryStatus.READY:
            if not self.sql or not self.sql.strip():
                raise ValueError("When status is 'ready', 'sql' must be a non-empty SELECT query.")
        else:
            # Prevent accidental execution on clarification or unsupported requests
            self.sql = None

        if self.status == QueryStatus.CLARIFICATION_NEEDED and not self.clarification_question:
            # Provide a fallback question based on explanation if omitted
            self.clarification_question = self.explanation

        return self
