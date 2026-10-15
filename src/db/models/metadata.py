"""SQLAlchemy models for application metadata and audit logs (app_metadata schema)."""
from datetime import datetime
from typing import Optional
from sqlalchemy import (
    DateTime,
    Float,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.db.models.base import Base


class QueryAuditLog(Base):
    """Audit log tracking queries, validation decisions, execution time, and status."""
    __tablename__ = "query_audit_logs"
    __table_args__ = {"schema": "app_metadata"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    natural_language_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    generated_sql: Mapped[str] = mapped_column(Text, nullable=False)
    execution_status: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # 'SUCCESS', 'BLOCKED_BY_POLICY', 'FAILED', 'TIMED_OUT'
    row_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    execution_time_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    executed_by_role: Mapped[str] = mapped_column(String(50), server_default="analytics_user", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
