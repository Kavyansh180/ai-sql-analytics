"""Pydantic schemas for the FastAPI analytics API."""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator

from src.llm.models import QueryStatus


class AnalyticsQueryRequest(BaseModel):
    """Input request model for natural language analytics query."""
    question: str = Field(
        ...,
        min_length=3,
        max_length=500,
        description="Business analytics question in plain English",
        examples=["Which product generated the highest revenue?"],
    )

    @field_validator("question")
    @classmethod
    def validate_question(cls, v: str) -> str:
        """Validate question is non-empty, non-whitespace, and within size boundaries."""
        if not v or not v.strip():
            raise ValueError("Question cannot be empty or contain only whitespace.")
        cleaned = v.strip()
        if len(cleaned) < 3:
            raise ValueError("Question must be at least 3 characters long after trimming whitespace.")
        if len(cleaned) > 500:
            raise ValueError("Question exceeds the maximum allowed length of 500 characters.")
        return cleaned


class AnalyticsQueryResponse(BaseModel):
    """Standard response model for natural language analytics query."""
    status: QueryStatus = Field(description="Query resolution status: ready, clarification_needed, or unsupported")
    question: str = Field(description="Original business question submitted by the user")
    sql: Optional[str] = Field(default=None, description="Sanitized PostgreSQL query that was executed, if status is ready")
    explanation: str = Field(description="Plain-English explanation of the analytical calculation or clarification rationale")
    columns: List[str] = Field(default_factory=list, description="List of column names returned by the query")
    rows: List[Dict[str, Any]] = Field(default_factory=list, description="Array of row data in JSON format")
    row_count: int = Field(default=0, description="Number of rows returned")
    execution_time_ms: Optional[float] = Field(default=None, description="Database execution duration in milliseconds")
    clarification_question: Optional[str] = Field(default=None, description="Clarifying question if status is clarification_needed")
    error: Optional[str] = Field(default=None, description="Sanitized error description if query could not be resolved")
