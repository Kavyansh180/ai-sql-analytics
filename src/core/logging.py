"""Structured logging, request context correlation, and sensitive data redaction."""
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import logging
import re
from typing import Any, Dict, Optional

# Context variable for request correlation across async/sync scopes
request_id_ctx: ContextVar[Optional[str]] = ContextVar("request_id_ctx", default=None)


class SensitiveDataFilter(logging.Filter):
    """
    Logging filter that sanitizes sensitive data before output.
    
    Redacts:
    - Database connection strings containing passwords
    - Explicit password query parameters / credentials
    - Google Gemini / API keys (e.g., AIza...)
    - Raw customer email addresses
    - Bearer tokens / Authorization headers
    """

    PATTERNS = [
        # Database connection strings: postgresql://user:password@host
        (re.compile(r"://([^:]+):([^@]+)@", re.IGNORECASE), r"://\1:***@"),
        # Key-value password patterns: password=secret or "password": "secret"
        (re.compile(r"(password['\"]?\s*[:=]\s*['\"]?)([^'\"\s&]+)(['\"]?)", re.IGNORECASE), r"\1***\3"),
        # Google API keys: AIzaSy... (39 chars)
        (re.compile(r"AIza[0-9A-Za-z-_]{35}"), "[REDACTED_API_KEY]"),
        # General bearer tokens
        (re.compile(r"(Bearer\s+)[A-Za-z0-9_\-\.]{15,}", re.IGNORECASE), r"\1[REDACTED_TOKEN]"),
        # Customer email addresses: user@domain.com
        (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"), "[REDACTED_EMAIL]"),
        # X-Copilot-Secret header or parameter
        (re.compile(r"(x-copilot-secret['\"]?\s*[:=]\s*['\"]?)([^'\"\s&]+)(['\"]?)", re.IGNORECASE), r"\1[REDACTED_SECRET]\3"),
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        # Sanitize message string
        if isinstance(record.msg, str):
            record.msg = self.redact_text(record.msg)

        # Sanitize arguments if any
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: self._redact_value(v) for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(self._redact_value(v) for v in record.args)
            elif isinstance(record.args, list):
                record.args = [self._redact_value(v) for v in record.args]

        # Sanitize formatted exception text if present on record
        if record.exc_text:
            record.exc_text = self.redact_text(record.exc_text)

        return True

    @classmethod
    def redact_text(cls, text: str) -> str:
        """Apply all redaction regex rules to text."""
        sanitized = text
        for pattern, replacement in cls.PATTERNS:
            sanitized = pattern.sub(replacement, sanitized)
        return sanitized

    @classmethod
    def _redact_value(cls, val: Any) -> Any:
        if isinstance(val, str):
            return cls.redact_text(val)
        return val


class StructuredJsonFormatter(logging.Formatter):
    """Formats log records as structured, machine-parseable JSON."""

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        payload: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": message,
        }

        # Request ID from contextvar or record attribute
        req_id = getattr(record, "request_id", None) or request_id_ctx.get()
        if req_id:
            payload["request_id"] = req_id

        # Operational context fields
        for field in ("endpoint", "method", "status_code", "duration_ms", "client_ip", "error_type"):
            val = getattr(record, field, None)
            if val is not None:
                payload[field] = val

        # Handle exception tracebacks if present, ensuring sensitive data is redacted
        if record.exc_info:
            formatted_exc = self.formatException(record.exc_info)
            payload["exception"] = SensitiveDataFilter.redact_text(formatted_exc)

        return json.dumps(payload, default=str)


class TextLogFormatter(logging.Formatter):
    """Human-readable text log formatter with request ID correlation."""

    def format(self, record: logging.LogRecord) -> str:
        req_id = getattr(record, "request_id", None) or request_id_ctx.get()
        req_tag = f" [{req_id}]" if req_id else ""
        record.req_tag = req_tag
        if record.exc_text:
            record.exc_text = SensitiveDataFilter.redact_text(record.exc_text)
        return super().format(record)


def setup_logging(
    log_level: Optional[str] = None,
    log_format: Optional[str] = None,
) -> None:
    """
    Configure application-wide logging with sensitive data redaction and formatting.
    """
    from src.core.config import get_settings

    settings = get_settings()
    level_name = (log_level or settings.LOG_LEVEL or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    format_type = (log_format or settings.LOG_FORMAT or "json").lower()

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Avoid adding duplicate handlers if already initialized
    has_custom_handler = any(
        getattr(h, "_is_sql_analytics_handler", False) for h in root_logger.handlers
    )
    if not has_custom_handler:
        handler = logging.StreamHandler()
        handler._is_sql_analytics_handler = True  # type: ignore[attr-defined]

        # Add redactor filter
        handler.addFilter(SensitiveDataFilter())

        if format_type == "json":
            handler.setFormatter(StructuredJsonFormatter())
        else:
            fmt = "%(asctime)s - %(name)s - %(levelname)s%(req_tag)s - %(message)s"
            handler.setFormatter(TextLogFormatter(fmt=fmt))

        root_logger.addHandler(handler)
